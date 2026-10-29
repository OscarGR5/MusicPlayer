"""GTK application lifecycle."""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from .window import MusicPlayerWindow
from .mpris import DESKTOP_ENTRY, MprisService


class MusicPlayerApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=DESKTOP_ENTRY)
        self.startup_failed = False
        self.mpris = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        # This process-wide GTK preference also covers application-owned dialogs.
        Gtk.Settings.get_default().set_property('gtk-application-prefer-dark-theme', True)
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(Path(__file__).parent / 'assets'))
        Gtk.Window.set_default_icon_name(DESKTOP_ENTRY)

    def do_activate(self):
        windows = self.get_windows()
        window = windows[0] if windows else None
        if window is None:
            try:
                window = MusicPlayerWindow(self)
            except RuntimeError as exc:
                print(f"MusicPlayer could not start: {exc}", file=sys.stderr)
                self.startup_failed = True
                self.quit()
                return
        window.present()
        if self.mpris is None:
            self.mpris = MprisService(window.controller, self.activate, self.quit,
                                      lambda text: print(text, file=sys.stderr))
            self.mpris.start(self.get_dbus_connection())

    def do_shutdown(self):
        if self.mpris is not None:
            self.mpris.close()
        for window in self.get_windows():
            if hasattr(window, "player"):
                window.close_player()
        Gtk.Application.do_shutdown(self)
