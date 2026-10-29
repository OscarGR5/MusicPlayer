"""GTK application lifecycle."""

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .window import MusicPlayerWindow


class MusicPlayerApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.OscarGR5.MusicPlayer")
        self.startup_failed = False

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            try:
                window = MusicPlayerWindow(self)
            except RuntimeError as exc:
                print(f"MusicPlayer could not start: {exc}", file=sys.stderr)
                self.startup_failed = True
                self.quit()
                return
        window.present()

    def do_shutdown(self):
        for window in self.get_windows():
            if hasattr(window, "player"):
                window.close_player()
        Gtk.Application.do_shutdown(self)
