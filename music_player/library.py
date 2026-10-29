"""Small SQLite index. Each connection belongs to the thread that creates it."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Song:
    id: int
    folder_id: int
    path: str
    title: str
    artist: str
    album: str
    duration: float
    mtime_ns: int
    size: int
    artwork_path: str | None


class LibraryDatabase:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER NOT NULL REFERENCES folders(id),
                path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                album TEXT NOT NULL,
                duration REAL NOT NULL,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                artwork_path TEXT
            );
        """)

    def configure_default_folder(self, folder):
        if not self.folders():
            self.connection.execute("INSERT INTO folders(path) VALUES (?)", (str(Path(folder).expanduser().resolve()),))
            self.connection.commit()

    def folders(self):
        return self.connection.execute("SELECT id, path FROM folders ORDER BY id").fetchall()

    def songs(self):
        return [Song(**dict(row)) for row in self.connection.execute("SELECT * FROM songs ORDER BY id")]

    def upsert(self, folder_id, path, metadata, stat, artwork_path):
        self.connection.execute("""
            INSERT INTO songs(folder_id, path, title, artist, album, duration, mtime_ns, size, artwork_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                folder_id=excluded.folder_id, title=excluded.title,
                artist=excluded.artist, album=excluded.album, duration=excluded.duration,
                mtime_ns=excluded.mtime_ns, size=excluded.size, artwork_path=excluded.artwork_path
        """, (folder_id, str(path), metadata.title, metadata.artist, metadata.album,
              metadata.duration, stat.st_mtime_ns, stat.st_size, artwork_path))
        row = self.connection.execute("SELECT * FROM songs WHERE path = ?", (str(path),)).fetchone()
        return Song(**dict(row))

    def remove(self, ids):
        self.connection.executemany("DELETE FROM songs WHERE id = ?", ((id_,) for id_ in ids))

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.commit()
        self.close()
