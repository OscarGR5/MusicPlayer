"""Conservative listening accounting with atomic, idempotent SQLite checkpoints."""

import logging
import math
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from .library import LibraryDatabase

UTC = timezone.utc
logger = logging.getLogger(__name__)


def local_timezone():
    try:
        name = str(Path('/etc/localtime').resolve().relative_to('/usr/share/zoneinfo'))
        return ZoneInfo(name)
    except (OSError, ValueError):
        pass
    try:
        with open('/etc/localtime', 'rb') as source:
            return ZoneInfo.from_file(source, key='system-local')
    except (OSError, ValueError):
        return UTC


def utc_now():
    return datetime.now(UTC)


@dataclass
class Session:
    id: str
    song: object
    started_at: datetime
    duration: float | None
    listened: float = 0
    qualified_at: datetime | None = None
    ended_at: datetime | None = None
    reason: str | None = None
    sequence: int = 0
    intervals: list = field(default_factory=list)
    dirty: bool = True


class ListeningTracker:
    """Only clock time corroborated by continuous advancing position is counted.

    Samples across seeks, pauses, buffering, backwards/large position jumps, or
    scheduler gaps >2 seconds are discarded. This favors undercounting uncertain
    audio over inventing listening time. Call sample at least four times/second.
    """

    def __init__(self, database, clock=time.monotonic, wall_clock=utc_now, zone=None):
        self.clock, self.wall_clock = clock, wall_clock
        self.zone = zone or local_timezone()
        self.db = LibraryDatabase(database)
        self.db.connection.execute('PRAGMA busy_timeout = 100')
        self.db.connection.executescript('''
            CREATE TABLE IF NOT EXISTS playback_sessions (
                id TEXT PRIMARY KEY,
                song_id INTEGER NOT NULL REFERENCES songs(id),
                title TEXT NOT NULL, artist TEXT NOT NULL, album TEXT NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT, end_reason TEXT,
                duration REAL, listened_seconds REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS listening_intervals (
                session_id TEXT NOT NULL REFERENCES playback_sessions(id),
                sequence INTEGER NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
                local_date TEXT NOT NULL, timezone TEXT NOT NULL,
                seconds REAL NOT NULL CHECK(seconds >= 0),
                PRIMARY KEY (session_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS qualified_plays (
                session_id TEXT PRIMARY KEY REFERENCES playback_sessions(id),
                qualified_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS player_preferences (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                volume REAL NOT NULL CHECK(volume >= 0 AND volume <= 1)
            );
        ''')
        # A previous process's active session never resumes counting on startup.
        self.db.connection.execute('''
            UPDATE playback_sessions SET end_reason = 'interrupted',
                ended_at = COALESCE((SELECT MAX(ended_at) FROM listening_intervals
                                     WHERE session_id = playback_sessions.id), started_at)
            WHERE ended_at IS NULL
        ''')
        self.db.commit()
        self.active = None
        self._sessions = {}
        self._anchor = None
        self._last_flush = self.clock()
        self.error = None
        self._volume_dirty = False
        row = self.db.connection.execute('SELECT volume FROM player_preferences WHERE id=1').fetchone()
        self.volume = row[0] if row else 1.0

    def start(self, song):
        self.finish('switch')
        # External files are deliberately excluded from library statistics.
        if song is None or song.id is None:
            return
        duration = song.duration if song.duration > 0 else None
        self.active = Session(uuid.uuid4().hex, song, self.wall_clock().astimezone(UTC), duration)
        self._sessions[self.active.id] = self.active

    def sample(self, state, position, duration=None):
        now = self.clock()
        wall = self.wall_clock().astimezone(UTC)
        session = self.active
        if session and duration is not None and math.isfinite(duration) and duration > 0:
            session.duration = duration
        valid = session and state == 'playing' and position is not None and math.isfinite(position) and position >= 0
        if valid and self._anchor is not None:
            before, previous_wall, previous_position = self._anchor
            elapsed, advance = now - before, position - previous_position
            if 0 < elapsed <= 2 and 0 < advance <= elapsed + 0.25:
                listened = min(elapsed, advance)
                start = wall - timedelta(seconds=listened)
                self._record(start, wall, listened)
        self._anchor = (now, wall, position) if valid else None
        if now - self._last_flush >= 5:
            self.flush()

    def _record(self, start, end, seconds):
        session = self.active
        threshold = min(30, session.duration / 2) if session.duration else 30
        if session.qualified_at is None and session.listened + seconds + 1e-6 >= threshold:
            remaining = max(0, threshold - session.listened)
            session.qualified_at = start + timedelta(seconds=remaining)
        session.listened += seconds
        session.dirty = True
        cursor = start
        while cursor < end:
            local = cursor.astimezone(self.zone)
            midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), self.zone).astimezone(UTC)
            boundary = min(midnight, end)
            amount = (boundary - cursor).total_seconds()
            date = local.date().isoformat()
            # Coalesce consecutive samples until the next checkpoint or state boundary.
            if session.intervals and session.intervals[-1]['local_date'] == date and self._anchor is not None and \
                    abs((cursor - session.intervals[-1]['end']).total_seconds()) < 0.05:
                interval = session.intervals[-1]
                interval['end'] = boundary
                interval['seconds'] += amount
            else:
                session.sequence += 1
                session.intervals.append(dict(sequence=session.sequence, start=cursor, end=boundary,
                                              local_date=date, seconds=amount))
            cursor = boundary

    def suspend(self):
        self._anchor = None
        self.flush()

    def finish(self, reason):
        self._anchor = None
        if self.active is not None:
            self.active.ended_at = self.wall_clock().astimezone(UTC)
            self.active.reason = reason
            self.active.dirty = True
            self.active = None
        self.flush()

    def set_volume(self, volume):
        self.volume = volume
        self._volume_dirty = True

    def flush(self):
        try:
            with self.db.connection:
                if self._volume_dirty:
                    self.db.connection.execute('''INSERT INTO player_preferences VALUES(1, ?)
                        ON CONFLICT(id) DO UPDATE SET volume=excluded.volume''', (self.volume,))
                for session in self._sessions.values():
                    if not session.dirty:
                        continue
                    self.db.connection.execute('''
                        INSERT INTO playback_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET ended_at=excluded.ended_at,
                            end_reason=excluded.end_reason, duration=excluded.duration,
                            listened_seconds=excluded.listened_seconds
                    ''', (session.id, session.song.id, session.song.title, session.song.artist, session.song.album,
                          session.started_at.isoformat(), session.ended_at.isoformat() if session.ended_at else None,
                          session.reason, session.duration, session.listened))
                    for interval in session.intervals:
                        self.db.connection.execute('''INSERT OR IGNORE INTO listening_intervals
                            VALUES(?, ?, ?, ?, ?, ?, ?)''',
                            (session.id, interval['sequence'], interval['start'].isoformat(), interval['end'].isoformat(),
                             interval['local_date'], str(self.zone), interval['seconds']))
                    if session.qualified_at is not None:
                        self.db.connection.execute('INSERT OR IGNORE INTO qualified_plays VALUES (?, ?)',
                                                   (session.id, session.qualified_at.isoformat()))
            self._sessions = {key: session for key, session in self._sessions.items() if session.ended_at is None}
            for session in self._sessions.values():
                session.intervals.clear()
                session.dirty = False
            self._volume_dirty = False
            self._last_flush = self.clock()
            self.error = None
            return True
        except sqlite3.Error as exc:
            # Retain all pending sessions/intervals and retry with the same primary keys.
            self.error = f'Listening history could not be saved: {exc}'
            logger.error(self.error)
            return False

    def close(self):
        self.finish('exit')
        self.db.close()
