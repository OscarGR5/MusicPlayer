"""Keep every MPRIS integration case off the user's desktop session bus."""

import os
import subprocess
import sys
from pathlib import Path


def test_mpris_on_isolated_session_bus():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['dbus-run-session', '--', sys.executable, '-m', 'pytest',
                             'tests/mpris_cases.py', '-q'], cwd=root,
                            env={**os.environ, 'MUSICPLAYER_ISOLATED_DBUS': '1'},
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
