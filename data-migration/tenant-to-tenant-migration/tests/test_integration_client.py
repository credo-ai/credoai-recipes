import pytest
import requests

from kit.clients.base import APIError
from kit.clients.integration import IntegrationClient
from tests.fakes import CONFIG, FakeResponse, FakeSession, no_sleep, token


def client(*responses, dry_run=False):
    session = FakeSession(*responses)
    return IntegrationClient(CONFIG, dry_run=dry_run, session=session, sleep=no_sleep), session


def test_token_exchange_and_x_tenant_on_every_call():
    c, s = client(token(), FakeResponse(200, {"items": []}))
    c.get_json("risk_types")
    exchange, call = s.calls
    assert exchange["url"] == "https://api.example.com/api/v1/integration/auth/token"
    assert exchange["headers"] == {"X-API-Key": "secret-key", "X-Tenant": "tenant-b"}
    assert call["url"] == "https://api.example.com/api/v1/integration/risk_types"
    assert call["headers"] == {"Authorization": "Bearer tok-1", "X-Tenant": "tenant-b"}


def test_get_all_follows_cursor():
    c, s = client(
        token(),
        FakeResponse(200, {"items": [{"id": 1}], "pagination": {"has_more": True, "next_cursor": "c2"}}),
        FakeResponse(200, {"items": [{"id": 2}], "pagination": {"has_more": False, "next_cursor": None}}),
    )
    assert c.get_all("models") == [{"id": 1}, {"id": 2}]
    assert s.calls[2]["params"] == {"page_limit": 100, "page_after": "c2"}


def test_get_all_accepts_bare_list():
    c, _ = client(token(), FakeResponse(200, [{"id": "a"}]))
    assert c.get_all("triggers/t1/actions") == [{"id": "a"}]


def test_401_refreshes_token_once():
    c, s = client(token("old"), FakeResponse(401), token("new"), FakeResponse(200, {"items": []}))
    c.get_json("risk_types")
    assert s.calls[-1]["headers"]["Authorization"] == "Bearer new"


def test_read_retries_5xx_then_succeeds():
    c, _ = client(token(), FakeResponse(502), FakeResponse(200, {"ok": True}))
    assert c.get_json("ping") == {"ok": True}


def test_read_gives_up_after_three_tries():
    c, _ = client(token(), FakeResponse(500), FakeResponse(500), FakeResponse(500))
    with pytest.raises(APIError) as exc:
        c.get_json("ping")
    assert exc.value.status_code == 500


def test_read_retries_dropped_connection():
    c, _ = client(token(), requests.ConnectionError("reset"), FakeResponse(200, {"ok": True}))
    assert c.get_json("ping") == {"ok": True}


def test_write_is_not_retried_on_5xx():
    c, s = client(token(), FakeResponse(500))
    assert c.post("risk_types", {"name": "x"}).status_code == 500
    assert len(s.calls) == 2


def test_write_is_not_retried_on_dropped_connection():
    c, _ = client(token(), requests.ConnectionError("reset"))
    with pytest.raises(APIError, match="by name or key"):
        c.post("risk_types", {"name": "x"})


def test_write_retries_429():
    c, _ = client(token(), FakeResponse(429), FakeResponse(201, {"id": "n1"}))
    assert c.post("risk_types", {"name": "x"}).status_code == 201


def test_dry_run_blocks_writes_without_any_http_call():
    c, s = client(dry_run=True)
    assert c.post("risk_types", {"name": "x"}) is None
    assert s.calls == []


def test_read_only_client_refuses_writes_loudly():
    c = IntegrationClient(CONFIG, dry_run=True, read_only=True, session=FakeSession(), sleep=no_sleep)
    with pytest.raises(APIError, match="read-only"):
        c.post("risk_types", {"name": "x"})


def test_second_401_stops_instead_of_failing_item_by_item():
    c, _ = client(token(), FakeResponse(401), token("new"), FakeResponse(401))
    with pytest.raises(APIError, match="still unauthorized"):
        c.post("risk_types", {"name": "x"})


def test_429_honors_retry_after():
    waits = []
    session = FakeSession(token(), FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, {"ok": 1}))
    c = IntegrationClient(CONFIG, dry_run=False, session=session, sleep=waits.append)
    c.get_json("ping")
    assert waits == [7]


def test_non_json_success_body_is_an_api_error():
    c, _ = client(token(), FakeResponse(200, text="<html>proxy</html>"))
    with pytest.raises(APIError, match="not JSON"):
        c.get_json("ping")


