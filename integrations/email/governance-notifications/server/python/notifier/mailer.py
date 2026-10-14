"""Render and send emails.

Templates live in `templates/` as `<name>.txt.j2` (plain text) and
`<name>.html.j2` (HTML, autoescaped). Values that reach a template come from
the SDK, which means from data your users typed in Credo AI, so the HTML is
always escaped and subjects are stripped of line breaks.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .config import Settings

log = logging.getLogger("notifier.mailer")

_TEMPLATES = Path(__file__).parent / "templates"
_MAX_SUBJECT = 150
# The logo is embedded in each email (an inline CID attachment) rather than
# linked, because Gmail and Outlook block remote images by default. HTML
# templates show it with `<img src="cid:credo-ai-logo">`.
_LOGO = _TEMPLATES / "credo-ai-logo.png"
_LOGO_CID = "credo-ai-logo"

_env = Environment(
    loader=FileSystemLoader(_TEMPLATES),
    autoescape=lambda name: bool(name and name.endswith(".html.j2")),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


class MailError(Exception):
    """The SMTP server didn't accept the message; try again later."""


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str


def render(template: str, to: str, subject: str, **context: Any) -> Email:
    return Email(
        to=to,
        subject=_clean_subject(subject),
        text=_env.get_template(f"{template}.txt.j2").render(**context),
        html=_env.get_template(f"{template}.html.j2").render(**context),
    )


def _clean_subject(subject: str) -> str:
    one_line = " ".join(subject.split())
    if len(one_line) > _MAX_SUBJECT:
        one_line = one_line[: _MAX_SUBJECT - 1] + "…"
    return one_line


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._last_send = 0.0

    def send(self, email: Email) -> None:
        msg = EmailMessage()
        msg["From"] = self._settings.smtp_from
        msg["To"] = email.to
        msg["Subject"] = email.subject
        # Some relays don't add these, and mail without them scores as spam.
        msg["Date"] = formatdate(localtime=True)
        from_domain = parseaddr(self._settings.smtp_from)[1].rpartition("@")[2]
        msg["Message-ID"] = make_msgid(domain=from_domain or None)
        msg.set_content(email.text)
        msg.add_alternative(email.html, subtype="html")

        if self._settings.mail_dry_run:
            log.info("MAIL_DRY_RUN: not sending\n%s", msg.as_string())
            return

        # Attached after the dry-run log so the log isn't a wall of base64.
        msg.get_body(("html",)).add_related(
            _LOGO.read_bytes(), maintype="image", subtype="png", cid=f"<{_LOGO_CID}>"
        )

        self._throttle()
        try:
            self._deliver(msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"{type(exc).__name__}: {exc}") from exc

    def _deliver(self, msg: EmailMessage) -> None:
        s = self._settings
        context = ssl.create_default_context()
        if s.smtp_security == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, timeout=30, context=context
            )
        else:
            smtp = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
        with smtp:
            if s.smtp_security == "starttls":
                smtp.starttls(context=context)
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password)
            smtp.send_message(msg)

    def _throttle(self) -> None:
        interval = self._settings.smtp_min_interval_seconds
        if interval <= 0:
            return
        with self._lock:
            wait = self._last_send + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_send = time.monotonic()
