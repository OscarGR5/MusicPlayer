import shutil
import itertools
import threading
import time
from contextlib import contextmanager

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from music_player.audio import Gst
from music_player.library import LibraryDatabase
from music_player.paths import database_path
from music_player.song_model import format_duration
from music_player.window import MusicPlayerWindow
from test_audio import wait_for
from test_library import scan

pytestmark = pytest.mark.skipif(not Gtk.init_check(), reason="No graphical display available")
_application_ids = itertools.count()


@contextmanager
def open_window():
    app = Gtk.Application(application_id=f"io.github.OscarGR5.MusicPlayer.LibraryTests.Test{next(_application_ids)}",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    window = MusicPlayerWindow(app)
    sink = Gst.ElementFactory.make("fakesink")
    sink.set_property("sync", True)
    window.player.pipeline.set_property("audio-sink", sink)
    window.present()
    try:
        yield window
    finally:
        window.close()
        if window.scanner and window.scanner.thread:
            window.scanner.thread.join(timeout=5)
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)


def test_search_sort_all_columns_activate_and_playback_controls(user_directories, wav_file):
    from mutagen.id3 import TALB, TIT2, TPE1
    from mutagen.wave import WAVE

    for filename, title, artist, album in (("first.wav", "Zebra", "Artist A", "Album Z"),
                                           ("second.wav", "alpha", "Artist Z", "Album A")):
        path = user_directories / filename
        shutil.copyfile(wav_file, path)
        audio = WAVE(path)
        audio.add_tags()
        for frame in (TIT2(encoding=3, text=title), TPE1(encoding=3, text=artist), TALB(encoding=3, text=album)):
            audio.tags.add(frame)
        audio.save()
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        assert window.store.get_n_items() == 2
        assert window.sorted.get_item(0).song.title == "alpha"
        for column, field, expected in ((1, "title", "alpha"), (2, "artist", "Artist A"), (3, "album", "Album A")):
            window.view.sort_by_column(window.view.get_columns().get_item(column), Gtk.SortType.ASCENDING)
            assert getattr(window.sorted.get_item(0).song, field) == expected
            window.view.sort_by_column(window.view.get_columns().get_item(column), Gtk.SortType.DESCENDING)
            assert getattr(window.sorted.get_item(0).song, field) != expected
        # Ensure duration is sorted numerically rather than as display strings.
        from dataclasses import replace
        songs = [item.song for item in window._items.values()]
        window._update_songs([replace(songs[0], duration=61), replace(songs[1], duration=600)])
        window.view.sort_by_column(window.view.get_columns().get_item(4), Gtk.SortType.ASCENDING)
        assert window.sorted.get_item(0).song.duration == 61
        window.view.sort_by_column(window.view.get_columns().get_item(4), Gtk.SortType.DESCENDING)
        assert window.sorted.get_item(0).song.duration == 600
        window._search.set_text("ZEBRA album z")
        wait_for(lambda: window.filtered.get_n_items() == 1)
        assert window.sorted.get_item(0).song.title == "Zebra"
        window.view.emit("activate", 0)
        wait_for(lambda: window._status.get_text() == "Playing")
        assert window.sorted.get_item(0).playing
        assert window.player.path == user_directories / "first.wav"
        window._controls[1].emit("clicked")
        wait_for(lambda: window._status.get_text() == "Paused")
        window._controls[0].emit("clicked")
        wait_for(lambda: window._status.get_text() == "Playing")
        playing = window.sorted.get_item(0)
        # Failed selection must preserve the marker for audio that is still playing.
        from dataclasses import replace
        missing = replace(playing.song, path=str(user_directories / "missing.wav"))
        window._perform(lambda: window._load_and_play(missing))
        assert window._status.get_text().startswith("Error:")
        assert playing.playing
        assert window.player.pipeline.get_state(0).state == Gst.State.PLAYING
        window._controls[2].emit("clicked")
        assert window._status.get_text() == "Stopped"
        assert not window.sorted.get_item(0).playing
        window.player.path.unlink()
        window.view.emit("activate", 0)
        assert window._status.get_text().startswith("Error:")
        window._search.set_text("no such song")
        wait_for(lambda: window.filtered.get_n_items() == 0)
        assert window._empty.get_visible()
        assert "No matching" in window._empty.get_text()
    assert format_duration(3661) == "1:01:01"


def test_existing_index_visible_while_worker_runs_and_main_thread_stays_responsive(user_directories, wav_file, monkeypatch):
    path = user_directories / "song.wav"
    shutil.copyfile(wav_file, path)
    scan()
    # Force a metadata refresh, retaining the already persisted row at startup.
    path.touch()
    from music_player import scanner
    original = scanner.read_metadata
    release = threading.Event()
    started = threading.Event()
    worker_threads = []
    update_threads = []
    update = MusicPlayerWindow._update_songs

    def delayed(path):
        worker_threads.append(threading.get_ident())
        started.set()
        assert release.wait(5)
        return original(path)

    def record_updates(self, songs):
        update_threads.append(threading.get_ident())
        return update(self, songs)

    monkeypatch.setattr(scanner, "read_metadata", delayed)
    monkeypatch.setattr(MusicPlayerWindow, "_update_songs", record_updates)
    try:
        with open_window() as window:
            assert window.store.get_n_items() == 1
            wait_for(started.is_set)
            assert window.scanner.running
            assert not window.scanner.start()
            assert not window._rescan.get_sensitive()
            ticks = []

            def tick():
                ticks.append(1)
                if len(ticks) >= 4:
                    release.set()
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE

            GLib.timeout_add(10, tick)
            wait_for(lambda: not window.scanner.running)
            assert len(ticks) == 4
            assert worker_threads and all(id_ != threading.get_ident() for id_ in worker_threads)
            assert update_threads and all(id_ == threading.get_ident() for id_ in update_threads)
            assert window._rescan.get_sensitive()
            assert window.store.get_n_items() == 1
            # A background refresh keeps the persistent song ID and selection.
            window.selection.set_selected(0)
            selected_id = window.selection.get_selected_item().song.id
            path.touch()
            window._rescan.emit("clicked")
            wait_for(lambda: not window.scanner.running)
            assert window.selection.get_selected_item().song.id == selected_id
            with LibraryDatabase(database_path()) as db:
                assert db.songs()[0].id == selected_id
    finally:
        release.set()


def test_close_cancels_scan_without_waiting_for_metadata(user_directories, wav_file, monkeypatch):
    shutil.copyfile(wav_file, user_directories / "song.wav")
    started, release = threading.Event(), threading.Event()
    from music_player import scanner
    original = scanner.read_metadata

    def delayed(path):
        started.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr(scanner, "read_metadata", delayed)
    try:
        with open_window() as window:
            wait_for(started.is_set)
            before = time.monotonic()
            window.close()
            assert time.monotonic() - before < 0.5
            assert window.scanner.cancelled.is_set()
            assert window.player.pipeline.get_state(0).state == Gst.State.NULL
            release.set()
            window.scanner.thread.join(timeout=5)
            assert not window.scanner.thread.is_alive()
    finally:
        release.set()


def test_scan_error_is_visible_and_rescan_is_enabled(monkeypatch):
    def fail(*args):
        raise OSError("disk is full")

    monkeypatch.setattr("music_player.scanner.scan_library", fail)
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        assert "Library scan failed: disk is full" in window._scan_label.get_text()
        assert window._rescan.get_sensitive()
