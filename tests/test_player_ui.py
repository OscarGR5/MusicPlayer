from dataclasses import replace
from pathlib import Path
import shutil

import gi
import pytest

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk

from music_player.artwork import cache_artwork
from music_player.audio import AudioPlayer
from test_audio import wait_for
from test_library_ui import open_window

pytestmark = pytest.mark.skipif(not Gtk.init_check(), reason='No graphical display available')


@pytest.fixture
def library(indexed_songs, long_wav_file):
    for song in indexed_songs:
        shutil.copyfile(long_wav_file, song.path)
    return indexed_songs


def test_player_icons_modes_volume_metadata_and_seeking(library, artwork, tmp_path, monkeypatch):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        song = window.sorted.get_item(0).song
        cover = cache_artwork(artwork, tmp_path / 'art')
        window.controller.activate([replace(song, artwork_path=cover)])
        wait_for(lambda: window.controller.state == 'playing')
        bar = window.bar
        assert bar.play.get_icon_name() == 'media-playback-pause-symbolic'
        assert bar.title.get_text() == song.title
        assert song.artist in bar.artist.get_text()
        assert bar.cover.get_paintable() is not None
        bar.play.emit('clicked')
        wait_for(lambda: window.controller.state == 'paused')
        assert bar.play.get_icon_name() == 'media-playback-start-symbolic'
        bar.shuffle.emit('clicked')
        assert window.controller.shuffle and bar.shuffle.get_active()
        bar.repeat.emit('clicked')
        assert window.controller.repeat == 'all' and bar.repeat.get_active()
        bar.repeat.emit('clicked')
        assert window.controller.repeat == 'one' and bar.repeat.get_active()
        assert bar.repeat.get_icon_name() == 'media-playlist-repeat-song-symbolic'
        bar.repeat.emit('clicked')
        assert window.controller.repeat == 'off' and not bar.repeat.get_active()
        bar.volume.set_value(22)
        assert window.player.volume == pytest.approx(0.22)
        bar.mute.emit('clicked')
        assert window.player.muted and bar.mute.get_icon_name() == 'audio-volume-muted-symbolic'
        bar.mute.emit('clicked')
        assert not window.player.muted and window.player.volume == pytest.approx(0.22)
        calls = []
        seek = window.player.seek
        def record(value):
            calls.append(value)
            return seek(value)
        monkeypatch.setattr(window.player, 'seek', record)
        bar.begin_seek()
        for value in (2, 3, 4):
            bar.progress.set_value(value)
        window.controller.tick()
        assert bar.progress.get_value() == 4
        assert calls == []
        bar.finish_seek()
        wait_for(lambda: calls == [4] and not window.player.seeking)
        assert window.player.position == pytest.approx(4, abs=0.05)
        # Keyboard/wheel edits are debounced too.
        for value in (5, 5.5, 6):
            bar.progress.set_value(value)
        wait_for(lambda: calls == [4, 6] and not window.player.seeking)
        assert window.player.position == pytest.approx(6, abs=0.05)
        bar.update_progress()
        assert bar.elapsed.get_text() == '0:06'
        assert bar.total.get_text() == '0:12'
        monkeypatch.setattr(AudioPlayer, 'duration', property(lambda self: None))
        window.controller.current = replace(song, duration=0)
        bar.update_progress()
        assert not bar.progress.get_sensitive()
        assert bar.total.get_text() == '-:--'


