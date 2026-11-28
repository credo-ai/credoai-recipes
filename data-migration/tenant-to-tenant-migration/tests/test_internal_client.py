import pytest

from kit.clients.base import APIError
from kit.clients.internal import InternalClient
from tests.fakes import CONFIG, FakeResponse, FakeSession, no_sleep, token


def client(*responses):
    session = FakeSession(*responses)
    return InternalClient(CONFIG, dry_run=False, session=session, sleep=no_sleep), session


def test_token_exchange_and_tenant_scoped_url():
    c, s = client(token(), FakeResponse(200, {"data": []}))
    c.get_json("entity_types")
    exchange, call = s.calls
    assert exchange["url"] == "https://api.example.com/auth/exchange"
    assert exchange["json"] == {"api_token": "secret-key", "tenant": "tenant-b"}
    assert call["url"] == "https://api.example.com/api/v2/tenant-b/entity_types"


def test_get_all_cursor_follows_meta_after():
    c, s = client(
        token(),
        FakeResponse(200, {"data": [{"id": "a"}], "meta": {"after": "c2"}}),
        FakeResponse(200, {"data": [{"id": "b"}], "meta": {}}),
    )
    assert [d["id"] for d in c.get_all_cursor("users")] == ["a", "b"]
    assert s.calls[2]["params"] == {"page[limit]": 100, "page[after]": "c2"}


def test_get_all_offset_pages_until_short_page():
    c, _ = client(
        token(),
        FakeResponse(200, {"data": [{"id": "a"}, {"id": "b"}]}),
        FakeResponse(200, {"data": [{"id": "c"}]}),
    )
    assert [d["id"] for d in c.get_all_offset("domains", page_size=2)] == ["a", "b", "c"]


def test_get_all_offset_stops_when_endpoint_ignores_page_number():
    page = FakeResponse(200, {"data": [{"id": "a"}, {"id": "b"}]})
    c, _ = client(token(), page, page)
    assert [d["id"] for d in c.get_all_offset("teams", page_size=2)] == ["a", "b"]


def test_get_all_cursor_rejects_unexpected_shape():
    c, _ = client(token(), FakeResponse(200, {"items": []}))
    with pytest.raises(APIError, match="no 'data'"):
        c.get_all_cursor("users")


def test_get_data_returns_the_data_member():
    c, _ = client(token(), FakeResponse(200, {"data": [{"id": "K+1"}]}))
    assert c.get_data("policy_control_bases/K/versions") == [{"id": "K+1"}]


def test_get_data_rejects_unexpected_shape():
    c, _ = client(token(), FakeResponse(200, {"items": []}))
    with pytest.raises(APIError, match="no 'data'"):
        c.get_data("policy_control_bases/K/versions")


def test_delete_sends_a_body_when_given():
    c, s = client(token(), FakeResponse(204))
    c.delete("teams/t1/relationships/members", json={"data": [{"id": "u1", "type": "users"}]})
    assert s.calls[1]["method"] == "DELETE"
    assert s.calls[1]["json"] == {"data": [{"id": "u1", "type": "users"}]}
