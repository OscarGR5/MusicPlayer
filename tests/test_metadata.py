import base64
import hashlib
import shutil
import subprocess
from pathlib import Path

import mutagen
import pytest
from mutagen.flac import Picture
from mutagen.id3 import APIC, TALB, TIT2, TPE1
from mutagen.mp4 import MP4Cover

from music_player.artwork import GdkPixbuf, cache_artwork
from music_player.metadata import read_metadata


@pytest.mark.parametrize("extension", ["wav", "mp3", "flac", "m4a", "ogg", "opus"])
def test_real_format_tags_artwork_and_read_only(wav_file, tmp_path, artwork, extension):
    if extension == "wav":
        path = wav_file
    else:
        if not shutil.which("ffmpeg"):
            pytest.skip("ffmpeg is required only to generate optional format fixtures")
        path = tmp_path / f"sample.{extension}"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav_file), str(path)], check=True)
    audio = mutagen.File(path)
    if audio.tags is None:
        audio.add_tags()
    if extension in ("wav", "mp3"):
        audio.tags.add(TIT2(encoding=3, text="Test title"))
        audio.tags.add(TPE1(encoding=3, text="Test artist"))
        audio.tags.add(TALB(encoding=3, text="Test album"))
        audio.tags.add(APIC(encoding=3, mime="image/png", type=3, data=artwork))
    elif extension == "m4a":
        audio.tags.update({"\xa9nam": ["Test title"], "\xa9ART": ["Test artist"],
                           "\xa9alb": ["Test album"], "covr": [MP4Cover(artwork, imageformat=MP4Cover.FORMAT_PNG)]})
    else:
        audio.tags.update({"title": ["Test title"], "artist": ["Test artist"], "album": ["Test album"]})
        picture = Picture()
        picture.type = 3
        picture.mime = "image/png"
        picture.data = artwork
        if extension == "flac":
            audio.add_picture(picture)
        else:
            audio.tags["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
    audio.save()
    before = hashlib.sha256(path.read_bytes()).digest()
    result = read_metadata(path)
    assert (result.title, result.artist, result.album) == ("Test title", "Test artist", "Test album")
    assert result.duration == pytest.approx(1, abs=0.2)
    assert result.artwork == artwork
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_fallback_values_and_corrupt_file(wav_file, tmp_path):
    result = read_metadata(wav_file)
    assert result.title == wav_file.stem
    assert result.artist == "Unknown artist"
    assert result.album == "Unknown album"
    assert result.duration == pytest.approx(1)
    assert result.artwork is None
    invalid = tmp_path / "bad.mp3"
    invalid.write_bytes(b"not an audio file")
    with pytest.raises((mutagen.MutagenError, ValueError)):
        read_metadata(invalid)


def test_thumbnail_sizing_reuse_and_corrupt_artwork(tmp_path, artwork):
    target = cache_artwork(artwork, tmp_path / "covers")
    image = GdkPixbuf.Pixbuf.new_from_file(target)
    assert (image.get_width(), image.get_height()) == (96, 48)
    mtime = Path(target).stat().st_mtime_ns
    assert cache_artwork(artwork, tmp_path / "covers") == target
    assert Path(target).stat().st_mtime_ns == mtime
    assert cache_artwork(b"corrupt image", tmp_path / "covers") is None
    assert cache_artwork(None, tmp_path / "covers") is None
