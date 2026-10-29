from dataclasses import replace
from pathlib import Path
import random

import pytest

from music_player.controller import PlaybackController
from music_player.paths import database_path
from test_history import Clock


class FakeAudio:
    can_seek = True

    def __init__(self, status, event):
        self.event = event
        self.path = None
        self.position = 0
        self.duration = 120
        self.seeking = False
        self.volume = 1
        self.muted = False
        self.loads = []
        self.seeks = []
        self.closed = False

    def load(self, path):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        self.path = path
        self.position = 0
        self.loads.append(path)
        self.event('ready')

    def play(self):
        self.event('playing')

    def pause(self):
        self.event('paused')

    def stop(self):
        self.position = 0
        self.event('stopped')

    def seek(self, position):
        self.seeking = True
        self.event('seeking')
        self.position = position
        self.seeks.append(position)
        self.seeking = False
        self.event('seeked')

    def close(self):
        self.closed = True


@pytest.fixture
def controller(indexed_songs):
    clock = Clock()
    events = []
    player = PlaybackController(database_path(), events.append, rng=random.Random(7), audio_factory=FakeAudio,
                                clock=clock.monotonic, wall_clock=clock.utcnow)
    yield player, clock, events
    player.close()


def test_sequential_queue_snapshot_end_and_previous_from_middle(controller, indexed_songs):
    player, clock, events = controller
    source = list(indexed_songs)
    player.activate(source)
    source.reverse()
    assert player.queue == tuple(indexed_songs)
    assert player.current_song_id == indexed_songs[0].id
    player.previous()
    assert player.audio.seeks == [0]
    for song in indexed_songs[1:]:
        player.next()
        assert player.current_song_id == song.id
    player.next()
    assert player.state == 'stopped'
    assert player.message == 'End of queue'
    player.activate(indexed_songs, 2)
    player.previous()
    assert player.current_song_id == indexed_songs[1].id
    player.next()
    assert player.current_song_id == indexed_songs[2].id


def test_previous_restarts_after_three_seconds_and_navigates_while_paused(controller, indexed_songs):
    player, clock, events = controller
    player.activate(indexed_songs, 1)
    player.audio.position = 3.01
    player.pause()
    player.previous()
    assert player.current_index == 1 and player.audio.position == 0
    assert player.state == 'paused'
    player.previous()
    assert player.current_index == 0 and player.state == 'paused'
    player.next()
    assert player.current_index == 1 and player.state == 'paused'
    player.play_pause()
    player.next()
    assert player.current_index == 2 and player.state == 'playing'


def test_shuffle_no_duplicates_cycle_boundary_and_actual_history(controller, indexed_songs):
    player, clock, events = controller
    player.activate(indexed_songs)
    loads = len(player.audio.loads)
    player.toggle_shuffle()
    assert len(player.audio.loads) == loads
    visited = [player.current_index]
    for _ in range(3):
        player.next()
        visited.append(player.current_index)
    assert sorted(visited) == [0, 1, 2, 3]
    player.previous()
    assert player.current_index == visited[-2]
    player.next()
    assert player.current_index == visited[-1]
    player.cycle_repeat()
    player.audio.event('eos')
    assert player.current_index != visited[-1]
    new_cycle = [player.current_index]
    for _ in range(3):
        player.audio.event('eos')
        new_cycle.append(player.current_index)
    assert sorted(new_cycle) == [0, 1, 2, 3]
    current = player.current_index
    loads = len(player.audio.loads)
    player.toggle_shuffle()
    assert player.current_index == current and len(player.audio.loads) == loads
    assert player.order == [0, 1, 2, 3]


def test_shuffle_on_midcycle_excludes_already_heard_tracks(controller, indexed_songs):
    player, clock, events = controller
    player.activate(indexed_songs)
    player.next()
    player.toggle_shuffle()
    assert 0 not in player.order
    assert player.order[0] == 1
    assert sorted(player.order[1:]) == [2, 3]


def test_repeat_off_all_one_and_manual_next_override(controller, indexed_songs):
    player, clock, events = controller
    player.activate(indexed_songs, 3)
    player.audio.event('eos')
    assert player.state == 'stopped' and player.message == 'Finished'
    player.cycle_repeat()
    player.play_pause()
    player.audio.event('eos')
    assert player.current_index == 0 and player.state == 'playing'
    player.cycle_repeat()
    assert player.repeat == 'one'
    session = player.history.active.id
    player.audio.event('eos')
    assert player.current_index == 0
    assert player.history.active.id != session
    player.next()
    assert player.current_index == 1
    player.cycle_repeat()
    assert player.repeat == 'off'


def test_empty_single_missing_and_all_unavailable_queues(controller, indexed_songs):
    player, clock, events = controller
    player.activate([])
    player.next()
    player.previous()
    assert player.current is None
    player.activate([indexed_songs[0]])
    player.toggle_shuffle()
    player.cycle_repeat()
    player.audio.event('eos')
    assert player.current_index == 0 and player.state == 'playing'
    player.activate(indexed_songs)
    Path(indexed_songs[1].path).unlink()
    player.toggle_shuffle()  # sequential order
    player.next()
    assert player.current_index == 2
    Path(indexed_songs[2].path).unlink()
    Path(indexed_songs[3].path).unlink()
    Path(indexed_songs[0].path).unlink()
    player.next()  # Repeat All must terminate rather than loop over missing paths.
    assert player.state == 'stopped'


