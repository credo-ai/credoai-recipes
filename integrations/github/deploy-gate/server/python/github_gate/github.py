"""GitHub commit-status client.

The one GitHub call the service makes: `POST /repos/{repo}/statuses/{sha}`. The workflow posts
`pending` when a PR opens; this service flips it to `success` / `failure` when the Credo AI review
resolves.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("github_gate.github")

GITHUB_API_BASE = "https://api.github.com"

# Branch protection and the notify workflow match this literal string. Renaming it in one place and
# not the others leaves PRs blocked indefinitely.
STATUS_CONTEXT = "deploy-gate/approval"


def set_commit_status(
    repo: str,
    sha: str,
    state: str,
    *,
    token: str,
    description: str = "",
    context: str = STATUS_CONTEXT,
    http: httpx.Client | None = None,
) -> None:
    payload = {"state": state, "context": context, "description": description}
    post = http.post if http is not None else httpx.post
    resp = post(
        f"{GITHUB_API_BASE}/repos/{repo}/statuses/{sha}",
        json=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"GitHub API error {resp.status_code} setting {context}={state} "
            f"on {repo}@{sha[:12]}: {resp.text}"
        )
    logger.info("Set %s=%s on %s@%s", context, state, repo, sha[:12])
