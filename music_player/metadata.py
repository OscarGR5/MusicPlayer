"""Read audio metadata and embedded artwork without modifying the source file."""

import base64
import binascii
import math
from dataclasses import dataclass
from pathlib import Path

import mutagen
from mutagen.flac import Picture

SUPPORTED_EXTENSIONS = frozenset({".mp3", ".flac", ".m4a", ".ogg", ".oga", ".opus", ".wav"})
MAX_ARTWORK_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class Metadata:
    title: str
    artist: str
    album: str
    duration: float
    artwork: bytes | None = None


def _text(tags, keys, fallback):
    for key in keys:
        try:
            value = tags.get(key)
        except ValueError:
            # Vorbis comments reject keys outside their ASCII key alphabet.
            continue
        if hasattr(value, "text"):
            value = value.text
        if isinstance(value, (tuple, list)):
            value = "; ".join(str(part).strip() for part in value if str(part).strip())
        if value is not None and str(value).strip():
            return str(value).strip()
    return fallback


def embedded_artwork(audio):
    tags = audio.tags or {}
    pictures = getattr(audio, "pictures", [])
    if hasattr(tags, "getall"):
        pictures = pictures or tags.getall("APIC")
    if pictures:
        picture = next((item for item in pictures if item.type == 3), pictures[0])
        data = picture.data
        return bytes(data) if len(data) <= MAX_ARTWORK_BYTES else None
    covers = tags.get("covr", [])
    if covers:
        return bytes(covers[0]) if len(covers[0]) <= MAX_ARTWORK_BYTES else None
    # Vorbis/Opus: FLAC picture blocks, with legacy base64 COVERART fallback.
    for key in ("metadata_block_picture", "coverart"):
        for value in tags.get(key, []):
            try:
                if len(value) > MAX_ARTWORK_BYTES * 2:
                    continue
                decoded = base64.b64decode(value, validate=True)
                data = Picture(decoded).data if key == "metadata_block_picture" else decoded
                if len(data) <= MAX_ARTWORK_BYTES:
                    return bytes(data)
            except (ValueError, binascii.Error, mutagen.MutagenError):
                continue
    return None


def read_metadata(path):
    path = Path(path)
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("Unsupported or invalid audio file")
    tags = audio.tags or {}
    duration = float(getattr(audio.info, "length", 0))
    if not math.isfinite(duration) or duration < 0:
        duration = 0
    return Metadata(
        title=_text(tags, ("TIT2", "\xa9nam", "title"), path.stem),
        artist=_text(tags, ("TPE1", "\xa9ART", "artist"), "Unknown artist"),
        album=_text(tags, ("TALB", "\xa9alb", "album"), "Unknown album"),
        duration=duration,
        artwork=embedded_artwork(audio),
    )
