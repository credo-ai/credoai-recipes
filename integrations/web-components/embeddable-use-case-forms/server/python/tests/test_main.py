"""Endpoint and token-exchange tests; no network."""

import asyncio

import pytest
from app import credo_auth as app_credo_auth
from app import main as app_main
from app.config import Settings
from app.credo_auth import CredoAuthError, exchange_integration_token
from app.main import app
from credoai.auth import AuthenticationError as CredoSdkAuthError
from fastapi.testclient import TestClient


def make_settings(**overrides) -> Settings:
    values = {
        "credo_api_key": "secret-key",  # pragma: allowlist secret
        "credo_tenant": "acme",
        "credo_private_api_base_url": "http://private.test",
        "credo_integration_api_base_url": "http://integration.test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_main, "get_settings", make_settings)
    return TestClient(app)


def test_issue_tokens_returns_integration_token(client, monkeypatch):
    async def fake_integration(_settings):
        return "bearer-1"

    monkeypatch.setattr(app_main, "exchange_integration_token", fake_integration)
    response = client.post("/api/token")
    assert response.status_code == 200
    assert response.json() == {"integration_api_token": "bearer-1"}


def test_issue_tokens_503_when_unconfigured(monkeypatch):
    monkeypatch.setattr(
        app_main, "get_settings", lambda: make_settings(credo_api_key="")
    )
    response = TestClient(app).post("/api/token")
    assert response.status_code == 503
    assert "CREDO_API_KEY" in response.json()["detail"]


def test_issue_tokens_maps_rejected_credentials_to_502(client, monkeypatch):
    async def rejected(_settings):
        raise CredoAuthError("token exchange failed: 401", status_code=401)

    monkeypatch.setattr(app_main, "exchange_integration_token", rejected)
    response = client.post("/api/token")
    assert response.status_code == 502
    assert "CREDO_API_KEY" in response.json()["detail"]


# --- exchange_integration_token: pycredoai's create_auth_handler_async ---
#
# This no longer takes an httpx transport at all — it goes through
# credoai.auth.create_auth_handler_async internally, so these tests mock at
# that boundary instead of re-testing the SDK's own wire behavior.


def test_exchange_integration_token_calls_sdk_with_resolved_config(monkeypatch):
    captured = {}

    async def fake_create_auth_handler_async(**kwargs):
        captured.update(kwargs)

        class FakeAuthHandler:
            _access_token = "bearer-abc"

        return FakeAuthHandler()

    monkeypatch.setattr(
        app_credo_auth, "create_auth_handler_async", fake_create_auth_handler_async
    )
    token = asyncio.run(exchange_integration_token(make_settings()))
    assert token == "bearer-abc"
    assert captured == {
        "base_url": "http://integration.test/api/v1/integration",
        "auth_type": "api_key",
        "token": "secret-key",
        "tenant": "acme",
        "timeout": 30.0,
    }


def test_exchange_integration_token_maps_sdk_auth_error(monkeypatch):
    async def fake_create_auth_handler_async(**_kwargs):
        raise CredoSdkAuthError("Invalid API key for tenant 'acme'", status_code=401)

    monkeypatch.setattr(
        app_credo_auth, "create_auth_handler_async", fake_create_auth_handler_async
    )
    with pytest.raises(CredoAuthError) as excinfo:
        asyncio.run(exchange_integration_token(make_settings()))
    assert excinfo.value.status_code == 401


def test_allowed_origins_parsed_from_comma_separated_setting():
    settings = make_settings(allowed_origins=" https://a.test ,https://b.test,, ")
    assert settings.allowed_origin_list == ["https://a.test", "https://b.test"]


def test_cors_allows_default_dev_origin_only(client):
    allowed = "http://localhost:5173"
    ok = client.options(
        "/api/token",
        headers={"Origin": allowed, "Access-Control-Request-Method": "POST"},
    )
    assert ok.headers.get("access-control-allow-origin") == allowed
    blocked = client.options(
        "/api/token",
        headers={
            "Origin": "https://evil.test",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in blocked.headers
