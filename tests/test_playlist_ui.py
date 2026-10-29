import hashlib
import shutil
import gc
import weakref
import sqlite3
from pathlib import Path

import gi
import pytest

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk

from music_player.library import LibraryDatabase
from music_player.paths import database_path
from music_player.window import MusicPlayerWindow
from test_audio import wait_for
from test_library_ui import open_window

pytestmark = pytest.mark.skipif(not Gtk.init_check(), reason='No graphical display available')


@pytest.fixture
def library(indexed_songs, long_wav_file):
    for song in indexed_songs:
        shutil.copyfile(long_wav_file, song.path)
    return indexed_songs


def dialog(window):
    assert len(window._dialogs) == 1
    return next(iter(window._dialogs))


def ids(window):
    return [window.sorted.get_item(index).song.id for index in range(window.sorted.get_n_items())]


def members(window, playlist_id):
    return [song.id for song in window.playlists.songs(playlist_id)]


def test_replaced_rows_are_released_and_rebound_context_menu_works(library, monkeypatch):
    rows = weakref.WeakSet()
    attach = MusicPlayerWindow._attach_song_menu

    def record(self, widget, item):
        rows.add(item)
        attach(self, widget, item)

    monkeypatch.setattr(MusicPlayerWindow, '_attach_song_menu', record)
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        playlist = window.playlists.create('Row lifecycle', [song.id for song in library[:2]])
        window._refresh_sidebar()
        gc.collect()
        initial = len(rows)
        assert initial > 0
        for _ in range(12):
            window._show_view(playlist.id)
            window._show_view(None)
        gc.collect()
        # The same final model needs at most the original live rows. Checking
        # object lifetime avoids machine-dependent RSS or timing assertions.
        assert len(rows) <= initial
        cell = next(item.get_child().get_parent() for item in rows
                    if item.get_item() is not None and item.get_position() == 0)
        # Exercise the installed gesture callback after many model replacements.
        # This is callback verification, not a physical pointer-input test.
        calls = []
        monkeypatch.setattr(window, '_show_song_menu', lambda *args: calls.append(args))
        cell._song_context_gesture.emit('pressed', 1, 3.0, 4.0)
        assert calls and calls[0][0] == 0


