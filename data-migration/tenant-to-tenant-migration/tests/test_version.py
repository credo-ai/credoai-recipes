import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent


def test_version_flag_prints_the_release_version():
    out = subprocess.run([sys.executable, "migrate.py", "--version"], cwd=KIT, capture_output=True, text=True)
    assert out.returncode == 0
    assert out.stdout.strip() == "0.2.1"


def test_changelog_top_entry_is_the_current_version():
    from kit import __version__

    first = next(line for line in (KIT / "CHANGELOG.md").read_text().splitlines() if line.startswith("## "))
    assert first.startswith(f"## {__version__} ")
