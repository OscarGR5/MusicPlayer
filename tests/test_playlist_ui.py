import hashlib
import shutil
from pathlib import Path

import gi
import pytest

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk

from music_player.library import LibraryDatabase
from music_player.paths import database_path
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
        assert window.sorted.get_sorter() is None
        assert all(column.get_sorter() is None for column in window._column_sorters)
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
