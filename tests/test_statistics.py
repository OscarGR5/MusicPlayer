"""Controlled Phase 4 databases: no real-time waits and no personal history."""

import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from music_player.history import ListeningTracker
from music_player.paths import database_path
from music_player.playlists import PlaylistStore
from music_player.statistics import StatisticsReader, listening_time
from test_history import Clock, listen


@pytest.fixture
def history_db(indexed_songs):
    # Initialize the real Phase 4 schema, including playlist memberships.
    history = ListeningTracker(database_path())
    history.close()
    playlists = PlaylistStore(database_path())
    playlists.create('Saved order', [indexed_songs[2].id, indexed_songs[0].id])
    return database_path()


def add_session(path, song, key, start='2026-01-02T12:00:00+00:00', seconds=40,
                qualified=True, zone='UTC', artist='Historical artist', album='Shared album',
                intervals=None, qualified_at=None, title='Historical title'):
    instant = datetime.fromisoformat(start)
    end = instant + timedelta(seconds=seconds)
    if intervals is None:
        local = instant.astimezone(ZoneInfo(zone)).date().isoformat()
        intervals = [(start, end.isoformat(), local, zone, seconds)]
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO playback_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (key, song, title, artist, album, start, end.isoformat(), 'eos', 120, 99999))
        for index, values in enumerate(intervals, 1):
            db.execute('INSERT INTO listening_intervals VALUES (?, ?, ?, ?, ?, ?, ?)', (key, index, *values))
        if qualified:
            db.execute('INSERT INTO qualified_plays VALUES (?, ?)',
                       (key, qualified_at or (instant + timedelta(seconds=30)).isoformat()))


def read(path, period=None, today=date(2026, 10, 29)):
    reader = StatisticsReader(path)
    try:
        return reader.read(period, today)
    finally:
        reader.close()


def dump(path):
    with sqlite3.connect(path) as db:
        return tuple(db.iterdump()), db.execute('PRAGMA foreign_key_check').fetchall()


def test_totals_rankings_partial_listens_and_metadata(history_db, indexed_songs):
    a, b, c, d = [song.id for song in indexed_songs]
    add_session(history_db, a, 'one', seconds=40)
    add_session(history_db, a, 'two', seconds=50)
    add_session(history_db, b, 'three', seconds=100, artist='Other artist')
    add_session(history_db, c, 'partial', seconds=7, qualified=False)
    add_session(history_db, d, 'zero', seconds=0, qualified=False)
    with sqlite3.connect(history_db) as db:
        db.execute("UPDATE songs SET title='Retagged', artist='New artist', album='New album' WHERE id=?", (a,))
        db.execute('UPDATE songs SET available=0 WHERE id=?', (b,))
    stats = read(history_db)
    assert (stats.seconds, stats.plays, stats.unique_songs) == (197, 3, 3)
    assert [(row.song_id, row.plays, row.seconds) for row in stats.songs] == [(a, 2, 90), (b, 1, 100)]
    assert (stats.songs[0].title, stats.songs[0].artist) == ('Retagged', 'New artist')
    assert (stats.songs[1].title, stats.songs[1].artist) == ('Historical title', 'Other artist')
    assert [(row.title, row.seconds, row.plays) for row in stats.artists] == [
        ('Other artist', 100, 1), ('Historical artist', 97, 2)]
    assert [(row.artist, row.title, row.seconds) for row in stats.albums] == [
        ('Other artist', 'Shared album', 100), ('Historical artist', 'Shared album', 97)]
    assert len(stats.activity) == 12
    assert sum(row.seconds for row in stats.activity) == 197


def test_top_ten_and_deterministic_ties(history_db, indexed_songs):
    with sqlite3.connect(history_db) as db:
        folder = db.execute('SELECT id FROM folders LIMIT 1').fetchone()[0]
        for index in range(12):
            db.execute('''INSERT INTO songs(folder_id,path,title,artist,album,duration,mtime_ns,size)
                          VALUES (?, ?, 'Same title', 'Artist', 'Album', 120, 0, 0)''', (folder, f'/generated/{index}'))
        ids = [row[0] for row in db.execute("SELECT id FROM songs WHERE path LIKE '/generated/%' ORDER BY id")]
    for index, song_id in enumerate(ids):
        add_session(history_db, song_id, f'session-{index}', artist=f'Artist {index:02}', album='Album')
    stats = read(history_db)
    assert len(stats.songs) == len(stats.artists) == len(stats.albums) == 10
    assert [row.song_id for row in stats.songs] == ids[:10]
    assert [row.title for row in stats.artists] == [f'Artist {index:02}' for index in range(10)]
    assert [row.artist for row in stats.albums] == [f'Artist {index:02}' for index in range(10)]
    assert stats.unique_songs == stats.plays == 12


