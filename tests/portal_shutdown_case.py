"""Standalone reproducer for GTK's Wayland portal shutdown crash.

Run explicitly on the affected GNOME/Wayland session, outside pytest, from
the checkout:
    PYTHONPATH=. python3 tests/portal_shutdown_case.py

This is deliberately outside test_*.py: the host GTK 4.22.5 portal backend
segfaults, and CI's isolated Xvfb bus does not exercise the GNOME portal.
"""

import os
import tempfile
from pathlib import Path

from music_player.app import MusicPlayerApplication
from music_player.library import LibraryDatabase
from music_player.mpris import MprisService
from music_player.paths import database_path
from gi.repository import GLib


def main():
    with tempfile.TemporaryDirectory(prefix='musicplayer-portal-qa-') as directory:
        root = Path(directory)
        os.environ['XDG_DATA_HOME'] = str(root / 'data')
        os.environ['XDG_CACHE_HOME'] = str(root / 'cache')
        music = root / 'Music'
        music.mkdir()
        with LibraryDatabase(database_path()) as database:
            database.configure_default_folder(music)
        # No media service or audio is needed to reproduce this GTK failure.
        MprisService.start = lambda *args: None
        app = MusicPlayerApplication()
        app.set_application_id('io.github.OscarGR5.MusicPlayer.PortalShutdownQA')

        def close_during_presentation():
            window = app.get_windows()[0]
            window._open_file(None)
            window.close()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(300, close_during_presentation)
        return app.run(['musicplayer-portal-qa'])


if __name__ == '__main__':
    raise SystemExit(main())