def test_native_create_validation_sidebar_rename_and_confirmed_delete(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        window._create_playlist.emit('clicked')
        create = dialog(window)
        create.name_entry.set_text('   ')
        create.response(Gtk.ResponseType.ACCEPT)
        assert create.error_label.get_visible()
        assert create.get_visible()
        create.name_entry.set_text('x' * 81)
        create.response(Gtk.ResponseType.ACCEPT)
        assert '80' in create.error_label.get_text()
        create.name_entry.set_text('  Test playlist  ')
        create.response(Gtk.ResponseType.ACCEPT)
        playlist = window.playlists.list()[0]
        assert playlist.name == 'Test playlist'
        assert playlist.id in window._playlist_rows
        assert not window._dialogs
        window._name_dialog()
        duplicate = dialog(window)
        duplicate.name_entry.set_text('TEST PLAYLIST')
        duplicate.response(Gtk.ResponseType.ACCEPT)
        assert duplicate.get_visible() and 'already exists' in duplicate.error_label.get_text()
        duplicate.response(Gtk.ResponseType.CANCEL)
        window.playlists.add(playlist.id, [library[2].id, library[0].id])
        window.sidebar.select_row(window._playlist_rows[playlist.id])
        assert window._playlist_id == playlist.id
        assert window._heading.get_text() == 'Test playlist'
        assert ids(window) == [library[2].id, library[0].id]
        window.activate_action('win.playlist-rename', GLib.Variant('x', playlist.id))
        rename = dialog(window)
        assert rename.name_entry.get_text() == 'Test playlist'
        rename.name_entry.set_text('Renamed')
        rename.response(Gtk.ResponseType.ACCEPT)
        assert window._heading.get_text() == 'Renamed'
        assert members(window, playlist.id) == [library[2].id, library[0].id]
        window.activate_action('win.playlist-delete', GLib.Variant('x', playlist.id))
        dialog(window).response(Gtk.ResponseType.CANCEL)
        assert window.playlists.get(playlist.id).name == 'Renamed'
        window.activate_action('win.playlist-delete', GLib.Variant('x', playlist.id))
        dialog(window).response(Gtk.ResponseType.ACCEPT)
        assert window._playlist_id is None
        assert window._heading.get_text() == 'All Songs'
        assert window.sidebar.get_selected_row() == window._all_row
        assert playlist.id not in window._playlist_rows
        assert len(ids(window)) == 4
        assert all(Path(song.path).is_file() for song in library)


def test_context_add_multiselection_visible_order_and_unselected_right_click(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        first = window.playlists.create('First')
        second = window.playlists.create('Second')
        window._refresh_sidebar()
        assert isinstance(window.selection, Gtk.MultiSelection)
        window.view.sort_by_column(window._title_column, Gtk.SortType.DESCENDING)
        order = ids(window)
        window.selection.select_item(2, True)
        window.selection.select_item(0, False)
        menu = window._show_song_menu(2)
        assert tuple(window._context_ids) == (order[0], order[2])
        assert [song.id for song in window._selected_songs()] == [order[0], order[2]]
        submenu = menu.get_item_link(0, 'submenu')
        assert submenu.get_n_items() == 2
        assert window._popover.get_visible()
        window.activate_action('win.playlist-add', GLib.Variant('x', first.id))
        assert members(window, first.id) == [order[0], order[2]]
        window.activate_action('win.playlist-add', GLib.Variant('x', first.id))
        assert members(window, first.id) == [order[0], order[2]]
        window._show_song_menu(1)
        assert window._context_ids == (order[1],)
        assert [song.id for song in window._selected_songs()] == [order[1]]
        window.activate_action('win.playlist-add', GLib.Variant('x', second.id))
        assert members(window, second.id) == [order[1]]
        # GTK's range selection model supplies Shift selection without custom key handlers.
        window.selection.select_range(0, 3, True)
        window._show_song_menu(1)
        assert window._context_ids == tuple(order[:3])
        window.activate_action('win.playlist-add', GLib.Variant('x', second.id))
        assert members(window, second.id) == [order[1], order[0], order[2]]


def test_create_from_empty_playlist_submenu_adds_selection_atomically(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        window.selection.select_range(0, 2, True)
        selected = ids(window)[:2]
        menu = window._show_song_menu(0)
        submenu = menu.get_item_link(0, 'submenu')
        assert submenu.get_item_attribute_value(0, 'label', GLib.VariantType.new('s')).get_string() == 'Create Playlist…'
        window.activate_action('win.playlist-create', None)
        create = dialog(window)
        create.name_entry.set_text('New from songs')
        create.response(Gtk.ResponseType.ACCEPT)
        playlist = window.playlists.list()[0]
        assert members(window, playlist.id) == selected
        assert playlist.id in window._playlist_rows


def test_empty_playlist_search_order_moves_remove_and_restore_all_sort(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        window.view.sort_by_column(window._title_column, Gtk.SortType.DESCENDING)
        all_order = ids(window)
        playlist = window.playlists.create('Ordered')
        window._refresh_sidebar()
        window.sidebar.select_row(window._playlist_rows[playlist.id])
        assert window._empty.get_visible()
        assert 'playlist is empty' in window._empty.get_text()
        assert window._playlist_count.get_text() == '0 songs'
        assert not window._playlist_play.get_sensitive()
        saved = [all_order[2], all_order[0], all_order[1]]
        window.playlists.add(playlist.id, saved)
        window._reload_view()
        assert ids(window) == saved
        assert window.view.get_sorter().get_primary_sort_column() is None
        assert all(column.get_sorter() is not None for column in window._column_sorters)
        assert not window._saved_order.get_visible()
        assert window._playlist_count.get_text() == '3 songs'
        song = window.sorted.get_item(1).song
        window._search.set_text(song.title)
        wait_for(lambda: window.filtered.get_n_items() == 1)
        window._show_song_menu(0)
        assert window.lookup_action('playlist-up').get_enabled()
        assert window.lookup_action('playlist-down').get_enabled()
        window.activate_action('win.playlist-up', None)
        assert members(window, playlist.id) == [saved[1], saved[0], saved[2]]
        assert window._selected_songs()[0].id == saved[1]
        window._show_song_menu(0)
        assert not window.lookup_action('playlist-up').get_enabled()
        assert window.lookup_action('playlist-down').get_enabled()
        window.activate_action('win.playlist-down', None)
        assert members(window, playlist.id) == saved
        window._search.set_text('')
        wait_for(lambda: window.filtered.get_n_items() == 3)
        window._show_song_menu(2)
        assert not window.lookup_action('playlist-down').get_enabled()
        window.selection.select_item(0, False)
        window._show_song_menu(2)
        assert not window.lookup_action('playlist-up').get_enabled()
        assert not window.lookup_action('playlist-down').get_enabled()
        before = {song.path: hashlib.sha256(Path(song.path).read_bytes()).digest() for song in library}
        window.activate_action('win.playlist-remove', None)
        assert ids(window) == [saved[1]]
        assert window._playlist_count.get_text() == '1 song'
        window.sidebar.select_row(window._all_row)
        assert ids(window) == all_order
        assert all(column.get_sorter() is not None for column in window._column_sorters)
        assert {song.path: hashlib.sha256(Path(song.path).read_bytes()).digest() for song in library} == before


def test_playlist_play_ignores_search_double_click_uses_visible_snapshot_and_modes(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        saved = [library[2].id, library[0].id, library[1].id]
        playlist = window.playlists.create('Play', saved)
        window._refresh_sidebar()
        window._show_view(playlist.id)
        window._search.set_text(Path(library[0].path).stem)
        wait_for(lambda: window.filtered.get_n_items() == 1)
        window._playlist_play.emit('clicked')
        wait_for(lambda: window.controller.state == 'playing')
        assert [song.id for song in window.controller.queue] == saved
        assert window.controller.current_song_id == saved[0]
        window.controller.toggle_shuffle()
        window.controller.cycle_repeat()
        window.view.emit('activate', 0)
        assert [song.id for song in window.controller.queue] == [library[0].id]
        assert window.controller.shuffle and window.controller.repeat == 'all'
        assert window.bar.get_visible()


def test_edit_remove_delete_and_switch_views_leave_active_queue_unchanged(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        saved = [library[2].id, library[0].id, library[1].id]
        playlist = window.playlists.create('Snapshot', saved)
        window._refresh_sidebar()
        window._show_view(playlist.id)
        window._playlist_play.emit('clicked')
        wait_for(lambda: window.controller.state == 'playing')
        queue = window.controller.queue
        window._show_song_menu(0)
        window.activate_action('win.playlist-remove', None)
        assert window.controller.queue == queue and window.controller.current_song_id == saved[0]
        assert window.controller.state == 'playing'
        window.playlists.move(playlist.id, saved[2], -1)
        window.playlists.add(playlist.id, [library[3].id])
        window._reload_view()
        window._show_view(None)
        window._search.set_text('no matches')
        window._delete_dialog(playlist.id).response(Gtk.ResponseType.ACCEPT)
        assert window.controller.queue == queue
        assert window.controller.state == 'playing'
        window.bar.next.emit('clicked')
        wait_for(lambda: window.controller.current_song_id == saved[1])
        assert window.controller.queue == queue


def test_missing_rows_remain_visible_unplayable_and_restore_after_scan(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        playlist = window.playlists.create('Missing', [song.id for song in library])
        window._refresh_sidebar()
        path = Path(library[0].path)
        contents = path.read_bytes()
        path.unlink()
        with LibraryDatabase(database_path()) as db:
            db.remove([library[0].id])
        window._show_view(playlist.id)
        assert len(ids(window)) == 4
        assert not window.sorted.get_item(0).song.available
        window.view.emit('activate', 0)
        assert window.controller.current is None
        window.selection.select_item(0, True)
        assert not window.bar.play.get_sensitive()
        window._playlist_play.emit('clicked')
        wait_for(lambda: window.controller.state == 'playing')
        assert window.controller.current_song_id == library[1].id
        assert [song.id for song in window.controller.queue] == [song.id for song in library]
        path.write_bytes(contents)
        window._start_scan()
        wait_for(lambda: not window.scanner.running)
        assert window.sorted.get_item(0).song.id == library[0].id
        assert window.sorted.get_item(0).song.available
        assert members(window, playlist.id) == [song.id for song in library]


def test_playlist_sidebar_and_order_persist_after_window_restart(library):
    with open_window() as first:
        wait_for(lambda: not first.scanner.running)
        playlist = first.playlists.create('Persist', [library[3].id, library[1].id])
        first.playlists.rename(playlist.id, 'Persist renamed')
        first.playlists.move(playlist.id, library[1].id, -1)
    with open_window() as second:
        assert playlist.id in second._playlist_rows
        second.sidebar.select_row(second._playlist_rows[playlist.id])
        assert second._heading.get_text() == 'Persist renamed'
        assert ids(second) == [library[1].id, library[3].id]


def test_keyboard_context_actions_and_pending_dialog_cleanup(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        playlist = window.playlists.create('Keyboard')
        window._refresh_sidebar()
        window.selection.select_item(0, True)
        window.view.grab_focus()
        window.activate_action('win.context-menu', None)
        assert window._popover.get_visible()
        assert len(window._context_ids) == 1
        window._close_menu()
        row = window._playlist_rows[playlist.id]
        window.sidebar.select_row(row)
        row.grab_focus()
        window.activate_action('win.context-menu', None)
        model = window._popover.get_menu_model()
        assert model.get_item_attribute_value(0, 'label', GLib.VariantType.new('s')).get_string() == 'Rename'
        assert model.get_item_attribute_value(1, 'label', GLib.VariantType.new('s')).get_string() == 'Delete'
        window._name_dialog()
        assert window._dialogs
        window.close()
        assert not window._dialogs


@pytest.fixture
def sorting_playlist(library):
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        metadata = [('Zebra', 'Artist B', 'Album C', 61),
                    ('alpha', 'Artist C', 'Album A', 600),
                    ('Echo', 'Artist A', 'Album D', 121),
                    ('bravo', 'Artist D', 'Album B', 9)]
        with LibraryDatabase(database_path()) as db:
            db.connection.executemany('UPDATE songs SET title=?,artist=?,album=?,duration=? WHERE id=?',
                                      ((*values, song.id) for song, values in zip(library, metadata)))
        saved = [library[index].id for index in (2, 0, 3, 1)]
        playlist = window.playlists.create('Temporary sorting', saved)
        window._refresh_sidebar()
        window.sidebar.select_row(window._playlist_rows[playlist.id])
        yield window, playlist, [song.id for song in library], saved


def database_snapshot():
    with sqlite3.connect(database_path().as_uri() + '?mode=ro', uri=True) as db:
        return tuple(db.iterdump())


@pytest.mark.parametrize('column,ascending', [
    (1, (1, 3, 2, 0)),  # Title, case insensitive
    (2, (2, 0, 1, 3)),  # Artist
    (3, (1, 3, 0, 2)),  # Album
    (4, (3, 0, 2, 1)),  # Numeric duration: 9, 61, 121, 600
])
def test_playlist_column_sort_both_directions_and_saved_order(sorting_playlist, column, ascending):
    window, playlist, songs, saved = sorting_playlist
    before = database_snapshot()
    assert ids(window) == saved
    assert not window._saved_order.get_visible()
    header = window.view.get_columns().get_item(column)
    for direction, order in ((Gtk.SortType.ASCENDING, ascending),
                             (Gtk.SortType.DESCENDING, ascending[::-1])):
        window.view.sort_by_column(header, direction)
        assert ids(window) == [songs[index] for index in order]
        assert window._saved_order.get_visible()
        assert members(window, playlist.id) == saved
    window._saved_order.emit('clicked')
    assert ids(window) == saved
    assert window.view.get_sorter().get_primary_sort_column() is None
    assert not window._saved_order.get_visible()
    assert database_snapshot() == before


def test_sorted_playlist_search_and_multiselection_follow_song_ids(sorting_playlist):
    window, playlist, songs, saved = sorting_playlist
    # Keep the same two songs through direction changes, other columns and reset.
    window.selection.select_item(0, True)
    window.selection.select_item(2, False)
    selected = {saved[0], saved[2]}
    for column, direction in ((1, Gtk.SortType.ASCENDING), (1, Gtk.SortType.DESCENDING),
                              (4, Gtk.SortType.ASCENDING), (2, Gtk.SortType.DESCENDING)):
        window.view.sort_by_column(window.view.get_columns().get_item(column), direction)
        assert {song.id for song in window._selected_songs()} == selected
    window._saved_order.emit('clicked')
    assert {song.id for song in window._selected_songs()} == selected
    window.view.sort_by_column(window._title_column, Gtk.SortType.ASCENDING)
    window._search.set_text('a')
    wait_for(lambda: window._query == 'a')
    # Narrow with multiple terms while retaining the column sort.
    window._search.set_text('bravo album b')
    wait_for(lambda: window._query == 'bravo album b')
    assert ids(window) == [songs[3]]
    window.selection.select_item(0, True)
    window.view.sort_by_column(window._title_column, Gtk.SortType.DESCENDING)
    assert [song.id for song in window._selected_songs()] == [songs[3]]
    window._search.set_text('br')
    wait_for(lambda: window._query == 'br')
    assert ids(window) == [songs[0], songs[3]]
    window.view.sort_by_column(window._title_column, Gtk.SortType.ASCENDING)
    assert ids(window) == [songs[3], songs[0]]
    window._saved_order.emit('clicked')
    assert ids(window) == [songs[0], songs[3]]
    assert [song.id for song in window._selected_songs()] == [songs[3]]
    window._search.set_text('nothing matches')
    wait_for(lambda: window.filtered.get_n_items() == 0)
    window.view.sort_by_column(window._title_column, Gtk.SortType.ASCENDING)
    window._search.set_text('')
    wait_for(lambda: window.filtered.get_n_items() == 4)
    assert ids(window) == [songs[index] for index in (1, 3, 2, 0)]
    assert members(window, playlist.id) == saved


def test_sorted_playlist_play_and_activation_use_independent_queue_snapshots(sorting_playlist):
    window, playlist, songs, saved = sorting_playlist
    window.view.sort_by_column(window._title_column, Gtk.SortType.ASCENDING)
    window._search.set_text('br')
    wait_for(lambda: window.filtered.get_n_items() == 2)
    window._playlist_play.emit('clicked')
    wait_for(lambda: window.controller.state == 'playing')
    assert [song.id for song in window.controller.queue] == saved
    assert window.controller.current_song_id == saved[0]
    queue = window.controller.queue
    window.view.sort_by_column(window._title_column, Gtk.SortType.DESCENDING)
    assert window.controller.queue is queue
    assert ids(window) == [songs[0], songs[3]]
    window.view.emit('activate', 1)
    assert window.controller.current_song_id == songs[3]
    assert [song.id for song in window.controller.queue] == [songs[0], songs[3]]
    queue = window.controller.queue
    window._saved_order.emit('clicked')
    window._search.set_text('')
    wait_for(lambda: window.filtered.get_n_items() == 4)
    window.view.sort_by_column(window._title_column, Gtk.SortType.ASCENDING)
    assert window.controller.queue is queue
    assert window.controller.current_song_id == songs[3]
    assert members(window, playlist.id) == saved


def test_switch_views_resets_playlist_sort_and_restores_all_songs_sort(sorting_playlist):
    window, playlist, songs, saved = sorting_playlist
    other = window.playlists.create('Other', saved[::-1])
    window._refresh_sidebar()
    window.sidebar.select_row(window._all_row)
    album = window.view.get_columns().get_item(3)
    window.view.sort_by_column(album, Gtk.SortType.DESCENDING)
    all_order = ids(window)
    window.selection.select_item(0, True)
    selected = window._selected_songs()[0].id
    window.view.sort_by_column(album, Gtk.SortType.ASCENDING)
    assert [song.id for song in window._selected_songs()] == [selected]
    window.view.sort_by_column(album, Gtk.SortType.DESCENDING)
    window.sidebar.select_row(window._playlist_rows[playlist.id])
    assert ids(window) == saved
    assert not window._saved_order.get_visible()
    window.view.sort_by_column(window._title_column, Gtk.SortType.ASCENDING)
    window.sidebar.select_row(window._playlist_rows[other.id])
    assert ids(window) == saved[::-1]
    assert window.view.get_sorter().get_primary_sort_column() is None
    assert not window._saved_order.get_visible()
    window.view.sort_by_column(window._title_column, Gtk.SortType.DESCENDING)
    window.sidebar.select_row(window._playlist_rows[playlist.id])
    assert ids(window) == saved
    window.sidebar.select_row(window._all_row)
    assert ids(window) == all_order
    assert window.view.get_sorter().get_primary_sort_column() == album
    assert window.view.get_sorter().get_primary_sort_order() == Gtk.SortType.DESCENDING
    assert not window._saved_order.get_visible()


def test_temporary_sort_does_not_persist_after_restart(sorting_playlist):
    window, playlist, songs, saved = sorting_playlist
    before = database_snapshot()
    window.view.sort_by_column(window._title_column, Gtk.SortType.ASCENDING)
    assert ids(window) != saved
    window.close()
    with open_window() as restarted:
        wait_for(lambda: not restarted.scanner.running)
        restarted.sidebar.select_row(restarted._playlist_rows[playlist.id])
        assert ids(restarted) == saved
        assert not restarted._saved_order.get_visible()
        assert members(restarted, playlist.id) == saved
        assert database_snapshot() == before


def test_move_actions_disabled_while_sorted_and_restored_in_saved_order(sorting_playlist):
    window, playlist, songs, saved = sorting_playlist
    window._show_song_menu(1)
    assert window.lookup_action('playlist-up').get_enabled()
    assert window.lookup_action('playlist-down').get_enabled()
    before = database_snapshot()
    window.view.sort_by_column(window._title_column, Gtk.SortType.ASCENDING)
    assert not window._popover.get_visible()
    for action in ('playlist-up', 'playlist-down'):
        assert not window.lookup_action(action).get_enabled()
        window.activate_action(f'win.{action}', None)
    window._show_song_menu(1)
    for action in ('playlist-up', 'playlist-down'):
        assert not window.lookup_action(action).get_enabled()
        window.activate_action(f'win.{action}', None)
    # The callback also rejects stale/direct requests while sorted.
    window._move_playlist_song(-1)
    window._move_playlist_song(1)
    assert database_snapshot() == before
    window._saved_order.emit('clicked')
    assert ids(window) == saved
    window._show_song_menu(1)
    assert window.lookup_action('playlist-up').get_enabled()
    assert window.lookup_action('playlist-down').get_enabled()
    window.activate_action('win.playlist-up', None)
    assert ids(window) == [saved[1], saved[0], saved[2], saved[3]]
    assert members(window, playlist.id) == ids(window)
    assert [song.id for song in window._selected_songs()] == [saved[1]]
    window._show_song_menu(0)
    window.activate_action('win.playlist-down', None)
    assert ids(window) == saved
    assert members(window, playlist.id) == saved


@pytest.mark.parametrize('playlist_view,query', [(False, ''), (True, ''), (True, 'track 00')],
                         ids=['all-songs', 'playlist', 'filtered-playlist'])
def test_sort_returns_viewport_to_top_preserving_selection_and_queue(library, wav_file, user_directories,
                                                                    playlist_view, query):
    from music_player.metadata import Metadata

    def settle(window):
        frames = []

        def tick(widget, clock):
            frames.append(None)
            return len(frames) < 3

        window.view.add_tick_callback(tick)
        wait_for(lambda: len(frames) == 3)

    with open_window(size=(720, 480)) as window:
        wait_for(lambda: not window.scanner.running)
        songs = []
        with LibraryDatabase(database_path()) as db:
            folder_id = db.folders()[0]['id']
            for index in range(120):
                path = user_directories / f'scroll-{index:03}.wav'
                shutil.copyfile(wav_file, path)
                songs.append(db.upsert(folder_id, path,
                                       Metadata(f'Track {index:03}', f'Artist {index:03}',
                                                f'Album {index:03}', index + 1), path.stat(), None))
        saved = [song.id for song in songs[::2] + songs[1::2]]
        playlist = window.playlists.create('Scroll regression', saved)
        window._refresh_sidebar()
        if playlist_view:
            window._show_view(playlist.id)
        else:
            window._reload_view()
        if query:
            window._search.set_text(query)
            wait_for(lambda: window._query == query)
        displayed = ids(window)
        window.controller.activate([library[0]], playing=False)
        wait_for(lambda: window.controller.state == 'paused')
        queue = window.controller.queue
        window.selection.select_item(0, True)
        window.selection.select_item(5, False)
        selected = {song.id for song in window._selected_songs()}
        vertical = window.view.get_vadjustment()
        horizontal = window.view.get_hadjustment()
        settle(window)
        assert vertical.get_upper() > vertical.get_page_size() * 2
        # Stay away from the edge: showing Saved order can widen a narrow window
        # slightly and clamp an adjustment previously set to its maximum.
        horizontal.set_value((horizontal.get_upper() - horizontal.get_page_size()) / 2)
        settle(window)
        horizontal_position = horizontal.get_value()
        assert horizontal_position > 0
        # From the top, GTK otherwise follows the old first row to the bottom
        # when its position changes. Also cover sorts made after manual scrolling.
        for column in range(1, 5):
            for direction in (Gtk.SortType.DESCENDING, Gtk.SortType.ASCENDING):
                if direction == Gtk.SortType.ASCENDING:
                    vertical.set_value((vertical.get_upper() - vertical.get_page_size()) / 2)
                    settle(window)
                    assert vertical.get_value() > 0
                window.view.sort_by_column(window.view.get_columns().get_item(column), direction)
                settle(window)
                wait_for(lambda: vertical.get_value() == pytest.approx(vertical.get_lower(), abs=1))
                settle(window)
                assert vertical.get_value() == pytest.approx(vertical.get_lower(), abs=1)
                assert horizontal.get_value() == pytest.approx(horizontal_position, abs=1)
                assert {song.id for song in window._selected_songs()} == selected
                assert window.controller.queue is queue
        if playlist_view:
            vertical.set_value(vertical.get_upper() - vertical.get_page_size())
            settle(window)
            window._saved_order.emit('clicked')
            settle(window)
            wait_for(lambda: vertical.get_value() == pytest.approx(vertical.get_lower(), abs=1))
            assert ids(window) == displayed
            assert {song.id for song in window._selected_songs()} == selected
            assert window.controller.queue is queue
        assert members(window, playlist.id) == saved
