"""One GStreamer playbin, independent of GTK widgets."""

from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402


class AudioPlayer:
    def __init__(self, on_status):
        Gst.init(None)
        self._on_status = on_status
        self._closed = False
        self.path = None
        self.pipeline = Gst.ElementFactory.make("playbin", "player")
        if self.pipeline is None:
            raise RuntimeError("GStreamer playbin is unavailable. Install gstreamer1-plugins-base.")
        self._bus = self.pipeline.get_bus()
        self._bus.add_signal_watch()
        self._handler = self._bus.connect("message", self._on_message)

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("The audio player is closed.")

    def _reset(self):
        # Discard messages from the previous stream before reusing the pipeline.
        self._bus.set_flushing(True)
        self.pipeline.set_state(Gst.State.NULL)
        self._bus.set_flushing(False)

    def load(self, path):
        self._ensure_open()
        candidate = Path(path).expanduser().resolve(strict=True)
        if not candidate.is_file():
            raise ValueError("Select a regular local audio file.")
        self._reset()
        self.path = candidate
        self.pipeline.set_property("uri", candidate.as_uri())
        self._on_status(f"Ready: {candidate.name}")

    def _set_state(self, state):
        self._ensure_open()
        if self.path is None:
            raise ValueError("Select an audio file first.")
        if self.pipeline.set_state(state) == Gst.StateChangeReturn.FAILURE:
            self._reset()
            self._on_status("Error: GStreamer could not start playback. Check the audio output and installed decoders.")

    def play(self):
        self._set_state(Gst.State.PLAYING)

    def pause(self):
        self._set_state(Gst.State.PAUSED)

    def stop(self):
        self._ensure_open()
        self._reset()
        self._on_status("Stopped")

    def _on_message(self, bus, message):
        if self._closed:
            return
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            self._reset()
            self._on_status(f"Error: {error.message}")
        elif message.type == Gst.MessageType.EOS:
            self._reset()
            self._on_status("Finished")
        elif message.type == Gst.MessageType.STATE_CHANGED and message.src == self.pipeline:
            old, new, pending = message.parse_state_changed()
            if new == Gst.State.PLAYING:
                self._on_status("Playing")
            elif new == Gst.State.PAUSED and pending == Gst.State.VOID_PENDING:
                self._on_status("Paused")

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._bus.disconnect(self._handler)
        self._bus.set_flushing(True)
        self.pipeline.set_state(Gst.State.NULL)
        self._bus.remove_signal_watch()
