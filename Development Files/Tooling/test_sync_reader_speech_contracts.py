import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("sync_reader_speech_contracts.py")
ROOT = Path(__file__).resolve().parents[2]
HANDBOOK = ROOT / "Development Files" / "Core" / "HANDBOOK.html"
TOOLING_README = ROOT / "Development Files" / "Tooling" / "README.md"


def test_reader_speech_contracts_are_synchronized():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert SCRIPT.name in HANDBOOK.read_text(encoding="utf-8")
    assert SCRIPT.name in TOOLING_README.read_text(encoding="utf-8")
