#!/usr/bin/env python3
"""Run this checkout with the interpreter recorded in the desktop entry."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_player.__main__ import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