def test_declared_unavailable_entries_are_skipped(controller, indexed_songs):
    player, clock, events = controller
    player.activate([indexed_songs[0], replace(indexed_songs[1], available=False), indexed_songs[2]])
    player.next()
    assert player.current_song_id == indexed_songs[2].id


def test_errors_clear_playing_and_do_not_manufacture_listening(controller, indexed_songs):
    player, clock, events = controller
    player.activate(indexed_songs)
    clock.advance(0.5)
    player.audio.position = 0.5
    player.tick()
    player.audio.event('error', 'decoder failed')
    assert player.state == 'error'
    assert player.message == 'Error: decoder failed'
    clock.advance(40)
    player.tick()
    row = player.history.db.connection.execute('SELECT listened_seconds, end_reason FROM playback_sessions').fetchone()
    assert tuple(row) == (0.5, 'error')
    assert player.history.db.connection.execute('SELECT COUNT(*) FROM qualified_plays').fetchone()[0] == 0


def test_pause_stop_switch_and_exit_flush_history(controller, indexed_songs):
    player, clock, events = controller
    player.activate(indexed_songs)
    for _ in range(6):
        clock.advance(0.25)
        player.audio.position += 0.25
        player.tick()
    player.pause()
    assert player.history.db.connection.execute('SELECT listened_seconds FROM playback_sessions').fetchone()[0] == 1.5
    clock.advance(20)
    player.tick()
    player.next()
    assert player.state == 'paused'
    player.play_pause()
    clock.advance(0.5)
    player.audio.position = 0.5
    player.stop()
    rows = player.history.db.connection.execute('SELECT listened_seconds,end_reason FROM playback_sessions ORDER BY started_at').fetchall()
    assert [tuple(row) for row in rows] == [(1.5, 'switch'), (0.5, 'stop')]
    player.close()
    assert player.audio.closed


def test_external_excluded_indexed_file_associated_and_volume_saved(controller, indexed_songs, wav_file):
    player, clock, events = controller
    player.open_file(wav_file)
    assert player.current_song_id is None
    assert player.history.active is None
    player.open_file(indexed_songs[0].path)
    assert player.current_song_id == indexed_songs[0].id
    player.set_volume(0.37)
    player.toggle_mute()
    assert player.audio.muted and player.audio.volume == 0.37
    player.toggle_mute()
    assert not player.audio.muted and player.audio.volume == 0.37
    player.close()
    reopened = PlaybackController(database_path(), lambda _: None, audio_factory=FakeAudio)
    try:
        assert reopened.audio.volume == 0.37
    finally:
        reopened.close()


def test_missing_current_at_repeat_one_eos_stops_and_notifies(controller, indexed_songs):
    player, clock, events = controller
    player.activate(indexed_songs)
    player.cycle_repeat()
    player.cycle_repeat()
    Path(indexed_songs[0].path).unlink()
    player.audio.event('eos')
    assert player.state == 'stopped'
    assert 'unavailable' in player.message
    assert events[-1] == 'state'


def test_external_corrupt_file_reports_useful_error(controller, tmp_path):
    player, clock, events = controller
    path = tmp_path / 'corrupt.mp3'
    path.write_bytes(b'not audio')
    with pytest.raises(ValueError, match='Could not read audio file'):
        player.open_file(path)
    assert player.current is None


def test_checkpoint_error_clears_after_successful_retry(controller, indexed_songs):
    import sqlite3
    player, clock, events = controller
    player.activate(indexed_songs)
    clock.advance(1)
    player.audio.position = 1
    player.tick()
    lock = sqlite3.connect(database_path())
    try:
        lock.execute('BEGIN IMMEDIATE')
        clock.advance(4)
        player.tick()
        assert player.message.startswith('Listening history could not be saved:')
        lock.rollback()
        clock.advance(0.25)
        player.audio.position = 1.25
        player.tick()
        assert player.history.error is None
        assert player.message == 'Playing'
    finally:
        lock.close()


def test_saved_order_start_skips_missing_preserves_modes_and_empty_is_noop(controller, indexed_songs):
    player, clock, events = controller
    player.activate(indexed_songs)
    previous_queue = player.queue
    assert not player.play_queue([])
    assert player.queue == previous_queue
    unavailable = [replace(song, available=False) for song in indexed_songs]
    assert not player.play_queue(unavailable)
    assert player.queue == previous_queue
    Path(indexed_songs[1].path).unlink()
    saved = [replace(indexed_songs[0], available=False), indexed_songs[1], indexed_songs[3], indexed_songs[2]]
    player.toggle_shuffle()
    player.cycle_repeat()
    player.cycle_repeat()
    assert player.play_queue(saved)
    assert player.queue == tuple(saved)
    assert player.current_song_id == indexed_songs[3].id
    assert player.shuffle and player.repeat == 'one'
