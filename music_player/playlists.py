"""Persistent playlist membership and ordering, without GTK or playback policy."""

import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from .library import LibraryDatabase, Song


@dataclass(frozen=True)
class Playlist:
    id: int
    name: str
    created_at: str


class PlaylistStore:
    def __init__(self, database):
        self.database = database
        with LibraryDatabase(database) as library:
            library.connection.executescript('''
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
                    name_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS playlist_songs (
                    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                    song_id INTEGER NOT NULL REFERENCES songs(id),
                    position INTEGER NOT NULL CHECK(position >= 0),
                    PRIMARY KEY (playlist_id, song_id),
                    UNIQUE (playlist_id, position)
                );
            ''')

    @staticmethod
    def _name(value):
        name = value.strip()
        if not name:
            raise ValueError('Enter a playlist name.')
        if len(name) > 80:
            raise ValueError('Playlist names must be 80 characters or fewer.')
        return name, unicodedata.normalize('NFC', name).casefold()

    @staticmethod
    def _require(connection, playlist_id):
        row = connection.execute('SELECT id, name, created_at FROM playlists WHERE id=?', (playlist_id,)).fetchone()
        if row is None:
            raise ValueError('This playlist no longer exists.')
        return Playlist(**dict(row))

    def list(self):
        with LibraryDatabase(self.database) as library:
            return [Playlist(**dict(row)) for row in library.connection.execute('SELECT id, name, created_at FROM playlists ORDER BY id')]

    def get(self, playlist_id):
        with LibraryDatabase(self.database) as library:
            return self._require(library.connection, playlist_id)

    def create(self, value, song_ids=()):
        name, key = self._name(value)
        with LibraryDatabase(self.database) as library:
            try:
                cursor = library.connection.execute('INSERT INTO playlists(name,name_key,created_at) VALUES(?,?,?)',
                                                    (name, key, datetime.now(timezone.utc).isoformat()))
            except sqlite3.IntegrityError as exc:
                raise ValueError('A playlist with that name already exists.') from exc
            self._add(library.connection, cursor.lastrowid, song_ids)
            return self._require(library.connection, cursor.lastrowid)

    def rename(self, playlist_id, value):
        name, key = self._name(value)
        with LibraryDatabase(self.database) as library:
            self._require(library.connection, playlist_id)
            try:
                library.connection.execute('UPDATE playlists SET name=?,name_key=? WHERE id=?', (name, key, playlist_id))
            except sqlite3.IntegrityError as exc:
                raise ValueError('A playlist with that name already exists.') from exc
            return self._require(library.connection, playlist_id)

    def delete(self, playlist_id):
        with LibraryDatabase(self.database) as library:
            self._require(library.connection, playlist_id)
            library.connection.execute('DELETE FROM playlists WHERE id=?', (playlist_id,))

    def songs(self, playlist_id):
        with LibraryDatabase(self.database) as library:
            self._require(library.connection, playlist_id)
            return [Song(**dict(row)) for row in library.connection.execute('''
                SELECT s.* FROM playlist_songs p JOIN songs s ON s.id=p.song_id
                WHERE p.playlist_id=? ORDER BY p.position
            ''', (playlist_id,))]

    def add(self, playlist_id, song_ids):
        with LibraryDatabase(self.database) as library:
            db = library.connection
            self._require(db, playlist_id)
            return self._add(db, playlist_id, song_ids)

    @staticmethod
    def _add(db, playlist_id, song_ids):
        existing = {row[0] for row in db.execute('SELECT song_id FROM playlist_songs WHERE playlist_id=?', (playlist_id,))}
        position = len(existing)
        added = 0
        for song_id in dict.fromkeys(song_ids):
            if song_id not in existing:
                db.execute('INSERT INTO playlist_songs VALUES(?,?,?)', (playlist_id, song_id, position))
                existing.add(song_id)
                position += 1
                added += 1
        return added

    @staticmethod
    def _reorder(db, playlist_id, ids):
        # Vacate the old position range before assigning compact unique positions.
        maximum = db.execute('SELECT MAX(position) FROM playlist_songs WHERE playlist_id=?', (playlist_id,)).fetchone()[0]
        if maximum is not None:
            db.execute('UPDATE playlist_songs SET position=position+? WHERE playlist_id=?', (maximum + 1, playlist_id))
            db.executemany('UPDATE playlist_songs SET position=? WHERE playlist_id=? AND song_id=?',
                           ((position, playlist_id, song_id) for position, song_id in enumerate(ids)))

    def remove(self, playlist_id, song_ids):
        with LibraryDatabase(self.database) as library:
            db = library.connection
            self._require(db, playlist_id)
            db.executemany('DELETE FROM playlist_songs WHERE playlist_id=? AND song_id=?',
                           ((playlist_id, song_id) for song_id in song_ids))
            ids = [row[0] for row in db.execute('SELECT song_id FROM playlist_songs WHERE playlist_id=? ORDER BY position', (playlist_id,))]
            self._reorder(db, playlist_id, ids)

    def move(self, playlist_id, song_id, direction):
        if direction not in (-1, 1):
            raise ValueError('Choose Move Up or Move Down.')
        with LibraryDatabase(self.database) as library:
            db = library.connection
            self._require(db, playlist_id)
            ids = [row[0] for row in db.execute('SELECT song_id FROM playlist_songs WHERE playlist_id=? ORDER BY position', (playlist_id,))]
            if song_id not in ids:
                raise ValueError('This song is no longer in the playlist.')
            position = ids.index(song_id)
            destination = position + direction
            if not 0 <= destination < len(ids):
                return False
            ids[position], ids[destination] = ids[destination], ids[position]
            self._reorder(db, playlist_id, ids)
            return True
