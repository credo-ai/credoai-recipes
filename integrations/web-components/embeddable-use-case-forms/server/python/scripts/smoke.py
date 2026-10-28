"""Read-only preflight / smoke test: verify the API key mints tokens the
frontend can use, and that the configured use case is reachable.

Reads .env, performs the token exchange, then probes each surface
exactly the way the browser will: the platform API with `Authorization:
Bearer` alone, the integration service with Bearer + `X-Tenant` — never the
raw API key (despite its OpenAPI spec listing Bearer as sufficient, the
integration service 401s without the tenant header).

The use case id lives in the frontend's own config (`web/public/
config.js`, `useCaseId`), not the backend's. With no argument it is read from
there; pass one to override.

Every check runs and every failure is reported. Exit code is 1 if any check
failed, so scripts and Claude can gate on it. GET-only; writes nothing.

Usage (from server/python/):
    python scripts/smoke.py [use_case_id]
"""

import asyncio
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.credo_auth import exchange_integration_token  # noqa: E402

CONFIG_JS = Path(__file__).resolve().parents[3] / "web" / "public" / "config.js"


def read_config_js(key: str) -> str:
    """Value of a single-quoted `key: '...'` entry in config.js, or ''."""
    if not CONFIG_JS.exists():
        return ""
    m = re.search(rf"^\s*{key}:\s*'([^']*)'", CONFIG_JS.read_text(), re.MULTILINE)
    return m.group(1) if m else ""


async def main() -> int:
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)
        print(f"FAIL {msg}")

    use_case_id = sys.argv[1] if len(sys.argv) > 1 else read_config_js("useCaseId")
    if not use_case_id:
        fail("no use case id: pass one or set useCaseId in web/public/config.js")

    settings = get_settings()
    try:
        settings.validate_credo_ai_config()
    except Exception as e:
        fail(f".env: {e}")
        return 1
    print(f"tenant={settings.credo_tenant}")
    cfg_tenant = read_config_js("tenant")
    if not cfg_tenant:
        fail("tenant is empty in web/public/config.js")
    elif cfg_tenant != settings.credo_tenant:
        fail(
            f"tenant mismatch: config.js has '{cfg_tenant}', .env has "
            f"'{settings.credo_tenant}'"
        )
    if not read_config_js("user"):
        fail("user is empty in web/public/config.js")
    if not use_case_id:
        return 1

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            integration_token = await exchange_integration_token(settings)
        except Exception as e:
            fail(f"token exchange: {e}")
            return 1
        print("Bearer token: ok")

        # Probe the platform API the way the frontend reads — with the same token
        # the integration service takes.
        response = await client.get(
            f"{settings.credo_private_api_base_url}/api/v2/"
            f"{settings.credo_tenant}/use_cases/{use_case_id}",
            headers={
                "Authorization": f"Bearer {integration_token}",
                "Accept": "application/vnd.api+json",
            },
        )
        if response.status_code != 200:
            fail(f"platform API use case read: HTTP {response.status_code}")
        else:
            attrs = (response.json().get("data") or {}).get("attributes") or {}
            print(
                f"Platform API read (Bearer only): ok — use case '{attrs.get('name')}'"
            )

        # Probe the integration service as the browser must: Bearer +
        # X-Tenant, no X-API-Key.
        integration_headers = {
            "Authorization": f"Bearer {integration_token}",
            "X-Tenant": settings.credo_tenant,
            "Accept": "application/json",
        }
        integration_base = (
            f"{settings.credo_integration_api_base_url}/api/v1/integration"
        )
        response = await client.get(
            f"{integration_base}/use_cases/{use_case_id}",
            headers=integration_headers,
        )
        use_case_body: dict = {}
        if response.status_code != 200:
            fail(f"integration API use case read: HTTP {response.status_code}")
        else:
            print("Integration API read (Bearer + X-Tenant, no X-API-Key): ok")
            use_case_body = response.json()
        questionnaire_ids = (
            use_case_body.get("questionnaire_ids")
            or use_case_body.get("questionnaireIds")
            or []
        )

        # Reads mirroring the TypeScript SDK's declared resources
        # (@credo-ai/sdk) — probed here, raw, before any frontend code
        # is written against them. This is exactly the kind of check that
        # already caught the SDK-declared-but-404ing evidence DELETE route
        # (see app/credo_auth.py); GET-only, non-destructive.
        for label, path in [
            ("use case models", f"/use_cases/{use_case_id}/models"),
            ("use case vendors", f"/use_cases/{use_case_id}/vendors"),
            ("use case evidences", f"/use_cases/{use_case_id}/evidences"),
        ]:
            response = await client.get(
                f"{integration_base}{path}", headers=integration_headers
            )
            status = (
                "ok" if response.status_code == 200 else f"HTTP {response.status_code}"
            )
            print(f"Integration API read ({label}): {status}")
            if response.status_code != 200:
                fail(f"integration API {label}: {status}")

        if questionnaire_ids:
            qid = questionnaire_ids[0]
            response = await client.get(
                f"{integration_base}/questionnaires/{qid}", headers=integration_headers
            )
            status = (
                "ok" if response.status_code == 200 else f"HTTP {response.status_code}"
            )
            print(f"Integration API read (questionnaire {qid}): {status}")
            if response.status_code != 200:
                fail(f"integration API questionnaire {qid}: {status}")
        else:
            fail("use case has no questionnaire_ids (forms and card need one attached)")

    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    print("\nOK: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
