import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from music_player.history import ListeningTracker
from music_player.library import LibraryDatabase
from music_player.paths import database_path
from test_library import scan


class Clock:
    def __init__(self, wall=None):
        self.value = 0
        self.wall = wall or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def monotonic(self):
        return self.value

    def utcnow(self):
        return self.wall

    def advance(self, seconds):
        self.value += seconds
        self.wall += timedelta(seconds=seconds)


def tracker(clock, zone=None):
    return ListeningTracker(database_path(), clock.monotonic, clock.utcnow, zone)


def listen(history, clock, seconds, position=0):
    history.sample('playing', position)
    steps = round(seconds / 0.25)
    for _ in range(steps):
        clock.advance(0.25)
        position += 0.25
        history.sample('playing', position)
    return position


def totals(history):
    history.flush()
    connection = history.db.connection
    return (connection.execute('SELECT COALESCE(SUM(listened_seconds),0) FROM playback_sessions').fetchone()[0],
            connection.execute('SELECT COUNT(*) FROM qualified_plays').fetchone()[0])


@pytest.mark.parametrize('duration,threshold', [(120, 30), (40, 20), (2, 1), (0, 30)])
def test_qualification_rules_once_per_session(indexed_songs, duration, threshold):
    clock = Clock()
    history = tracker(clock)
    try:
        history.start(replace(indexed_songs[0], duration=duration))
        position = listen(history, clock, threshold - 0.25)
        assert totals(history)[1] == 0
        listen(history, clock, 0.5, position)
        assert totals(history)[1] == 1
        listen(history, clock, 2, position + 0.5)
        history.finish('stop')
        assert totals(history)[1] == 1
        assert history.db.connection.execute('SELECT end_reason FROM playback_sessions').fetchone()[0] == 'stop'
    finally:
        history.close()


def test_skips_pauses_stalls_seeks_and_scheduler_gaps(indexed_songs):
    clock = Clock()
    history = tracker(clock)
    try:
        history.start(indexed_songs[0])
        position = listen(history, clock, 4)
        history.suspend()
        clock.advance(40)
        history.sample('paused', position)
        # Seek forwards, then backwards. Neither skipped region counts.
        listen(history, clock, 2, 90)
        history.suspend()
        listen(history, clock, 1, 1)
        history.suspend()
        history.sample('buffering', 2)
        clock.advance(4)
        history.sample('buffering', 2)
        history.sample('playing', 2)
        for _ in range(4):
            clock.advance(1)
            history.sample('playing', 2)  # stalled position despite nominal PLAYING
        clock.advance(1)
        history.sample('playing', 100)  # unannounced seek jump is also discarded
        clock.advance(20)
        history.sample('playing', 120)  # unobserved scheduler/suspend gap
        history.finish('skip')
        assert totals(history) == pytest.approx((7, 0))
        assert history.db.connection.execute('SELECT end_reason FROM playback_sessions').fetchone()[0] == 'skip'
    finally:
        history.close()


def test_failed_playback_and_external_files_do_not_invent_time(indexed_songs):
    clock = Clock()
    history = tracker(clock)
    try:
        history.start(indexed_songs[0])
        history.sample('loading', 0)
        clock.advance(30)
        history.sample('error', 0)
        history.finish('error')
        assert totals(history) == (0, 0)
        history.start(replace(indexed_songs[1], id=None))
        listen(history, clock, 31)
        assert totals(history) == (0, 0)
        assert history.db.connection.execute('SELECT COUNT(*) FROM playback_sessions').fetchone()[0] == 1
    finally:
        history.close()


def test_midnight_month_attribution_and_historical_metadata(indexed_songs):
    clock = Clock(datetime(2026, 1, 31, 23, 59, 55, tzinfo=timezone.utc))
    history = tracker(clock, ZoneInfo('Europe/Dublin'))
    try:
        history.start(replace(indexed_songs[0], duration=10))
        listen(history, clock, 11)
        history.finish('eos')
        rows = history.db.connection.execute('''SELECT local_date, SUM(seconds) FROM listening_intervals
                                                GROUP BY local_date ORDER BY local_date''').fetchall()
        assert [(row[0], row[1]) for row in rows] == [('2026-01-31', 5), ('2026-02-01', 6)]
        assert history.db.connection.execute('SELECT qualified_at FROM qualified_plays').fetchone()[0].startswith('2026-02-01T00:00:00')
        history.db.connection.execute("UPDATE songs SET artist='New artist', album='New album'")
        history.db.commit()
        row = history.db.connection.execute('SELECT artist, album FROM playback_sessions').fetchone()
        assert tuple(row) == ('Original artist', 'Original album')
    finally:
        history.close()


