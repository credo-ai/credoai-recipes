"""config.js parsing used by scripts/smoke.py; no network."""

import importlib.util
from pathlib import Path

SMOKE = Path(__file__).resolve().parent.parent / "scripts" / "smoke.py"


def load_smoke():
    spec = importlib.util.spec_from_file_location("smoke", SMOKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_read_config_js(tmp_path, monkeypatch):
    smoke = load_smoke()
    cfg = tmp_path / "config.js"
    cfg.write_text("window.credoConfig = {\n  tenant: 'acme',\n  useCaseId: '',\n};\n")
    monkeypatch.setattr(smoke, "CONFIG_JS", cfg)
    assert smoke.read_config_js("tenant") == "acme"
    assert smoke.read_config_js("useCaseId") == ""
    assert smoke.read_config_js("missing") == ""


def test_read_config_js_absent_file(tmp_path, monkeypatch):
    smoke = load_smoke()
    monkeypatch.setattr(smoke, "CONFIG_JS", tmp_path / "nope.js")
    assert smoke.read_config_js("tenant") == ""
