"""User data locations, following the XDG base directory convention."""

import os
from pathlib import Path


def _xdg_path(variable, fallback):
    value = Path(os.environ.get(variable, ""))
    return (value if value.is_absolute() else Path.home() / fallback) / "musicplayer"


def database_path():
    return _xdg_path("XDG_DATA_HOME", ".local/share") / "library.sqlite3"


def thumbnail_directory():
    return _xdg_path("XDG_CACHE_HOME", ".cache") / "covers"