def test_token_response_without_access_token_is_an_api_error():
    c, _ = client(FakeResponse(200, {"token": "x"}))
    with pytest.raises(APIError, match="sign-in"):
        c.get_json("ping")


def test_get_all_rejects_unexpected_shape():
    c, _ = client(token(), FakeResponse(200, {"data": [{"id": 1}]}))
    with pytest.raises(APIError, match="no 'items'"):
        c.get_all("models")


def test_get_all_stops_on_repeated_cursor():
    page = FakeResponse(200, {"items": [{"id": 1}], "pagination": {"has_more": True, "next_cursor": "same"}})
    c, _ = client(token(), page, page)
    with pytest.raises(APIError, match="repeated"):
        c.get_all("models")


# --- answer, custom field and evidence values never reach the log ---


@pytest.mark.parametrize(
    "method,path,body",
    [
        (
            "POST",
            "use_cases/u1/questionnaire_sections/s1/evidences",
            {"question_id": "q1", "data": {"value": "SECRET"}},
        ),
        ("POST", "use_cases/u1/controls/c1/evidences", {"control_requirement_id": "h1", "data": {"value": "SECRET"}}),
        ("PATCH", "use_cases/u1", {"custom_fields": [{"name": "Tier", "value": "SECRET"}]}),
        ("PATCH", "models/m1", {"custom_fields": [{"name": "Tier", "value": "SECRET"}]}),
    ],
)
@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry"])
def test_sensitive_writes_log_method_path_status_and_size_only(caplog, method, path, body, dry_run):
    import logging

    c, _ = client(token(), FakeResponse(201, {"data": {"value": "SECRET"}}), dry_run=dry_run)
    with caplog.at_level(logging.DEBUG, logger="kit.http"):
        c.request(method, path, json=body)
    assert "SECRET" not in caplog.text
    assert f"{method} {path}" in caplog.text
    if not dry_run:
        assert "201" in caplog.text and "bytes" in caplog.text


@pytest.mark.parametrize("path", ["use_cases/u1/evidences", "models/m1/custom_fields", "use_cases/u1", "use_cases"])
def test_sensitive_reads_do_not_log_the_response(caplog, path):
    import logging

    body = {"data": [{"attributes": {"data": {"value": "SECRET"}}}], "custom_fields": [{"value": "SECRET"}]}
    if path in ("use_cases/u1", "use_cases"):
        body = {"items": [{"id": "u1", "custom_fields": [{"name": "Tier", "value": "SECRET"}]}]}
    c, _ = client(token(), FakeResponse(200, body))
    with caplog.at_level(logging.DEBUG, logger="kit.http"):
        c.get_json(path)
    assert "SECRET" not in caplog.text
    assert f"GET {path}" in caplog.text and "200" in caplog.text


def test_other_requests_still_log_their_bodies(caplog):
    import logging

    c, _ = client(token(), FakeResponse(201, {"id": "r1", "name": "Privacy"}))
    with caplog.at_level(logging.DEBUG, logger="kit.http"):
        c.post("risk_types", {"name": "Privacy"})
    assert "body={'name': 'Privacy'}" in caplog.text and '"id": "r1"' in caplog.text


@pytest.mark.parametrize("path", ["use_cases/u1/controls", "models/m1/controls/c1"])
def test_entity_control_reads_do_not_log_the_response(caplog, path):
    import logging

    body = {"data": [{"attributes": {"compliances": [{"data": {"value": "SECRET"}}]}}]}
    c, _ = client(token(), FakeResponse(200, body))
    with caplog.at_level(logging.DEBUG, logger="kit.http"):
        c.get_json(path)
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize("status", [404, 500])
def test_failed_sensitive_read_keeps_the_body_out_of_the_error(status):
    from kit.clients.base import MAX_TRIES

    errors = [FakeResponse(status, {"error": "answer SECRET is invalid"}) for _ in range(MAX_TRIES)]
    c, _ = client(token(), *errors)
    with pytest.raises(APIError) as e:
        c.get_json("use_cases/u1/evidences")
    assert "SECRET" not in e.value.body and "SECRET" not in str(e.value)


def test_failed_other_read_keeps_its_body_for_the_error():
    c, _ = client(token(), FakeResponse(404, {"error": "no such model"}))
    with pytest.raises(APIError) as e:
        c.get_json("models/m1")
    assert "no such model" in e.value.body
