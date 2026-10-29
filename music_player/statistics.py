"""Read-only listening statistics. Persisted history is the only source of events."""

import calendar
import math
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Ranking:
    title: str
    artist: str
    seconds: float
    plays: int
    song_id: int | None = None
    artwork_path: str | None = None


@dataclass(frozen=True)
class Activity:
    period: str
    seconds: float


@dataclass(frozen=True)
class Statistics:
    period: str | None
    months: tuple[str, ...]
    seconds: float
    plays: int
    unique_songs: int
    songs: tuple[Ranking, ...]
    artists: tuple[Ranking, ...]
    albums: tuple[Ranking, ...]
    activity: tuple[Activity, ...]
    warnings: tuple[str, ...] = ()


def month_label(month):
    return date.fromisoformat(month + '-01').strftime('%B %Y')


def listening_time(seconds):
    """Compact durations, retaining seconds for short partial listens."""
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f'{days} d {hours} h {minutes} min'
    if hours:
        return f'{hours} h {minutes} min'
    return f'{minutes} min' if minutes else f'{seconds} s'


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # Legacy naive timestamps were recorded as UTC, never the current zone.
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def _local_date(value):
    try:
        parsed = date.fromisoformat(value)
        return parsed.isoformat() if parsed.isoformat() == value else None
    except (TypeError, ValueError):
        return None


def _zone(value):
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        # datetime.timezone string representations can occur in legacy records.
        match = re.fullmatch(r'UTC([+-])(\d{2}):(\d{2})(?::(\d{2}))?', value or '')
        if match:
            try:
                seconds = int(match[2]) * 3600 + int(match[3]) * 60 + int(match[4] or 0)
                return timezone(timedelta(seconds=seconds * (1 if match[1] == '+' else -1)))
            except ValueError:
                pass
        return None


