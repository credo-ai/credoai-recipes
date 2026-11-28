import pytest

from kit.run_meta import RunMetaMismatch, check_run_meta
from tests.fakes import FakeTenant, make_ctx


def test_extract_records_source_and_live_load_records_target(tmp_path):
    ctx = make_ctx(tmp_path)
    check_run_meta(ctx, "extract", live=False)
    check_run_meta(ctx, "load", live=True)
    import json

    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert meta == {"source": "source-tenant (https://fake)", "target": "target-tenant (https://fake)"}


def test_dry_run_load_does_not_record_target(tmp_path):
    ctx = make_ctx(tmp_path, dry_run=True)
    check_run_meta(ctx, "extract", live=False)
    check_run_meta(ctx, "load", live=False)
    import json

    assert "target" not in json.loads((tmp_path / "run_meta.json").read_text())


def test_different_target_is_refused(tmp_path):
    check_run_meta(make_ctx(tmp_path), "load", live=True)
    other = make_ctx(tmp_path, target=FakeTenant(tenant="other-tenant"))
    with pytest.raises(RunMetaMismatch, match="other-tenant"):
        check_run_meta(other, "load", live=False)