@pytest.mark.parametrize('start,zone,first,second', [
    ('2026-01-31T23:59:55+00:00', 'Europe/Dublin', '2026-01-31', '2026-02-01'),
    ('2025-12-31T23:59:55+00:00', 'Europe/Dublin', '2025-12-31', '2026-01-01'),
    ('2024-02-28T23:59:55+00:00', 'Europe/Dublin', '2024-02-28', '2024-02-29'),
    ('2026-06-01T22:59:55+00:00', 'Europe/Dublin', '2026-06-01', '2026-06-02'),
    ('2026-01-31T14:59:55+00:00', 'Asia/Tokyo', '2026-01-31', '2026-02-01'),
])
def test_tracker_midnight_month_year_and_leap_boundaries(history_db, indexed_songs, start, zone, first, second):
    from dataclasses import replace
    clock = Clock(datetime.fromisoformat(start))
    history = ListeningTracker(history_db, clock.monotonic, clock.utcnow, ZoneInfo(zone))
    history.start(replace(indexed_songs[0], duration=10))
    listen(history, clock, 11)
    history.finish('eos')
    history.close()
    stats = read(history_db, first[:7])
    assert next(row.seconds for row in stats.activity if row.period == first) == 5
    after = read(history_db, second[:7])
    assert next(row.seconds for row in after.activity if row.period == second) == 6
    assert after.plays == 1
    if first[:7] != second[:7]:
        assert stats.plays == 0
        assert stats.seconds == 5 and after.seconds == 6
    if second.startswith('2024-02'):
        assert len(after.activity) == 29


@pytest.mark.parametrize('start', ['2026-03-29T00:59:55+00:00', '2026-10-25T00:59:55+00:00'])
def test_dst_transitions_use_recorded_zone(history_db, indexed_songs, start, monkeypatch):
    clock = Clock(datetime.fromisoformat(start))
    history = ListeningTracker(history_db, clock.monotonic, clock.utcnow, ZoneInfo('Europe/Dublin'))
    history.start(indexed_songs[0])
    listen(history, clock, 40)
    history.finish('eos')
    history.close()
    # The query never asks the current computer timezone to attribute history.
    monkeypatch.setenv('TZ', 'Pacific/Honolulu')
    stats = read(history_db, start[:7])
    assert stats.seconds == 40 and stats.plays == 1
    assert sum(row.seconds for row in stats.activity) == 40


def test_qualification_zone_at_instant_not_latest_or_current(history_db, indexed_songs):
    add_session(history_db, indexed_songs[0].id, 'travel', start='2026-01-31T23:59:30+00:00',
                qualified_at='2026-02-01T00:00:00+00:00', intervals=[
                    ('2026-01-31T23:59:30+00:00', '2026-02-01T00:00:10+00:00', '2026-01-31', 'America/New_York', 40),
                    ('2026-02-01T01:00:00+00:00', '2026-02-01T01:00:10+00:00', '2026-02-01', 'Asia/Tokyo', 10)])
    assert read(history_db, '2026-01').plays == 1
    assert read(history_db, '2026-02').plays == 0
    assert read(history_db, '2026-02').seconds == 10


def test_all_time_not_limited_to_chart_and_zero_months(history_db, indexed_songs):
    add_session(history_db, indexed_songs[0].id, 'old', start='2022-02-01T00:00:00+00:00')
    stats = read(history_db)
    assert stats.seconds == 40 and stats.plays == 1
    assert '2022-02' in stats.months and '2026-10' in stats.months
    assert [row.period for row in stats.activity] == [f'2025-{month:02}' for month in (11, 12)] + [f'2026-{m:02}' for m in range(1, 11)]
    assert not any(row.seconds for row in stats.activity)
    empty = read(history_db, '2026-02')
    assert len(empty.activity) == 28
    assert empty.period == '2026-02' and empty.seconds == empty.plays == empty.unique_songs == 0


def test_qualified_only_activity_adds_month_and_counts_record(history_db, indexed_songs):
    add_session(history_db, indexed_songs[0].id, 'play', seconds=0)
    stats = read(history_db, '2026-01')
    assert stats.plays == 1 and stats.seconds == stats.unique_songs == 0
    assert len(stats.songs) == 1 and '2026-01' in stats.months