def test_replay_creates_new_session(indexed_songs):
    clock = Clock()
    history = tracker(clock)
    try:
        for _ in range(2):
            history.start(replace(indexed_songs[0], duration=2))
            listen(history, clock, 1)
            history.finish('eos')
        assert totals(history) == (2, 2)
        assert history.db.connection.execute('SELECT COUNT(*) FROM playback_sessions').fetchone()[0] == 2
    finally:
        history.close()


def test_crash_recovery_keeps_checkpoints_without_reopening_old_session(indexed_songs):
    clock = Clock()
    first = tracker(clock)
    first.start(indexed_songs[0])
    listen(first, clock, 7)
    # Simulate process death: discard unflushed memory without invoking finish/close.
    first.db.close()
    second = tracker(clock)
    try:
        assert totals(second) == (5, 0)
        row = second.db.connection.execute('SELECT end_reason, ended_at FROM playback_sessions').fetchone()
        assert row['end_reason'] == 'interrupted' and row['ended_at']
        second.start(indexed_songs[0])
        listen(second, clock, 2)
        second.flush()
        second.flush()
        assert totals(second) == (7, 0)
        assert second.db.connection.execute('SELECT SUM(seconds) FROM listening_intervals').fetchone()[0] == 7
    finally:
        second.close()


def test_locked_checkpoint_retries_without_duplicate_intervals(indexed_songs):
    clock = Clock()
    history = tracker(clock)
    lock = sqlite3.connect(database_path())
    try:
        history.start(replace(indexed_songs[0], duration=2))
        listen(history, clock, 1)
        lock.execute('BEGIN IMMEDIATE')
        assert not history.flush()
        assert history.error
        lock.rollback()
        assert history.flush()
        assert history.flush()
        assert totals(history) == (1, 1)
        assert history.db.connection.execute('SELECT SUM(seconds) FROM listening_intervals').fetchone()[0] == 1
    finally:
        lock.close()
        history.close()


def test_missing_song_retains_identity_and_history_when_it_returns(indexed_songs, monkeypatch):
    song = indexed_songs[0]
    clock = Clock()
    history = tracker(clock)
    try:
        history.start(song)
        listen(history, clock, 1)
        history.finish('skip')
        from pathlib import Path
        path = Path(song.path)
        contents = path.read_bytes()
        path.unlink()
        assert song.id in scan()[1]
        with LibraryDatabase(database_path()) as db:
            assert song.id not in {row.id for row in db.songs()}
            assert song.id in {row.id for row in db.songs(include_unavailable=True)}
        # Restore the same bytes and original timestamp; reuse metadata and identity.
        path.write_bytes(contents)
        import os
        os.utime(path, ns=(song.mtime_ns, song.mtime_ns))
        def unexpected_parse(path):
            raise AssertionError('Unchanged returning file must reuse metadata')
        monkeypatch.setattr('music_player.scanner.read_metadata', unexpected_parse)
        restored = scan()[0]
        assert any(row.id == song.id and row.available for row in restored)
        assert totals(history) == (1, 0)
    finally:
        history.close()


def test_phase2_database_migration_is_lossless(tmp_path):
    path = tmp_path / 'phase2.sqlite3'
    connection = sqlite3.connect(path)
    connection.executescript('''
        CREATE TABLE folders (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE);
        CREATE TABLE songs (id INTEGER PRIMARY KEY AUTOINCREMENT, folder_id INTEGER NOT NULL REFERENCES folders(id),
            path TEXT NOT NULL UNIQUE, title TEXT NOT NULL, artist TEXT NOT NULL, album TEXT NOT NULL,
            duration REAL NOT NULL, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL, artwork_path TEXT);
        INSERT INTO folders VALUES(7, '/music');
        INSERT INTO songs VALUES(42, 7, '/music/example.flac', 'Original', 'Artist', 'Album', 123.5, 1234567, 2000, '/cache/art.png');
    ''')
    connection.close()
    for _ in range(2):
        with LibraryDatabase(path) as db:
            song = db.songs()[0]
            assert (song.id, song.title, song.duration, song.mtime_ns, song.artwork_path, song.available) == \
                   (42, 'Original', 123.5, 1234567, '/cache/art.png', True)


def test_restore_write_failure_aborts_scan_visibly(indexed_songs, monkeypatch):
    song = indexed_songs[0]
    with LibraryDatabase(database_path()) as db:
        db.remove([song.id])
    def fail(self, song_id):
        raise sqlite3.OperationalError('cannot write database')
    monkeypatch.setattr(LibraryDatabase, 'restore', fail)
    with pytest.raises(sqlite3.OperationalError, match='cannot write database'):
        scan()
