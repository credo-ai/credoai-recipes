import httpx
import pytest
from github_gate.config import Settings
from github_gate.credo_private import CredoPrivateApiError, CredoPrivateClient

SETTINGS = Settings(
    credoai_api_key="key",  # pragma: allowlist secret
    credoai_tenant="acme",
    credoai_api_url="https://api.example.com",
)


def make_client(handler) -> CredoPrivateClient:
    return CredoPrivateClient(
        SETTINGS, http=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_create_webhook_exchanges_token_then_posts_jsonapi():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/auth/exchange":
            return httpx.Response(200, json={"access_token": "tok"})
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(201, json={"data": {"id": "wh-1"}})

    webhook = make_client(handler).create_webhook(
        "gate",
        ["entity_review_status_updated"],
        "https://g.example.com/webhooks/credo",
    )

    assert webhook["id"] == "wh-1"
    assert calls == [("POST", "/auth/exchange"), ("POST", "/api/v2/acme/webhooks")]


def test_create_webhook_raises_on_error():
    def handler(request):
        if request.url.path == "/auth/exchange":
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(403, text="nope")

    with pytest.raises(CredoPrivateApiError, match="403"):
        make_client(handler).create_webhook("d", ["e"], "u")


def test_delete_webhook_treats_404_as_done():
    def handler(request):
        if request.url.path == "/auth/exchange":
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(404)

    make_client(handler).delete_webhook("wh-gone")  # no raise


def test_missing_credentials_fail_fast():
    with pytest.raises(CredoPrivateApiError, match="CREDOAI_API_KEY"):
        CredoPrivateClient(Settings(credoai_api_key="", credoai_tenant=""))
