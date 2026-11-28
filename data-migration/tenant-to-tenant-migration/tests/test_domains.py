import logging

import pytest

from kit.clients.base import APIError
from kit.internal import domains
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeResponse, FakeTenant, make_ctx


def domain(did, name, description="About it", custom=True):
    attrs = {"name": name, "description": description, "grouping": None}
    if custom is not None:
        attrs["custom"] = custom
    return {"id": did, "type": "domains", "attributes": attrs}


# Extract


def source_ctx(tmp_path, source_domains):
    client = FakeTenant({"domains": source_domains}, dry_run=True, jsonapi=True)
    return make_ctx(tmp_path, source_internal=client)


def test_extract_keeps_custom_domains_only(tmp_path):
    ctx = source_ctx(
        tmp_path,
        [
            domain("anomaly_detection", "Anomaly Detection", custom=False),
            domain("store_ops", "Store Operations", "Shelves and tills"),
        ],
    )
    assert domains.extract(ctx) == 1
    assert load_extract(tmp_path, "domains") == [
        {"id": "store_ops", "name": "Store Operations", "description": "Shelves and tills"}
    ]


def test_extract_logs_how_many_built_in_domains_were_left_out(tmp_path, caplog):
    ctx = source_ctx(tmp_path, [domain("a", "A", custom=False), domain("b", "B", custom=False), domain("c", "C")])
    with caplog.at_level(logging.INFO, logger="kit.extract"):
        domains.extract(ctx)
    assert "domains: " in caplog.text and "2 built-in" in caplog.text


def test_extract_stops_when_a_domain_has_no_custom_flag(tmp_path):
    # Without the flag a built-in domain can't be told apart; guessing could copy one
    ctx = source_ctx(tmp_path, [domain("store_ops", "Store Operations", custom=None)])
    with pytest.raises(APIError, match="custom"):
        domains.extract(ctx)


# Load


def item(did, name, description="About it"):
    return {"id": did, "name": name, "description": description}


def target_ctx(tmp_path, items, existing=(), dry_run=False, **kwargs):
    save_extract(tmp_path, "domains", items)
    client = FakeTenant({"domains": list(existing)}, jsonapi=True, **kwargs)
    return make_ctx(tmp_path, target_internal=client, dry_run=dry_run)


def test_load_creates_the_domain_with_its_id_inside_attributes(tmp_path, caplog):
    ctx = target_ctx(tmp_path, [item("store_ops", "Store Operations", "Shelves")])
    with caplog.at_level(logging.INFO, logger="kit.load"):
        domains.load(ctx)
    assert ctx.target_internal.posts == [
        (
            "domains",
            {
                "data": {
                    "type": "domains",
                    "attributes": {"id": "store_ops", "name": "Store Operations", "description": "Shelves"},
                }
            },
        )
    ]
    assert ctx.tracker("domains").target_id("store_ops") == "store_ops"
    assert ctx.summary.to_dict()["domains"]["created"] == 1
    assert "Store Operations" in caplog.text


def test_load_tracks_the_id_the_server_returns(tmp_path):
    response = FakeResponse(201, {"data": {"id": "store_ops", "type": "domains", "attributes": {"custom": True}}})
    ctx = target_ctx(tmp_path, [item("store_ops", "Store Operations")], post_responses={"domains": response})
    domains.load(ctx)
    assert ctx.tracker("domains").target_id("store_ops") == "store_ops"


def test_same_id_on_the_target_is_matched_without_a_write(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path, [item("store_ops", "Store Operations")], existing=[domain("store_ops", "Store Operations")]
    )
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        domains.load(ctx)
    assert ctx.target_internal.posts == []
    assert ctx.tracker("domains").find("store_ops")["origin"] == "matched"
    assert ctx.summary.to_dict()["domains"]["matched"] == 1
    assert "differs" not in caplog.text


@pytest.mark.parametrize(
    "existing,field",
    [
        (domain("store_ops", "Shop Operations"), "name"),
        (domain("store_ops", "Store Operations", "Other"), "description"),
    ],
)
def test_matched_domain_with_a_different_name_or_description_is_warned_about(tmp_path, caplog, existing, field):
    ctx = target_ctx(tmp_path, [item("store_ops", "Store Operations")], existing=[existing])
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        domains.load(ctx)
    assert ctx.target_internal.posts == []
    assert "Store Operations" in caplog.text and field in caplog.text and "differs" in caplog.text


def test_same_name_with_a_different_id_is_not_matched(tmp_path):
    ctx = target_ctx(
        tmp_path, [item("store_ops", "Store Operations")], existing=[domain("store_operations", "Store Operations")]
    )
    domains.load(ctx)
    assert len(ctx.target_internal.posts) == 1
    assert ctx.tracker("domains").find("store_ops")["origin"] == "created"


def test_second_run_skips_the_domain(tmp_path):
    ctx = target_ctx(tmp_path, [item("store_ops", "Store Operations")])
    domains.load(ctx)
    domains.load(ctx)
    assert len(ctx.target_internal.posts) == 1
    assert ctx.summary.to_dict()["domains"]["skipped"] == 1


def test_failed_create_is_not_tracked(tmp_path):
    ctx = target_ctx(
        tmp_path,
        [item("store_ops", "Store Operations")],
        post_responses={"domains": FakeResponse(422, {"errors": [{"detail": "bad"}]})},
    )
    domains.load(ctx)
    assert ctx.tracker("domains").find("store_ops") is None
    assert ctx.summary.to_dict()["domains"]["failed"] == 1


def test_dry_run_writes_nothing(tmp_path, caplog):
    ctx = target_ctx(tmp_path, [item("store_ops", "Store Operations")], dry_run=True)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        domains.load(ctx)
    assert ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["domains"]["created"] == 1
    assert "would create" in caplog.text and "Store Operations" in caplog.text
