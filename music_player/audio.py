"""One reusable GStreamer playbin, independent of GTK widgets and queue policy."""

import math
from pathlib import Path

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402


class AudioPlayer:
    def __init__(self, on_status, on_event=None):
        Gst.init(None)
        self._on_status = on_status
        self._on_event = on_event or (lambda event, detail=None: None)
        self._closed = False
        self.path = None
        self.state = 'stopped'
        self.seeking = False
        self._target = Gst.State.NULL
        self._last_position = 0.0
        self._last_duration = None
        self._volume = 1.0
        self._muted = False
        self.pipeline = Gst.ElementFactory.make('playbin', 'player')
        if self.pipeline is None:
            raise RuntimeError('GStreamer playbin is unavailable. Install gstreamer1-plugins-base.')
        self._bus = self.pipeline.get_bus()
        self._bus.add_signal_watch()
        self._handler = self._bus.connect('message', self._on_message)

    def _notify(self, text, event, detail=None):
        if event in ('playing', 'paused', 'buffering', 'stopped', 'error', 'ready', 'eos'):
            self.state = event
        self._on_status(text)
        self._on_event(event, detail)

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError('The audio player is closed.')

    def _reset(self):
        self.seeking = False
        self._target = Gst.State.NULL
        self._bus.set_flushing(True)
        self.pipeline.set_state(Gst.State.NULL)
        self._bus.set_flushing(False)

    def load(self, path):
        self._ensure_open()
        candidate = Path(path).expanduser().resolve(strict=True)
        if not candidate.is_file():
            raise ValueError('Select a regular local audio file.')
        self._reset()
        self.path = candidate
        self._last_position, self._last_duration = 0.0, None
        self.pipeline.set_property('uri', candidate.as_uri())
        self._notify(f'Ready: {candidate.name}', 'ready')

    def _set_state(self, state):
        self._ensure_open()
        if self.path is None:
            raise ValueError('Select an audio file first.')
        self._target = state
        if self.pipeline.set_state(state) == Gst.StateChangeReturn.FAILURE:
            self._reset()
            message = 'GStreamer could not start playback. Check the audio output and installed decoders.'
            self._notify(f'Error: {message}', 'error', message)

    def play(self):
        self._set_state(Gst.State.PLAYING)

    def pause(self):
        self._set_state(Gst.State.PAUSED)

    def stop(self):
        self._ensure_open()
        self._reset()
        self._last_position = 0.0
        self._notify('Stopped', 'stopped')

    @property
    def position(self):
        if self._closed:
            return None
        success, value = self.pipeline.query_position(Gst.Format.TIME)
        if success and value >= 0:
            self._last_position = value / Gst.SECOND
        return self._last_position

    @property
    def duration(self):
        if self._closed:
            return None
        success, value = self.pipeline.query_duration(Gst.Format.TIME)
        if success and 0 < value < Gst.CLOCK_TIME_NONE:
            self._last_duration = value / Gst.SECOND
        return self._last_duration

    def seek(self, seconds):
        self._ensure_open()
        duration = self.duration
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0 or \
                (duration is not None and seconds > duration):
            raise ValueError('Seek position must be inside the track.')
        query = Gst.Query.new_seeking(Gst.Format.TIME)
        if self.path is None or not self.pipeline.query(query) or not query.parse_seeking()[1]:
            raise ValueError('This track is not currently seekable.')
        # Suspend tracking before FLUSH can change position or emit state messages.
        self.seeking = True
        self._on_event('seeking', None)
        success = self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                                            int(seconds * Gst.SECOND))
        if not success:
            self.seeking = False
            self._on_event('seeked', None)
            raise ValueError('GStreamer could not seek in this track.')
        self._last_position = float(seconds)

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value):
        self._ensure_open()
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('Volume must be between 0 and 1.')
        self._volume = float(value)
        self.pipeline.set_property('volume', float(value))

    @property
    def muted(self):
        return self._muted

    @muted.setter
    def muted(self, value):
        self._ensure_open()
        self._muted = bool(value)
        self.pipeline.set_property('mute', bool(value))

    def _on_message(self, bus, message):
        if self._closed:
            return
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            self._reset()
            self._notify(f'Error: {error.message}', 'error', error.message)
        elif message.type == Gst.MessageType.EOS:
            self._last_position = self.position
            self._last_duration = self.duration
            self._reset()
            # Reset before notifying: the controller can load the next track here.
            self._notify('Finished', 'eos')
        elif message.type == Gst.MessageType.BUFFERING:
            if self._target == Gst.State.PLAYING:
                if message.parse_buffering() < 100:
                    self.pipeline.set_state(Gst.State.PAUSED)
                    self._notify('Buffering', 'buffering')
                else:
                    self.pipeline.set_state(Gst.State.PLAYING)
        elif message.type == Gst.MessageType.ASYNC_DONE and self.seeking:
            self.seeking = False
            self._on_event('seeked', None)
        elif message.type == Gst.MessageType.STATE_CHANGED and message.src == self.pipeline:
            old, new, pending = message.parse_state_changed()
            if new == Gst.State.PLAYING:
                # PulseAudio/PipeWire may restore previous stream levels at preroll.
                # Keep the application's volume preference and mute intent authoritative.
                self.pipeline.set_property('volume', self._volume)
                self.pipeline.set_property('mute', self._muted)
                self._notify('Playing', 'playing')
            elif new == Gst.State.PAUSED and pending == Gst.State.VOID_PENDING and self._target == Gst.State.PAUSED:
                self._notify('Paused', 'paused')

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._bus.disconnect(self._handler)
        self._bus.set_flushing(True)
        self.pipeline.set_state(Gst.State.NULL)
        self._bus.remove_signal_watch()
