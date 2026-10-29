import os
import shutil
import sqlite3
import threading
from datetime import date
from pathlib import Path

import gi
import pytest

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk

from music_player.paths import database_path
from music_player.statistics_ui import StatisticsPage
from test_audio import wait_for
from test_library import scan
from test_library_ui import open_window
from test_statistics import add_session, dump

pytestmark = pytest.mark.skipif(not Gtk.init_check(), reason='No graphical display available')


@pytest.fixture
def library(user_directories, long_wav_file):
    for index in range(12):
        shutil.copyfile(long_wav_file, user_directories / f'Example song {index:02}.wav')
    songs, _, _ = scan()
    return songs


def enter(window):
    window.sidebar.select_row(window._statistics_row)
    wait_for(lambda: window.statistics.data is not None and window.statistics._future is None)
    return window.statistics


def seed(window, library):
    for index, song in enumerate(library):
        add_session(database_path(), song.id, f'demo-{index}', start=f'2026-10-{index+1:02}T12:00:00+00:00',
                    artist=f'Example artist {index:02}', album=f'Example album {index:02}', seconds=60 + index * 10)


def test_navigation_preserves_library_playlist_queue_and_player(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        playlist = window.playlists.create('Saved playlist', [song.id for song in library])
        window._refresh_sidebar()
        window._show_view(playlist.id)
        window._playlist_play.emit('clicked')
        wait_for(lambda: window.controller.state == 'playing')
        window.controller.toggle_shuffle()
        window.controller.cycle_repeat()
        window._search.set_text('Example song 00')
        wait_for(lambda: window.filtered.get_n_items() == 1)
        queue, current = tuple(window.controller.queue), window.controller.current_song_id
        store, selected = window.store, window._selected_songs()
        page = enter(window)
        assert window.sidebar.get_selected_row() is window._statistics_row
        assert window._content_stack.get_visible_child() is page
        assert window.bar.get_mapped() and window.bar.get_height() > 0
        assert not window.view.get_mapped()
        window._refresh_sidebar()
        assert window.sidebar.get_selected_row() is window._statistics_row
        window.sidebar.select_row(window._playlist_rows[playlist.id])
        assert page._timer is None
        assert window._search.get_text() == 'Example song 00'
        assert window.store is store and window._selected_songs() == selected
        enter(window)
        window.sidebar.select_row(window._all_row)
        assert window._heading.get_text() == 'All Songs'
        assert window.view.get_mapped()
        assert tuple(window.controller.queue) == queue
        assert window.controller.current_song_id == current and window.controller.state == 'playing'
        assert window.controller.shuffle and window.controller.repeat == 'all'


def test_summary_top_ten_selector_chart_and_read_only_refresh(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        seed(window, library)
        before = dump(database_path())
        page = enter(window)
        assert [widget.get_text() for widget in page.metrics] == ['23 min', '12', '12']
        assert len(page.data.songs) == len(page.data.artists) == len(page.data.albums) == 10
        assert page.rankings['Top Songs'].get_first_child().get_next_sibling() is not None
        assert len(page.chart.values) == 12
        assert 'Last 12 Months' in page.chart_heading.get_text()
        page.selector.set_selected(page._periods.index('2026-10'))
        wait_for(lambda: page.data.period == '2026-10')
        assert len(page.chart.values) == 31
        assert page.chart.values[0].seconds == 60
        page.activity_details.set_expanded(True)
        assert page.activity_rows.get_first_child().get_text().endswith('(60.00 s)')
        page.refresh()
        wait_for(lambda: page._future is None)
        assert page.period == '2026-10'
        assert dump(database_path()) == before
        page.selector.grab_focus()
        assert window.shortcuts.space() is False


def test_empty_history_and_empty_month_keep_selection(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        page = enter(window)
        assert page.message.get_text().startswith('No listening history yet.')
        assert [widget.get_text() for widget in page.metrics] == ['0 s', '0', '0']
        month = date.today().strftime('%Y-%m')
        page.selector.set_selected(page._periods.index(month))
        wait_for(lambda: page.data.period == month)
        assert page.message.get_text() == 'No listening activity in this month.'
        page.refresh()
        wait_for(lambda: page._future is None)
        assert page.period == month
        assert not any(item.seconds for item in page.chart.values)


def test_automatic_refresh_reuses_unchanged_rows_and_stops_hidden(library, monkeypatch):
    monkeypatch.setattr(StatisticsPage, 'REFRESH_SECONDS', 1)
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        page = enter(window)
        original = page.rankings['Top Songs'].get_first_child()
        page.refresh()
        wait_for(lambda: page._future is None)
        assert page.rankings['Top Songs'].get_first_child() is original
        add_session(database_path(), library[0].id, 'new', seconds=7, qualified=False)
        wait_for(lambda: page.data.seconds == 7)
        assert page.metrics[0].get_text() == '7 s'
        window.sidebar.select_row(window._all_row)
        assert page._timer is None
        old_data = page.data
        add_session(database_path(), library[0].id, 'hidden', seconds=5, qualified=False)
        page.refresh()
        assert page._future is None and page.data is old_data
        enter(window)
        wait_for(lambda: page.data.seconds == 12)


def test_temporary_read_failure_recovers_without_playback_change(library, monkeypatch):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        page = enter(window)
        original = page.reader.read
        def fail(*args, **kwargs):
            raise sqlite3.OperationalError('database is locked')
        monkeypatch.setattr(page.reader, 'read', fail)
        page.refresh()
        wait_for(lambda: 'Could not read' in page.message.get_text())
        monkeypatch.setattr(page.reader, 'read', original)
        page.refresh()
        wait_for(lambda: page._future is None)
        assert 'No listening history' in page.message.get_text()
        assert window.bar.get_mapped()


def test_worker_does_not_block_gtk_and_discards_stale_period(library, monkeypatch):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        seed(window, library)
        page = enter(window)
        original = page.reader.read
        gate, started = threading.Event(), threading.Event()
        threads = []
        def delayed(*args):
            threads.append(threading.get_ident())
            started.set()
            assert gate.wait(3)
            return original(*args)
        monkeypatch.setattr(page.reader, 'read', delayed)
        try:
            page.refresh()
            wait_for(started.is_set)
            page.selector.set_selected(page._periods.index('2026-10'))
            received = []
            GLib.idle_add(lambda: received.append(True) and False)
            wait_for(lambda: received)
            assert threads[0] != threading.get_ident()
        finally:
            gate.set()
        wait_for(lambda: page.data.period == '2026-10' and page._future is None)
        assert page.period == '2026-10' and len(page.chart.values) == 31


def capture(window, path):
    """Optional synthetic-only screenshots, kept outside the repository."""
    from gi.repository import Graphene
    drawn = []
    window.add_tick_callback(lambda *_: drawn.append(True) and False)
    wait_for(lambda: drawn)
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, window.get_width(), window.get_height())
    node = snapshot.to_node()
    if node is None:
        ready = []
        window.add_tick_callback(lambda *_: ready.append(True) and False)
        wait_for(lambda: ready)
        snapshot = Gtk.Snapshot.new()
        paintable.snapshot(snapshot, window.get_width(), window.get_height())
        node = snapshot.to_node()
    bounds = Graphene.Rect().init(0, 0, window.get_width(), window.get_height())
    texture = window.get_renderer().render_texture(node, bounds)
    texture.save_to_png(str(path))


@pytest.mark.parametrize('dark', [False, True])
@pytest.mark.parametrize('size', [(1040, 640), (720, 480)])
def test_layout_themes_and_scroll_player(library, size, dark):
    settings = Gtk.Settings.get_default()
    original = settings.get_property('gtk-application-prefer-dark-theme')
    settings.set_property('gtk-application-prefer-dark-theme', dark)
    try:
        with open_window(size) as window:
            wait_for(lambda: not window.scanner.running)
            seed(window, library)
            page = enter(window)
            # get_width/get_height exclude CSS borders. GTK 4.14's window
            # theme has a 5 px border, so compare the window's drawing bounds.
            try:
                wait_for(lambda: page.get_width() > 0 and
                         window.compute_bounds(window)[1].get_width() == size[0])
            except AssertionError:
                _, bounds = window.compute_bounds(window)
                raise AssertionError(
                    f'Requested {size}, window bounds '
                    f'{bounds.get_width()}x{bounds.get_height()}, content '
                    f'{window.get_child().get_width()}x{window.get_child().get_height()}, '
                    f'titlebar {window.get_titlebar().get_height()}'
                ) from None
            valid, bounds = window.compute_bounds(window)
            assert valid and bounds.get_height() == size[1]
            assert page.get_hadjustment().get_upper() <= page.get_width()
            assert page.get_vadjustment().get_upper() > page.get_height()
            assert window.bar.get_mapped()
            screenshot_dir = os.environ.get('MUSICPLAYER_UI_CAPTURE_DIR')
            if screenshot_dir:
                directory = Path(screenshot_dir)
                directory.mkdir(parents=True, exist_ok=True)
                capture(window, directory / f'{size[0]}-{size[1]}-{"dark" if dark else "light"}-top.png')
            page.selector.set_selected(page._periods.index('2026-10'))
            wait_for(lambda: page.data.period == '2026-10')
            # Wait for the period change to finish allocating before scrolling.
            frame = []
            page.add_tick_callback(lambda *_: frame.append(True) and False)
            wait_for(lambda: frame)
            adjustment = page.get_vadjustment()
            adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
            wait_for(lambda: abs(adjustment.get_value() - (adjustment.get_upper() - adjustment.get_page_size())) < 1)
            wait_for(lambda: page.chart.compute_bounds(page)[1].get_y() < page.get_height())
            assert window.bar.get_mapped() and window.bar.get_height() > 0
            if screenshot_dir:
                # Let a frame update before capturing the chart at scroll end.
                drawn = []
                page.chart.add_tick_callback(lambda *_: drawn.append(True) and False)
                wait_for(lambda: drawn)
                capture(window, directory / f'{size[0]}-{size[1]}-{"dark" if dark else "light"}-chart.png')
    finally:
        settings.set_property('gtk-application-prefer-dark-theme', original)