def test_deleted_library_identity_and_malformed_legacy_data(history_db, indexed_songs):
    song_id = indexed_songs[0].id
    add_session(history_db, song_id, 'deleted')
    with sqlite3.connect(history_db) as db:
        db.execute('DELETE FROM playlist_songs WHERE song_id=?', (song_id,))
        db.execute('DELETE FROM songs WHERE id=?', (song_id,))
        db.execute("UPDATE listening_intervals SET timezone='unavailable-zone'")
    stats = read(history_db, '2026-01')
    assert stats.songs[0].title == 'Historical title'
    assert stats.plays == 1 and stats.warnings
    with sqlite3.connect(history_db) as db:
        db.execute("UPDATE listening_intervals SET seconds='bad', local_date='invalid'")
        db.execute("UPDATE qualified_plays SET qualified_at='invalid'")
    stats = read(history_db)
    assert stats.plays == 1 and stats.seconds == 0 and stats.warnings
    assert read(history_db, '2026-01').plays == 0


def test_read_only_refresh_preserves_phase4_rows_and_reloads_changes(history_db, indexed_songs):
    add_session(history_db, indexed_songs[0].id, 'persisted', qualified=False)
    with sqlite3.connect(history_db) as db:
        db.execute("UPDATE playback_sessions SET ended_at=NULL, end_reason=NULL")
    before = dump(history_db)
    assert before[1] == []
    reader = StatisticsReader(history_db)
    try:
        first = reader.read(today=date(2026, 10, 29))
        assert reader.read(today=date(2026, 10, 29)) is first
        for period in (None, '2026-01', '2026-02', None):
            reader.read(period, date(2026, 10, 29))
        assert dump(history_db) == before
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            reader.connection.execute('DELETE FROM listening_intervals')
        add_session(history_db, indexed_songs[0].id, 'new', seconds=5, qualified=False)
        assert reader.read(today=date(2026, 10, 29)).seconds == 45
    finally:
        reader.close()


def test_empty_schema_and_empty_history(history_db, tmp_path):
    stats = read(history_db)
    assert stats.seconds == stats.plays == stats.unique_songs == 0
    assert not stats.songs and not stats.artists and not stats.albums
    assert stats.months == ('2026-10',)
    blank = tmp_path / 'blank.sqlite3'
    sqlite3.connect(blank).close()
    assert read(blank) == stats
    with pytest.raises(sqlite3.OperationalError):
        read(tmp_path / 'missing.sqlite3')
    assert not (tmp_path / 'missing.sqlite3').exists()


@pytest.mark.parametrize('seconds,label', [(0,'0 s'), (9,'9 s'), (125,'2 min'), (3660,'1 h 1 min'), (90060,'1 d 1 h 1 min')])
def test_listening_time_format(seconds, label):
    assert listening_time(seconds) == label


def test_qualification_uses_actual_interval_not_span_across_pauses(history_db, indexed_songs):
    add_session(history_db, indexed_songs[0].id, 'zone-change',
                qualified_at='2026-01-31T23:59:59+00:00', intervals=[
                    ('2026-01-31T21:00:00+00:00', '2026-01-31T21:00:20+00:00', '2026-01-31', 'Atlantic/Cape_Verde', 20),
                    ('2026-01-31T22:00:00+00:00', '2026-01-31T22:00:10+00:00', '2026-02-01', 'Asia/Tokyo', 10),
                    ('2026-01-31T23:59:49+00:00', '2026-01-31T23:59:59+00:00', '2026-01-31', 'Atlantic/Cape_Verde', 10),
                    ('2026-02-01T00:01:00+00:00', '2026-02-01T00:01:10+00:00', '2026-02-01', 'Asia/Tokyo', 10)])
    assert read(history_db, '2026-01').plays == 1
    assert read(history_db, '2026-02').plays == 0


def test_artist_identity_is_exact_stored_value(history_db, indexed_songs):
    values = ('Artist', 'artist', ' Artist ', '', 'Unknown artist')
    for index, artist in enumerate(values):
        add_session(history_db, indexed_songs[0].id, f'artist-{index}', artist=artist)
    stats = read(history_db)
    assert {row.title for row in stats.artists} == set(values)
    assert all(row.plays == 1 for row in stats.artists)