class StatisticsReader:
    """One read-only connection, owned by its calling worker thread.

    data_version avoids repeated aggregation while idle. A transaction keeps
    sessions, intervals, metadata and plays consistent during tracker writes.
    No LibraryDatabase initialization, migrations or history recovery runs here.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.connection = None
        self._key = None
        self._snapshot = None

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self._key = None

    def read(self, period=None, today=None):
        today = today or date.today()
        if period is not None:
            date.fromisoformat(period + '-01')
        if self.connection is None:
            self.connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2, isolation_level=None)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute('PRAGMA query_only = ON')
        connection = self.connection
        version = connection.execute('PRAGMA data_version').fetchone()[0]
        key = (version, period, today)
        if key == self._key:
            return self._snapshot
        connection.execute('BEGIN')
        try:
            snapshot = self._aggregate(connection, period, today)
        finally:
            connection.rollback()
        self._key, self._snapshot = key, snapshot
        return snapshot

    @staticmethod
    def _aggregate(connection, period, today):
        warnings = set()
        months = {today.strftime('%Y-%m')}
        monthly = defaultdict(float)
        daily = defaultdict(float)
        # [listening seconds, qualified plays] keyed by historical identities.
        songs, artists, albums = (defaultdict(lambda: [0.0, 0]) for _ in range(3))
        metadata = {}
        sessions = {}
        album_covers = {}
        qualified = {}
        zones = {}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'playback_sessions', 'listening_intervals', 'qualified_plays', 'songs'}
        if required <= tables:
            for row in connection.execute('''
                SELECT h.*, s.title AS current_title, s.artist AS current_artist,
                       s.available, s.artwork_path, s.album AS current_album
                FROM playback_sessions h LEFT JOIN songs s ON s.id = h.song_id
                ORDER BY h.started_at, h.id
            '''):
                song_id = row['song_id']
                artist = row['artist'] if row['artist'] is not None else 'Unknown artist'
                album = row['album'] if row['album'] is not None else 'Unknown album'
                sessions[row['id']] = (song_id, artist, album)
                current = row['available'] == 1
                if row['artwork_path'] and row['current_artist'] == artist and row['current_album'] == album:
                    album_covers.setdefault((artist, album), row['artwork_path'])
                # Latest session is the deterministic fallback for missing files.
                metadata[song_id] = (row['current_title'] if current and row['current_title'] else row['title'] or 'Unknown song',
                                     row['current_artist'] if current and row['current_artist'] else artist,
                                     row['artwork_path'])
            for row in connection.execute('SELECT session_id, qualified_at FROM qualified_plays ORDER BY session_id'):
                if row['session_id'] in sessions:
                    qualified[row['session_id']] = _timestamp(row['qualified_at'])

            def positive_seconds(value):
                try:
                    number = float(value)
                    if math.isfinite(number) and number >= 0:
                        return number
                except (ValueError, TypeError, OverflowError):
                    pass
                warnings.add('Invalid listening durations were ignored.')
                return 0.0

            connection.create_function('positive_seconds', 1, positive_seconds)
            # SQL combines checkpoints before Python groups historical identities.
            for row in connection.execute('''
                SELECT session_id, local_date, SUM(positive_seconds(seconds)) AS seconds
                FROM listening_intervals GROUP BY session_id, local_date
                ORDER BY session_id, local_date
            '''):
                session_id = row['session_id']
                if session_id not in sessions:
                    continue
                stamp = _local_date(row['local_date'])
                seconds = row['seconds']
                if stamp and seconds > 0:
                    months.add(stamp[:7])
                    monthly[stamp[:7]] += seconds
                    daily[stamp] += seconds
                elif not stamp:
                    warnings.add('Some listening dates are invalid; their time appears only in All Time totals.')
                if period is None or stamp and stamp[:7] == period:
                    song_id, artist, album = sessions[session_id]
                    songs[song_id][0] += seconds
                    artists[artist][0] += seconds
                    albums[(artist, album)][0] += seconds
            # Use actual interval bounds here: combining separated intervals
            # could otherwise invent a containing span across a timezone change.
            for row in connection.execute('''
                SELECT i.* FROM listening_intervals i
                JOIN qualified_plays q ON q.session_id = i.session_id
                ORDER BY i.session_id, i.sequence
            '''):
                session_id = row['session_id']
                stamp = _local_date(row['local_date'])
                instant = qualified.get(session_id)
                start, end = _timestamp(row['started_at']), _timestamp(row['ended_at'])
                if instant and start and end:
                    if start <= instant <= end:
                        rank = (0, abs((instant - start).total_seconds()))
                    elif start <= instant:
                        rank = (1, abs((instant - end).total_seconds()))
                    else:
                        rank = (2, (start - instant).total_seconds())
                    candidate = (rank, row['timezone'] or '', stamp or '')
                    if session_id not in zones or candidate < zones[session_id]:
                        zones[session_id] = candidate

            for session_id, instant in qualified.items():
                candidate = zones.get(session_id)
                zone = _zone(candidate[1]) if candidate else None
                if instant and zone:
                    stamp = instant.astimezone(zone).date().isoformat()
                elif instant and candidate and candidate[0][0] == 0 and candidate[2]:
                    # Unknown legacy zone, but a split-at-midnight interval still
                    # identifies the local day when it contains qualification.
                    stamp = candidate[2]
                    warnings.add('Some legacy timezones were recovered from recorded interval dates.')
                else:
                    stamp = instant.date().isoformat() if instant else None
                    warnings.add('Some play dates lack a usable timezone or timestamp; UTC is used where possible.')
                if stamp:
                    months.add(stamp[:7])
                if period is None or stamp and stamp[:7] == period:
                    song_id, artist, album = sessions[session_id]
                    songs[song_id][1] += 1
                    artists[artist][1] += 1
                    albums[(artist, album)][1] += 1
        elif tables & required:
            warnings.add('Listening history is incomplete. Some history tables are missing.')

        song_rows = [Ranking(metadata[key][0], metadata[key][1], value[0], value[1], key, metadata[key][2])
                     for key, value in songs.items() if value[1] > 0]
        song_rows.sort(key=lambda row: (-row.plays, -row.seconds, row.song_id))
        artist_rows = [Ranking(key, key, value[0], value[1]) for key, value in artists.items() if any(value)]
        artist_rows.sort(key=lambda row: (-row.seconds, row.title.casefold(), row.title))
        album_rows = []
        for (artist, album), value in albums.items():
            if not any(value):
                continue
            # Covers can be reused only from songs with matching historical tags.
            cover = album_covers.get((artist, album))
            album_rows.append(Ranking(album, artist, value[0], value[1], artwork_path=cover))
        album_rows.sort(key=lambda row: (-row.seconds, row.artist.casefold(), row.artist, row.title.casefold(), row.title))
        if period:
            first = date.fromisoformat(period + '-01')
            activity = tuple(Activity(f'{period}-{day:02}', daily[f'{period}-{day:02}'])
                             for day in range(1, calendar.monthrange(first.year, first.month)[1] + 1))
        else:
            end = today.year * 12 + today.month - 1
            activity = tuple(Activity(f'{index // 12:04}-{index % 12 + 1:02}',
                                      monthly[f'{index // 12:04}-{index % 12 + 1:02}'])
                             for index in range(end - 11, end + 1))
        return Statistics(period, tuple(sorted(months, reverse=True)), sum(value[0] for value in songs.values()),
                          sum(value[1] for value in songs.values()), sum(value[0] > 0 for value in songs.values()),
                          tuple(song_rows[:10]), tuple(artist_rows[:10]), tuple(album_rows[:10]), activity,
                          tuple(sorted(warnings)))
