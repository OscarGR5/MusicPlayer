"""Playback policy: immutable queue snapshots, navigation, modes, and history."""

import os
import random
import time
from pathlib import Path
from mutagen import MutagenError

from .audio import AudioPlayer
from .artwork import cache_artwork
from .history import ListeningTracker, utc_now
from .library import LibraryDatabase, Song
from .metadata import read_metadata
from .paths import thumbnail_directory


class PlaybackController:
    def __init__(self, database, on_change, *, rng=None, audio_factory=AudioPlayer,
                 clock=time.monotonic, wall_clock=utc_now, zone=None):
        self.database = database
        self.on_change = on_change
        self.history = ListeningTracker(database, clock, wall_clock, zone)
        try:
            self.audio = audio_factory(lambda text: None, self._audio_event)
        except Exception:
            self.history.close()
            raise
        self.audio.volume = self.history.volume
        self.rng = rng or random.Random()
        self.queue = ()
        self.current_index = None
        self.current = None
        self.order = []
        self.order_position = -1
        self.shuffle = False
        self.repeat = 'off'
        self.state = 'stopped'
        self.message = 'Select a song to play.'
        self._played = []
        self._history_position = -1
        self._cycle_seen = set()
        self._desired_playing = False
        self._transitioning = False
        self._closed = False
        self._history_problem = None

    @property
    def current_song_id(self):
        return self.current.id if self.current else None

    @property
    def position(self):
        return self.audio.position or 0

    @property
    def duration(self):
        value = self.audio.duration
        return value or (self.current.duration if self.current and self.current.duration > 0 else None)

    def _notify(self, event):
        self.on_change(event)

    def _sample(self):
        state = self.state if not self.audio.seeking else 'seeking'
        self.history.sample(state, self.audio.position, self.duration)

    def activate(self, songs, position=0, *, playing=True):
        snapshot = tuple(songs)
        if not snapshot:
            self.stop()
            self.queue = ()
            self.current = None
            self.current_index = None
            self._notify('track')
            return
        if not 0 <= position < len(snapshot):
            raise ValueError('Invalid queue position.')
        if not self._available(snapshot[position]):
            raise FileNotFoundError(f'Audio file unavailable: {snapshot[position].path}')
        self.queue = snapshot
        self._played, self._history_position = [], -1
        self._cycle_seen = set()
        self.order = list(range(len(snapshot)))
        if self.shuffle:
            self.order.remove(position)
            self.rng.shuffle(self.order)
            self.order.insert(0, position)
        if not self._switch(position, playing):
            message = self.message
            self.stop()
            self.current, self.current_index = None, None
            self.message = message
            self._notify('track')

    @staticmethod
    def _available(song):
        return song.available and Path(song.path).is_file() and os.access(song.path, os.R_OK)

    def _switch(self, index, playing, *, record=True, reason='switch'):
        song = self.queue[index]
        if not self._available(song):
            return False
        self._sample()
        self.history.suspend()
        self._transitioning = True
        try:
            self.audio.load(song.path)
        except (OSError, ValueError, RuntimeError) as exc:
            self.message = f'Error: {exc}'
            self._notify('message')
            return False
        finally:
            self._transitioning = False
        self.history.finish(reason)
        self.current_index, self.current = index, song
        self.order_position = self.order.index(index) if index in self.order else -1
        self._cycle_seen.add(index)
        if record:
            del self._played[self._history_position + 1:]
            self._played.append(index)
            self._history_position = len(self._played) - 1
        self.history.start(song)
        self.state, self.message = 'loading', 'Loading'
        self._desired_playing = playing
        self._notify('track')
        if playing:
            self.audio.play()
        else:
            self.audio.pause()
        return True

    def play_pause(self):
        if self.state in ('playing', 'loading', 'buffering'):
            self.pause()
        elif self.current is not None and self.state in ('stopped', 'error', 'eos'):
            if not self._switch(self.current_index, True, record=False):
                self.message = f'Error: Audio file unavailable: {self.current.path}'
                self._notify('message')
        elif self.audio.path is not None:
            self._desired_playing = True
            self.audio.play()

    def pause(self):
        if self.audio.path is not None:
            self._sample()
            self.history.suspend()
            self._desired_playing = False
            self.audio.pause()

    def stop(self):
        if self._closed:
            return
        self._sample()
        self.history.finish('stop')
        self._desired_playing = False
        self.audio.stop()

    def next(self, natural=False):
        if not self.queue:
            return
        playing = True if natural else self._desired_playing
        if not natural and self._history_position + 1 < len(self._played):
            for cursor in range(self._history_position + 1, len(self._played)):
                if self._switch(self._played[cursor], playing, record=False):
                    self._history_position = cursor
                    return
        candidates = self.order[self.order_position + 1:]
        if self.repeat == 'all':
            # Try the rest of this cycle before generating a fresh order.
            for index in candidates:
                if self._switch(index, playing):
                    return
            self.order = list(range(len(self.queue)))
            if self.shuffle:
                self.rng.shuffle(self.order)
                if len(self.order) > 1 and self.order[0] == self.current_index:
                    self.order[0], self.order[1] = self.order[1], self.order[0]
            self._cycle_seen.clear()
            candidates = self.order
        for index in candidates:
            if self._switch(index, playing):
                return
        self.stop()
        self.message = 'Finished' if natural else 'End of queue'
        self._notify('state')

    def previous(self):
        if self.current is None:
            return
        if self.position > 3:
            self.seek(0)
            return
        for cursor in range(self._history_position - 1, -1, -1):
            if self._switch(self._played[cursor], self._desired_playing, record=False):
                self._history_position = cursor
                return
        # Starting in the middle of an unshuffled list still has a preceding song.
        if not self.shuffle:
            for index in range(self.current_index - 1, -1, -1):
                if self._switch(index, self._desired_playing, record=False):
                    self._played.insert(0, index)
                    self._history_position = 0
                    return
        self.seek(0)

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        if self.current_index is not None:
            if self.shuffle:
                remaining = [index for index in range(len(self.queue)) if index not in self._cycle_seen]
                self.rng.shuffle(remaining)
                self.order = [self.current_index] + remaining
                self.order_position = 0
            else:
                self.order = list(range(len(self.queue)))
                self.order_position = self.current_index
        self._notify('modes')

    def cycle_repeat(self):
        self.repeat = {'off': 'all', 'all': 'one', 'one': 'off'}[self.repeat]
        self._notify('modes')

    def seek(self, seconds):
        self._sample()
        self.history.suspend()
        self.audio.seek(seconds)
        self._notify('progress')

    def set_volume(self, volume):
        self.audio.volume = volume
        self.history.set_volume(volume)
        self._notify('volume')

    def toggle_mute(self):
        self.audio.muted = not self.audio.muted
        self._notify('volume')

    def open_file(self, path):
        path = Path(path).expanduser().resolve(strict=True)
        with LibraryDatabase(self.database) as db:
            song = db.song_at_path(path)
        if song is None:
            try:
                metadata = read_metadata(path)
            except (MutagenError, OSError, ValueError) as exc:
                raise ValueError(f'Could not read audio file: {exc}') from exc
            stat = path.stat()
            cover = cache_artwork(metadata.artwork, thumbnail_directory())
            song = Song(None, 0, str(path), metadata.title, metadata.artist, metadata.album,
                        metadata.duration, stat.st_mtime_ns, stat.st_size, cover)
        elif not song.available:
            from dataclasses import replace
            song = replace(song, available=True)
        self.activate([song])

    def _audio_event(self, event, detail=None):
        if self._closed or self._transitioning:
            return
        if event == 'seeking':
            self.history.suspend()
            return
        if event == 'seeked':
            self._sample()
            self._notify('progress')
            return
        if event == 'eos':
            self._sample()
            self.history.finish('eos')
            self.state = 'eos'
            if self.current is not None and self.repeat == 'one':
                if not self._switch(self.current_index, True, record=False):
                    self.stop()
                    self.message = 'Finished: current file is unavailable'
                    self._notify('state')
            elif self.queue:
                self.next(natural=True)
            else:
                self.state, self.message = 'stopped', 'Finished'
                self._notify('state')
            return
        if event in ('paused', 'buffering', 'ready'):
            self.history.suspend()
        if event == 'error':
            self.history.finish('error')
            self._desired_playing = False
        elif event == 'stopped':
            self.history.finish('stop')
        elif event == 'playing':
            self._desired_playing = True
        elif event == 'paused':
            self._desired_playing = False
        self.state = 'stopped' if event == 'ready' else event
        self.message = f'Error: {detail}' if event == 'error' else {
            'playing': 'Playing', 'paused': 'Paused', 'buffering': 'Buffering',
            'ready': 'Ready', 'stopped': 'Stopped',
        }.get(event, event)
        if event == 'playing':
            self._sample()
        self._notify('state')

    def tick(self):
        if self._closed:
            return
        self._sample()
        if self.history.error:
            self._history_problem = self.history.error
            self.message = self.history.error
            self._notify('message')
        elif self._history_problem:
            if self.message == self._history_problem:
                self.message = self.state.capitalize()
            self._history_problem = None
            self._notify('message')
        self._notify('progress')

    def close(self):
        if self._closed:
            return
        self._sample()
        self.history.finish('exit')
        self._closed = True
        try:
            self.audio.close()
        finally:
            self.history.close()
