import pytest

from kit.clients.base import APIError
from tests.fakes import FakeResponse, FakeTenant, make_ctx


def test_fake_get_data_raises_for_an_unknown_path():
    with pytest.raises(APIError, match="404"):
        FakeTenant().get_data("missing")


def test_make_ctx_gives_supplied_target_fakes_the_context_dry_run(tmp_path):
    ctx = make_ctx(tmp_path, target=FakeTenant(), target_internal=FakeTenant(), dry_run=True)
    assert ctx.target.dry_run and ctx.target_internal.dry_run


def test_get_json_serves_bodies_and_records_params():
    fake = FakeTenant(json_bodies={"q/K/spec": {"sections": []}, "q/K/versions": [{"version": 1}]})
    assert fake.get_json("q/K/spec", {"version": 1}) == {"sections": []}
    assert fake.get_json("q/K/versions") == [{"version": 1}]
    assert fake.gets == [("q/K/spec", {"version": 1}), ("q/K/versions", None)]
    assert fake.get_json("other") == {}  # unchanged default for unknown paths


def test_get_json_body_can_be_an_error():
    fake = FakeTenant(json_bodies={"q/K/spec": APIError("GET q/K/spec failed: 500", 500)})
    with pytest.raises(APIError):
        fake.get_json("q/K/spec")


def test_get_json_returns_a_copy():
    fake = FakeTenant(json_bodies={"q/K/spec": {"sections": [{"id": "s1"}]}})
    fake.get_json("q/K/spec")["sections"].append({"id": "s2"})
    assert fake.get_json("q/K/spec") == {"sections": [{"id": "s1"}]}


def test_patch_is_recorded_and_blocked_in_dry_run():
    fake = FakeTenant()
    assert fake.patch("q/K", {"show_on_create": False}).status_code == 200
    assert fake.patches == [("q/K", {"show_on_create": False})]
    dry = FakeTenant(dry_run=True)
    assert dry.patch("q/K", {}) is None and dry.patches == []


def test_patch_response_can_be_set():
    fake = FakeTenant(patch_response=FakeResponse(500, text="boom"))
    assert fake.patch("q/K", {}).status_code == 500


def test_fake_delete_records_a_body():
    tenant = FakeTenant()
    tenant.delete("teams/t1/relationships/members", json={"data": [{"id": "u1", "type": "users"}]})
    assert tenant.deletes == ["teams/t1/relationships/members"]
    assert tenant.delete_bodies == [{"data": [{"id": "u1", "type": "users"}]}]