def test_ui_queue_snapshot_survives_search_sort_and_refresh(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        window.view.emit('activate', 0)
        wait_for(lambda: window.controller.state == 'playing')
        queue = window.controller.queue
        window._search.set_text(Path(queue[-1].path).stem)
        wait_for(lambda: window.filtered.get_n_items() == 1)
        window.view.sort_by_column(window._title_column, Gtk.SortType.DESCENDING)
        window._update_songs([replace(queue[1], title='Changed later')])
        window.bar.next.emit('clicked')
        wait_for(lambda: window.controller.current_song_id == queue[1].id and window.controller.state == 'playing')
        assert window.controller.queue == queue
        assert window.bar.title.get_text() == queue[1].title
        window.bar.previous.emit('clicked')
        wait_for(lambda: window.controller.current_song_id == queue[0].id)
        window._search.set_text('')
        wait_for(lambda: window.filtered.get_n_items() == 4)
        # Activating again makes a new snapshot of the current view.
        window.view.emit('activate', 0)
        assert window.controller.queue != queue
        assert window.controller.queue == tuple(window.sorted.get_item(i).song for i in range(4))


def test_central_actions_space_editable_guard_and_search_focus(library, monkeypatch):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        window.view.emit('activate', 0)
        wait_for(lambda: window.controller.state == 'playing')
        window.activate_action('win.search', None)
        window._search.set_text('typing with spaces')
        assert not window.shortcuts.space()
        assert window.controller.state == 'playing'
        assert window._search.get_text() == 'typing with spaces'
        window.view.grab_focus()
        assert window.shortcuts.space()
        wait_for(lambda: window.controller.state == 'paused')
        window.activate_action('win.next', None)
        wait_for(lambda: window.controller.current_index == 1 and window.controller.state == 'paused')
        window.activate_action('win.previous', None)
        wait_for(lambda: window.controller.current_index == 0)
        window.activate_action('win.mute', None)
        assert window.player.muted
        opened = []
        monkeypatch.setattr(window, '_open_file', lambda _: opened.append(True))
        window.activate_action('win.open', None)
        assert opened == [True]
        for action in ('next', 'previous', 'mute', 'open', 'search'):
            assert window.get_application().get_accels_for_action('win.' + action)
        assert window.get_application().get_accels_for_action('win.play-pause') == []


def test_space_defers_to_focused_native_controls(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        window.view.emit('activate', 0)
        wait_for(lambda: window.controller.state == 'playing')
        for button in (window.bar.play, window.bar.shuffle, window.bar.next,
                       window.bar.repeat, window.bar.mute, window._rescan,
                       window._create_playlist):
            button.grab_focus()
            assert not window.shortcuts.space(), button.get_tooltip_text()
            assert window.controller.state == 'playing'
        window.sidebar.select_row(window._statistics_row)
        window.statistics.selector.grab_focus()
        assert not window.shortcuts.space()


@pytest.mark.parametrize('dragging', [False, True])
@pytest.mark.parametrize('terminal', ['stop', 'error'])
def test_pending_seek_is_cancelled_when_playback_ends(library, dragging, terminal):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        window.view.emit('activate', 0)
        wait_for(lambda: window.controller.state == 'playing')
        bar = window.bar
        if dragging:
            bar.begin_seek()
        bar.progress.set_value(4)
        if terminal == 'stop':
            window.controller.stop()
        else:
            window.player.stop()
            window.controller._audio_event('error', 'Decoder failed')
        assert not bar._dragging
        assert bar._seek_source is None
        assert not bar.progress.get_sensitive()
        bar.finish_seek()
        assert bar._seek_source is None


def test_file_dialog_cancel_open_and_close_cleanup(library, monkeypatch):
    # Exercise asynchronous ownership without driving the user's portal. The
    # native pre-presentation shutdown case has a separate standalone reproducer.
    pending = []
    monkeypatch.setattr(Gtk.FileDialog, 'open', lambda dialog, parent, cancel, callback:
                        pending.append((dialog, cancel, callback)))

    def cancelled(dialog, result):
        raise GLib.Error.new_literal(Gtk.DialogError.quark(), 'Cancelled', Gtk.DialogError.CANCELLED)

    monkeypatch.setattr(Gtk.FileDialog, 'open_finish', cancelled)
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        window._open_file(None)
        chooser = window._chooser
        assert isinstance(chooser, Gtk.FileDialog)
        window._open_file(None)
        assert window._chooser is chooser
        assert len(pending) == 1
        window._chooser_cancel.cancel()
        pending[0][2](chooser, None)
        assert window._chooser is None
        window._open_file(None)
        chooser = window._chooser
        cancellation = window._chooser_cancel
        window.close()
        assert window._chooser is None
        assert cancellation.is_cancelled()


def test_file_dialog_result_errors_and_late_results_after_close(library, monkeypatch):
    class Result:
        def __init__(self, value):
            self.value = value

        def open_finish(self, result):
            if isinstance(self.value, Exception):
                raise self.value
            return self.value

    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        song = window.sorted.get_item(0).song
        window._file_response(Result(Gio.File.new_for_path(song.path)), None)
        wait_for(lambda: window.controller.state == 'playing')
        assert window.controller.current_song_id == song.id
        before = window._status.get_text()
        for code in (Gtk.DialogError.CANCELLED, Gtk.DialogError.DISMISSED):
            error = GLib.Error.new_literal(Gtk.DialogError.quark(), 'Cancelled', code)
            window._file_response(Result(error), None)
            assert window._status.get_text() == before
        window._file_response(Result(Gio.File.new_for_uri('https://example.com/audio.wav')), None)
        assert 'Only local files' in window._status.get_text()
        error = GLib.Error.new_literal(Gtk.DialogError.quark(), 'Chooser failed', Gtk.DialogError.FAILED)
        window._file_response(Result(error), None)
        assert 'Chooser failed' in window._status.get_text()
        opened = []
        monkeypatch.setattr(window.controller, 'open_file', opened.append)
        window.close()
        window._file_response(Result(Gio.File.new_for_path(song.path)), None)
        assert not opened


@pytest.mark.parametrize('size', [(1040, 640), (720, 480)])
@pytest.mark.parametrize('dark', [False, True])
def test_player_bar_stays_centered_and_inside_window(library, size, dark):
    settings = Gtk.Settings.get_default()
    previous = settings.get_property('gtk-application-prefer-dark-theme')
    settings.set_property('gtk-application-prefer-dark-theme', dark)
    try:
        with open_window(size) as window:
            wait_for(lambda: window.bar.get_width() > 0)
            bar = window.bar
            for child in (bar.cover, bar.title, bar.progress, bar.repeat, bar.volume, bar.mute):
                valid, bounds = child.compute_bounds(bar)
                assert valid and bounds.get_width() > 0
                assert bounds.get_x() >= -0.5
                assert bounds.get_x() + bounds.get_width() <= bar.get_width() + 0.5
            valid, center = bar.layout.get_center_widget().compute_bounds(bar)
            assert valid
            assert center.get_x() + center.get_width() / 2 == pytest.approx(bar.get_width() / 2, abs=1)
    finally:
        settings.set_property('gtk-application-prefer-dark-theme', previous)
