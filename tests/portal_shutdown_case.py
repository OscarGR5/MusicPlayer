"""Standalone application regression for GTK's Wayland chooser shutdown crash.

Run explicitly on the affected GNOME/Wayland session, outside pytest, from
the checkout:
    PYTHONPATH=. python3 tests/portal_shutdown_case.py

Use --timing mapped for a close after the application-owned chooser is mapped,
--requests 30 for rapid repeated requests, or --quit for application shutdown.
The original immediate case must pass with the compatibility fallback. The
GTK-only native case in gtk_chooser_shutdown_case.py still exposes the GTK bug.
"""

import argparse
import os
import tempfile
import time
from pathlib import Path

from music_player.app import MusicPlayerApplication
from music_player.library import LibraryDatabase
from music_player.mpris import MprisService
from music_player.paths import database_path
from gi.repository import GLib, Gtk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timing', choices=('immediate', 'mapped'), default='immediate')
    parser.add_argument('--requests', type=int, default=1)
    parser.add_argument('--quit', action='store_true', help='Quit through application shutdown')
    args = parser.parse_args()
    if args.requests < 1:
        parser.error('--requests must be positive')
    with tempfile.TemporaryDirectory(prefix='musicplayer-portal-qa-') as directory:
        root = Path(directory)
        os.environ['XDG_DATA_HOME'] = str(root / 'data')
        os.environ['XDG_CACHE_HOME'] = str(root / 'cache')
        os.environ['XDG_CONFIG_HOME'] = str(root / 'config')
        os.environ['XDG_STATE_HOME'] = str(root / 'state')
        music = root / 'Music'
        music.mkdir()
        with LibraryDatabase(database_path()) as database:
            database.configure_default_folder(music)
        # No media service or audio is needed to reproduce this GTK failure.
        MprisService.start = lambda *args: None
        app = MusicPlayerApplication()
        app.set_application_id('io.github.OscarGR5.MusicPlayer.PortalShutdownQA')
        failures = []
        opened = []

        def checked(callback):
            def run():
                try:
                    return callback()
                except Exception as exc:
                    failures.append(repr(exc))
                    app.quit()
                    return GLib.SOURCE_REMOVE
            return run

        @checked
        def close_during_presentation():
            window = app.get_windows()[0]
            window._open_file(None)
            chooser = window._chooser
            opened.append((window, chooser))
            for _ in range(args.requests - 1):
                window._open_file(None)
                assert window._chooser is chooser

            def close():
                app.quit() if args.quit else window.close()
                return GLib.SOURCE_REMOVE

            if args.timing == 'immediate':
                return close()
            deadline = time.monotonic() + 5

            @checked
            def when_mapped():
                if chooser.get_mapped():
                    return close()
                if time.monotonic() > deadline:
                    failures.append('Chooser did not map')
                    app.quit()
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE

            GLib.timeout_add(10, when_mapped)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(300, close_during_presentation)
        result = app.run(['musicplayer-portal-qa'])
        assert opened
        for window, chooser in opened:
            assert window._chooser is None
            assert not chooser.get_visible()
        assert not any(isinstance(window, Gtk.FileChooserDialog) for window in Gtk.Window.get_toplevels())
        if failures:
            raise RuntimeError('; '.join(failures))
        return result


if __name__ == '__main__':
    raise SystemExit(main())
