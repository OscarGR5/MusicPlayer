import sqlite3
from dataclasses import replace

import pytest

from music_player.history import ListeningTracker
from music_player.library import LibraryDatabase
from music_player.paths import database_path
from music_player.playlists import PlaylistStore
from test_history import Clock, listen
from test_library import scan


@pytest.fixture
def playlists(indexed_songs):
    return PlaylistStore(database_path())


def test_create_validate_casefold_rename_and_reopen(playlists):
    first = playlists.create('  Road Trip  ')
    assert first.name == 'Road Trip'
    assert first.created_at.endswith('+00:00')
    for invalid in ('', '   ', 'x' * 81):
        with pytest.raises(ValueError):
            playlists.create(invalid)
    with pytest.raises(ValueError, match='already exists'):
        playlists.create(' road TRIP ')
    unicode = playlists.create('Straße')
    with pytest.raises(ValueError, match='already exists'):
        playlists.create('STRASSE')
    renamed = playlists.rename(first.id, '  Driving  ')
    assert renamed.id == first.id and renamed.created_at == first.created_at and renamed.name == 'Driving'
    assert playlists.rename(first.id, 'DRIVING').name == 'DRIVING'
    with pytest.raises(ValueError):
        playlists.rename(unicode.id, 'Driving')
    with pytest.raises(ValueError):
        playlists.rename(first.id, '')
    reopened = PlaylistStore(database_path())
    assert reopened.get(first.id).name == 'DRIVING'
    assert [row.id for row in reopened.list()] == [first.id, unicode.id]


def test_multiple_membership_duplicates_and_saved_order(playlists, indexed_songs):
    ids = [song.id for song in indexed_songs]
    first = playlists.create('First', [ids[2], ids[0]])
    second = playlists.create('Second')
    assert playlists.add(first.id, [ids[0], ids[3], ids[3], ids[1]]) == 2
    assert playlists.add(first.id, ids) == 0
    playlists.add(second.id, ids)
    assert [song.id for song in playlists.songs(first.id)] == [ids[2], ids[0], ids[3], ids[1]]
    assert [song.id for song in PlaylistStore(database_path()).songs(first.id)] == [ids[2], ids[0], ids[3], ids[1]]
    assert [song.id for song in playlists.songs(second.id)] == ids
    playlists.rename(first.id, 'Renamed')
    assert [song.id for song in playlists.songs(first.id)] == [ids[2], ids[0], ids[3], ids[1]]


def test_move_boundaries_bulk_remove_and_position_integrity(playlists, indexed_songs):
    ids = [song.id for song in indexed_songs]
    playlist = playlists.create('Order', ids)
    assert not playlists.move(playlist.id, ids[0], -1)
    assert not playlists.move(playlist.id, ids[-1], 1)
    assert playlists.move(playlist.id, ids[2], -1)
    assert [row.id for row in playlists.songs(playlist.id)] == [ids[0], ids[2], ids[1], ids[3]]
    assert playlists.move(playlist.id, ids[0], 1)
    playlists.remove(playlist.id, [ids[1], ids[2]])
    assert [row.id for row in playlists.songs(playlist.id)] == [ids[0], ids[3]]
    playlists.add(playlist.id, [ids[1], ids[2]])
    assert [row.id for row in playlists.songs(playlist.id)] == [ids[0], ids[3], ids[1], ids[2]]
    with LibraryDatabase(database_path()) as db:
        assert [row[0] for row in db.connection.execute('SELECT position FROM playlist_songs WHERE playlist_id=? ORDER BY position', (playlist.id,))] == [0, 1, 2, 3]
        assert db.connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_bulk_operations_are_atomic_on_foreign_key_failure(playlists, indexed_songs):
    ids = [song.id for song in indexed_songs]
    playlist = playlists.create('Atomic')
    with pytest.raises(sqlite3.IntegrityError):
        playlists.add(playlist.id, [ids[0], 9999999, ids[1]])
    assert playlists.songs(playlist.id) == []
    with pytest.raises(sqlite3.IntegrityError):
        playlists.create('Atomic create', [ids[0], 9999999])
    assert [row.name for row in playlists.list()] == ['Atomic']
    with pytest.raises(ValueError, match='no longer exists'):
        playlists.add(999999, ids)


def test_unavailable_memberships_return_same_identity(playlists, indexed_songs):
    from pathlib import Path
    song = indexed_songs[0]
    playlist = playlists.create('Keep missing', [song.id])
    path = Path(song.path)
    contents = path.read_bytes()
    path.unlink()
    scan()
    assert playlists.songs(playlist.id)[0].id == song.id
    assert not playlists.songs(playlist.id)[0].available
    path.write_bytes(contents)
    scan()
    assert playlists.songs(playlist.id)[0].id == song.id
    assert playlists.songs(playlist.id)[0].available


def test_phase3_migration_and_delete_preserve_all_existing_data(indexed_songs):
    # The fixture database has the Phase 3 song schema; create real history first.
    clock = Clock()
    history = ListeningTracker(database_path(), clock.monotonic, clock.utcnow)
    history.start(replace(indexed_songs[0], duration=2))
    listen(history, clock, 1)
    history.finish('eos')
    connection = history.db.connection
    protected = ('folders', 'songs', 'playback_sessions', 'qualified_plays', 'listening_intervals')
    before = {name: [tuple(row) for row in connection.execute(f'SELECT * FROM {name}')] for name in protected}
    assert 'playlists' not in {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    playlists = PlaylistStore(database_path())
    playlist = playlists.create('Delete safely', [song.id for song in indexed_songs])
    other = playlists.create('Keep', [indexed_songs[0].id])
    playlists.rename(playlist.id, 'Rename safely')
    playlists.move(playlist.id, indexed_songs[1].id, -1)
    playlists.remove(playlist.id, [indexed_songs[0].id])
    playlists.delete(playlist.id)
    assert len(playlists.songs(other.id)) == 1
    assert connection.execute('SELECT COUNT(*) FROM playlist_songs WHERE playlist_id=?', (playlist.id,)).fetchone()[0] == 0
    assert {name: [tuple(row) for row in connection.execute(f'SELECT * FROM {name}')] for name in protected} == before
    assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    assert PlaylistStore(database_path()).list() == [other]
    history.close()
