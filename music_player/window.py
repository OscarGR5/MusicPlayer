"""Temporary Phase 1 playback test interface."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .audio import AudioPlayer


class MusicPlayerWindow(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="MusicPlayer")
        self.set_default_size(520, 200)
        self._chooser = None
        self._status = Gtk.Label(label="Select a local audio file to begin.", wrap=True, xalign=0)
        self._filename = Gtk.Label(label="No file selected", wrap=True, xalign=0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(20)
        self.set_child(box)
        open_button = Gtk.Button(label="Open audio file")
        open_button.connect("clicked", self._open_file)
        box.append(open_button)
        box.append(self._filename)
        controls = Gtk.Box(spacing=8)
        self._controls = []
        self.player = AudioPlayer(self._status.set_text)
        for label, action in (("Play / Resume", self.player.play), ("Pause", self.player.pause), ("Stop", self.player.stop)):
            button = Gtk.Button(label=label, sensitive=False)
            button.connect("clicked", lambda _, action=action: self._perform(action))
            controls.append(button)
            self._controls.append(button)
        box.append(controls)
        box.append(self._status)
        self.connect("close-request", self._on_close)

    def _perform(self, action):
        try:
            action()
        except (OSError, ValueError, RuntimeError) as exc:
            self._status.set_text(f"Error: {exc}")

    def _open_file(self, button):
        if self._chooser is not None:
            self._chooser.show()
            return
        self._chooser = Gtk.FileChooserNative.new(
            "Select local audio", self, Gtk.FileChooserAction.OPEN, "Open", "Cancel"
        )
        self._chooser.connect("response", self._file_response)
        self._chooser.show()

    def _file_response(self, chooser, response):
        try:
            if response == Gtk.ResponseType.ACCEPT:
                file = chooser.get_file()
                path = file.get_path() if file is not None else None
                if path is None:
                    raise ValueError("Only local files are supported.")
                self.player.load(path)
                self._filename.set_text(self.player.path.name)
                for button in self._controls:
                    button.set_sensitive(True)
        except (OSError, ValueError, RuntimeError) as exc:
            self._status.set_text(f"Error: {exc}")
        finally:
            chooser.destroy()
            self._chooser = None

    def close_player(self):
        if self._chooser is not None:
            self._chooser.destroy()
            self._chooser = None
        self.player.close()

    def _on_close(self, window):
        self.close_player()
        return False
