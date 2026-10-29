import hashlib
import shutil
import threading
from pathlib import Path

from mutagen.id3 import TIT2
from mutagen.wave import WAVE

from music_player.library import LibraryDatabase
from music_player.paths import database_path, thumbnail_directory
from music_player.scanner import discover_audio, scan_library


def scan():
    batches, progress = [], []
    removed, summary = scan_library(database_path(), thumbnail_directory(), threading.Event(), batches.extend, progress.append)
    return batches, removed, summary


def test_recursive_discovery_deduplicates_symlinks(user_directories, wav_file):
    nested = user_directories / "artist" / "album"
    nested.mkdir(parents=True)
    target = nested / "song.WAV"
    shutil.copyfile(wav_file, target)
    (user_directories / "alias.wav").symlink_to(target)
    (nested / "loop").symlink_to(user_directories, target_is_directory=True)
    (nested / "ignored.txt").write_text("not music")
    assert list(discover_audio(user_directories)) == [target]


def test_persistence_reuse_modification_removal_and_no_source_writes(user_directories, wav_file, monkeypatch):
    song = user_directories / "song.wav"
    shutil.copyfile(wav_file, song)
    before = hashlib.sha256(song.read_bytes()).digest()
    initial, removed, summary = scan()
    assert len(initial) == 1
    first_id = initial[0].id
    assert not removed
    assert hashlib.sha256(song.read_bytes()).digest() == before
    with LibraryDatabase(database_path()) as db:
        assert db.songs()[0].id == first_id
        assert len(db.folders()) == 1
        db.configure_default_folder(user_directories)
        assert len(db.folders()) == 1

    from music_player import scanner
    original = scanner.read_metadata
    parsed = []

    def record(path):
        parsed.append(path)
        return original(path)

    monkeypatch.setattr(scanner, "read_metadata", record)
    assert scan()[0] == []
    assert parsed == []
    audio = WAVE(song)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text="Updated title"))
    audio.save()
    updated = scan()[0]
    assert len(updated) == 1
    assert updated[0].title == "Updated title"
    assert updated[0].id == first_id
    assert len(parsed) == 1
    with LibraryDatabase(database_path()) as db:
        assert len(db.songs()) == 1
    song.unlink()
    assert scan()[1] == [first_id]
    with LibraryDatabase(database_path()) as db:
        assert db.songs() == []


def test_invalid_unreadable_and_offline_root_keep_valid_index(user_directories, wav_file, monkeypatch):
    target = user_directories / "song.wav"
    shutil.copyfile(wav_file, target)
    initial = scan()[0]
    (user_directories / "invalid.mp3").write_bytes(b"corrupt")
    assert "1 skipped" in scan()[2]

    def inaccessible(folder, on_error):
        on_error(PermissionError("unreadable directory"))
        return iter(())

    monkeypatch.setattr("music_player.scanner.discover_audio", inaccessible)
    assert scan()[1] == []
    with LibraryDatabase(database_path()) as db:
        assert db.songs() == initial
    user_directories.rename(user_directories.with_name("offline"))
    assert "Folder unavailable" in scan()[2]
    with LibraryDatabase(database_path()) as db:
        assert db.songs() == initial


def test_cancellation_does_not_purge_index(user_directories, wav_file):
    shutil.copyfile(wav_file, user_directories / "song.wav")
    initial = scan()[0]
    cancelled = threading.Event()
    cancelled.set()
    assert scan_library(database_path(), thumbnail_directory(), cancelled, lambda _: None, lambda _: None)[0] == []
    with LibraryDatabase(database_path()) as db:
        assert db.songs() == initial


def test_xdg_paths_are_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    assert database_path() == Path.home() / ".local/share/musicplayer/library.sqlite3"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert database_path() == tmp_path / "musicplayer/library.sqlite3"


def test_deleted_thumbnail_is_regenerated_and_metadata_failure_is_retried(user_directories, wav_file, artwork, monkeypatch):
    from mutagen.id3 import APIC
    from music_player.song_model import thumbnail_texture

    path = user_directories / "cover.wav"
    shutil.copyfile(wav_file, path)
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(APIC(encoding=3, mime="image/png", type=3, data=artwork))
    audio.save()
    initial = scan()[0][0]
    cover = Path(initial.artwork_path)
    cover.unlink()
    assert thumbnail_texture(str(cover)) is None
    updated = scan()[0][0]
    assert updated.id == initial.id
    assert cover.is_file()
    assert thumbnail_texture(str(cover)) is not None
    assert scan()[0] == []
    # A transient read failure must not record the new mtime as successfully parsed.
    path.touch()
    from music_player import scanner
    original = scanner.read_metadata

    def unreadable(path):
        raise PermissionError("temporarily unreadable")

    monkeypatch.setattr(scanner, "read_metadata", unreadable)
    assert "1 skipped" in scan()[2]
    monkeypatch.setattr(scanner, "read_metadata", original)
    assert scan()[0][0].id == initial.id


def test_corrupt_embedded_artwork_keeps_song_metadata(user_directories, wav_file):
    from mutagen.id3 import APIC

    path = user_directories / "bad-cover.wav"
    shutil.copyfile(wav_file, path)
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text="Good title"))
    audio.tags.add(APIC(encoding=3, mime="image/png", type=3, data=b"broken image"))
    audio.save()
    songs, removed, summary = scan()
    assert len(songs) == 1
    assert songs[0].title == "Good title"
    assert songs[0].artwork_path is None
    assert "0 skipped" in summary


def test_batches_are_persisted_before_incremental_delivery(user_directories, wav_file):
    for index in range(40):
        shutil.copyfile(wav_file, user_directories / f"song-{index}.wav")
    batches = []

    def receive(songs):
        batches.append(songs)
        with LibraryDatabase(database_path()) as db:
            persisted_ids = {song.id for song in db.songs()}
        assert {song.id for song in songs} <= persisted_ids

    scan_library(database_path(), thumbnail_directory(), threading.Event(), receive, lambda _: None)
    assert len(batches) >= 2
    assert sum(len(batch) for batch in batches) == 40
    assert len(batches[0]) < 40


def test_sqlite_write_failure_is_not_misreported_as_bad_audio(user_directories, wav_file, monkeypatch):
    import sqlite3
    import pytest

    shutil.copyfile(wav_file, user_directories / "song.wav")

    def fail(*args):
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(LibraryDatabase, "upsert", fail)
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        scan()
