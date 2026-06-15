"""verify_setup.py runs to completion without crashing."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_verify_script_runs():
    result = subprocess.run(
        [sys.executable, "scripts/verify_setup.py"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode in (0, 1), result.stderr
    assert "Setup Status" in result.stdout
