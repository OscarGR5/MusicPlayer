"""GTK song objects, search matching, and thumbnail reuse for recycled rows."""

from functools import lru_cache

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject  # noqa: E402


class SongItem(GObject.Object):
    playing = GObject.Property(type=bool, default=False)

    def __init__(self, song):
        super().__init__()
        self.song = song
        self.search_text = " ".join((song.title, song.artist, song.album)).casefold()

    def matches(self, query):
        return all(term in self.search_text for term in query.casefold().split())


def compare_songs(first, second, field):
    a, b = getattr(first.song, field), getattr(second.song, field)
    if isinstance(a, str):
        a, b = a.casefold(), b.casefold()
    return (a > b) - (a < b)


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


@lru_cache(maxsize=256)
def _cached_texture(path):
    return Gdk.Texture.new_from_filename(path)


def thumbnail_texture(path):
    try:
        # Failed reads are not cached: a scan can regenerate an evicted thumbnail.
        return _cached_texture(path) if path else None
    except (GLib.Error, OSError):
        return None
