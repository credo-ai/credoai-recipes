from kit.entities import risk_types
from kit.tracking import load_extract
from tests.fakes import FakeTenant, make_ctx

SOURCE = FakeTenant(
    {
        "risk_types": [
            {"id": "r1", "name": "Privacy", "description": "built in", "custom": False, "hidden": False},
            {"id": "r2", "name": "Model Risk", "description": "tenant made", "custom": True, "hidden": False},
        ]
    },
    dry_run=True,
)


def test_extract_keeps_only_custom_risk_types(tmp_path):
    ctx = make_ctx(tmp_path, source=SOURCE)
    assert risk_types.extract(ctx) == 1
    assert [r["id"] for r in load_extract(tmp_path, "risk_types")] == ["r2"]


def test_load_sends_name_and_description(tmp_path):
    ctx = make_ctx(tmp_path, source=SOURCE)
    risk_types.extract(ctx)
    risk_types.load(ctx)
    assert ctx.target.posts == [("risk_types", {"name": "Model Risk", "description": "tenant made"})]


def test_load_leaves_out_a_missing_description(tmp_path):
    source = FakeTenant(
        {"risk_types": [{"id": "r3", "name": "No Notes", "description": None, "custom": True}]}, dry_run=True
    )
    ctx = make_ctx(tmp_path, source=source)
    risk_types.extract(ctx)
    risk_types.load(ctx)
    assert ctx.target.posts == [("risk_types", {"name": "No Notes"})]
