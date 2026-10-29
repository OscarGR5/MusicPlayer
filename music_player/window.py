"""Compact All Songs library with native GTK4 filtering and column sorting."""

import sqlite3
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .controller import PlaybackController
from .player_bar import PlayerBar
from .statistics_ui import StatisticsIcon, StatisticsPage
from .shortcuts import WindowShortcuts
from .library import LibraryDatabase
from .playlists import PlaylistStore
from .paths import database_path, thumbnail_directory
from .scanner import LibraryScanner
from .song_model import SongItem, compare_songs, format_duration, thumbnail_texture


class MusicPlayerWindow(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="MusicPlayer")
        self.set_default_size(1040, 640)
        self.set_size_request(720, 400)
        self._closed = False
        self._chooser = None
        self._chooser_response_id = None
        self.scanner = None
        self._items = {}
        self._query = ""
        self._statistics_visible = False
        self._playlist_id = None
        self._playlist_rows = {}
        self._sidebar_refreshing = False
        self._popover = None
        self._dialogs = set()
        self._context_ids = ()
        self._context_playlist_id = None
        self._database = database_path()
        try:
            self.playlists = PlaylistStore(self._database)
            self.controller = PlaybackController(self._database, self._playback_changed)
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError(f'Could not open listening history: {exc}') from exc
        self.player = self.controller.audio
        self._install_playlist_actions()
        self._build_interface()
        self.connect("close-request", self._on_close)
        self.shortcuts = WindowShortcuts(self)
        self._tick_source = GLib.timeout_add(250, self._tick)
        try:
            with LibraryDatabase(self._database) as db:
                db.configure_default_folder(Path.home() / "Music")
                self._update_songs(db.songs())
            self.scanner = LibraryScanner(
                self._database, thumbnail_directory(), self._dispatch,
                self._update_songs, self._scan_progress, self._scan_finished,
            )
            self._start_scan()
        except (OSError, sqlite3.Error) as exc:
            self._scan_progress(f"Library unavailable: {exc}. Check access to the user data directory and restart.")
            self._rescan.set_sensitive(False)

    def _build_interface(self):
        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label="MusicPlayer"))
        open_button = Gtk.Button(label="Open audio file")
        open_button.connect("clicked", self._open_file)
        header.pack_end(open_button)
        self.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root)
        body = Gtk.Box(vexpand=True)
        root.append(body)
        self.sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar.add_css_class('navigation-sidebar')
        self.sidebar.set_size_request(160, -1)
        self._all_row = self._sidebar_row('All Songs', 'audio-x-generic-symbolic', None)
        self.sidebar.append(self._all_row)
        self._statistics_row = self._sidebar_row('Statistics', StatisticsIcon(), None)
        self.sidebar.append(self._statistics_row)
        section = Gtk.Box(spacing=4)
        label = Gtk.Label(label='PLAYLISTS', xalign=0, hexpand=True)
        label.add_css_class('dim-label')
        section.append(label)
        self._create_playlist = Gtk.Button(icon_name='list-add-symbolic', tooltip_text='Create playlist')
        self._create_playlist.add_css_class('flat')
        self._create_playlist.connect('clicked', lambda _: self._name_dialog())
        section.append(self._create_playlist)
        self.sidebar.append(Gtk.ListBoxRow(child=section, selectable=False, activatable=False))
        self.sidebar.select_row(self._all_row)
        self.sidebar.connect('row-selected', self._sidebar_selected)
        sidebar_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_child(self.sidebar)
        body.append(sidebar_scroll)
        self._refresh_sidebar()
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self._content_stack = Gtk.Stack(hexpand=True, vexpand=True)
        body.append(self._content_stack)
        self._content_stack.add_named(main, 'library')
        self.statistics = StatisticsPage(self._database)
        self._content_stack.add_named(self.statistics, 'statistics')
        self._content_stack.set_visible_child_name('library')
        toolbar = Gtk.Box(spacing=12)
        self._margins(toolbar, 12)
        headings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self._heading = Gtk.Label(label='All Songs', xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=20)
        self._heading.add_css_class('title-2')
        self._playlist_count = Gtk.Label(xalign=0, visible=False)
        self._playlist_count.add_css_class('dim-label')
        headings.append(self._heading)
        headings.append(self._playlist_count)
        toolbar.append(headings)
        self._playlist_play = Gtk.Button(label='Play', tooltip_text='Play playlist', visible=False)
        self._playlist_play.connect('clicked', lambda _: self._perform(self._play_playlist))
        toolbar.append(self._playlist_play)
        self._saved_order = Gtk.Button(label='Saved order', visible=False,
                                       tooltip_text='Restore saved playlist order')
        self._saved_order.add_css_class('flat')
        self._saved_order.connect('clicked', lambda _: self.view.sort_by_column(None, Gtk.SortType.ASCENDING))
        toolbar.append(self._saved_order)
        self._search = Gtk.SearchEntry(placeholder_text="Search songs, artists, albums")
        self._search.set_size_request(250, -1)
        self._search.connect("search-changed", self._search_changed)
        toolbar.append(self._search)
        self._rescan = Gtk.Button(label="Rescan Library")
        self._rescan.connect("clicked", lambda _: self._start_scan())
        toolbar.append(self._rescan)
        main.append(toolbar)

        self.store = Gio.ListStore.new(SongItem)
        self._filter = Gtk.CustomFilter.new(lambda item: item.matches(self._query))
        self.filtered = Gtk.FilterListModel(model=self.store, filter=self._filter)
        self.sorted = Gtk.SortListModel(model=self.filtered)
        self.selection = Gtk.MultiSelection(model=self.sorted)
        self.selection.connect("selection-changed", lambda *_: self._update_controls())
        self.view = Gtk.ColumnView(model=self.selection, show_row_separators=True)
        self.view.set_single_click_activate(False)
        self.view.connect("activate", self._activate_song)
        self._column_sorters = {}
        self._add_columns()
        self._all_sort_column = self._title_column
        self._all_sort_order = Gtk.SortType.ASCENDING
        sorter = self.view.get_sorter()
        # Capture IDs before SortListModel handles the signal and changes positions.
        sorter.connect('changed', self._sort_changing)
        self.sorted.set_sorter(sorter)
        sorter.connect_after('changed', self._sort_changed)
        self.view.sort_by_column(self._title_column, Gtk.SortType.ASCENDING)
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True,
                                      margin_end=12, overlay_scrolling=False)
        scroller.set_child(self.view)
        overlay = Gtk.Overlay(child=scroller)
        self._empty = Gtk.Label(label="Scanning your music folder…", justify=Gtk.Justification.CENTER,
                                wrap=True, can_target=False)
        self._empty.add_css_class("dim-label")
        overlay.add_overlay(self._empty)
        main.append(overlay)
        self.filtered.connect("items-changed", lambda *_: self._update_count())

        scan_row = Gtk.Box(spacing=12)
        self._margins(scan_row, 12)
        self._scan_label = Gtk.Label(label="Preparing library…", xalign=0, hexpand=True,
                                    ellipsize=Pango.EllipsizeMode.END)
        self._scan_label.add_css_class("dim-label")
        self._count = Gtk.Label(label="0 songs")
        self._count.add_css_class("dim-label")
        scan_row.append(self._scan_label)
        scan_row.append(self._count)
        main.append(scan_row)

        root.append(Gtk.Separator())
        self.bar = PlayerBar(self.controller, self._perform)
        self._status = self.bar.status
        self._filename = self.bar.title
        root.append(self.bar)
        self._update_controls()

    @staticmethod
    def _margins(widget, amount):
        for side in ("top", "bottom", "start", "end"):
            getattr(widget, f"set_margin_{side}")(amount)

    def _add_columns(self):
        cover_factory = Gtk.SignalListItemFactory()

        def cover_setup(factory, item):
            image = Gtk.Image(pixel_size=32)
            image.set_size_request(32, 32)
            item.set_child(image)

        def cover_bind(factory, item):
            image = item.get_child()
            self._attach_song_menu(image.get_parent() or image, item)
            texture = thumbnail_texture(item.get_item().song.artwork_path)
            if texture is None:
                image.set_from_icon_name("audio-x-generic-symbolic")
            else:
                image.set_from_paintable(texture)

        cover_factory.connect("setup", cover_setup)
        cover_factory.connect("bind", cover_bind)
        cover_factory.connect("unbind", self._cell_unbind, 'cover')
        self.view.append_column(Gtk.ColumnViewColumn(title="", factory=cover_factory, fixed_width=64))

        for title, field, width in (("Title", "title", 220), ("Artist", "artist", 160),
                                    ("Album", "album", 180), ("Duration", "duration", 90)):
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", self._cell_setup, field)
            factory.connect("bind", self._cell_bind, field)
            factory.connect("unbind", self._cell_unbind, field)
            column = Gtk.ColumnViewColumn(title=title, factory=factory, resizable=True,
                                          expand=field != "duration", fixed_width=width)
            sorter = Gtk.CustomSorter.new(lambda a, b, _data, field=field: compare_songs(a, b, field))
            column.set_sorter(sorter)
            self._column_sorters[column] = sorter
            self.view.append_column(column)
            if field == "title":
                self._title_column = column

    def _cell_setup(self, factory, item, field):
        label = Gtk.Label(xalign=1 if field == "duration" else 0, ellipsize=Pango.EllipsizeMode.END)
        if field == "title":
            box = Gtk.Box(spacing=6)
            marker = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
            marker.set_tooltip_text("Now playing")
            marker.set_visible(False)
            box.append(marker)
            box.append(label)
            label.set_hexpand(True)
            item.set_child(box)
        else:
            item.set_child(label)

    def _cell_bind(self, factory, item, field):
        song_item = item.get_item()
        child = item.get_child()
        self._attach_song_menu(child.get_parent() or child, item)
        label = child.get_last_child() if field == "title" else child
        value = getattr(song_item.song, field)
        text = format_duration(value) if field == "duration" else value
        unavailable = not song_item.song.available
        label.set_text(text)
        label.set_tooltip_text(f'{text} (Unavailable)' if unavailable else text)
        if unavailable:
            label.add_css_class('dim-label')
        else:
            label.remove_css_class('dim-label')
        if field == 'title' and unavailable:
            label.set_text(f'{text} (Unavailable)')
        if field == "title":
            marker = child.get_first_child()
            marker.set_visible(song_item.playing)
            item._playing_handler = song_item.connect("notify::playing", lambda song, _: marker.set_visible(song.playing))

    def _cell_unbind(self, factory, item, field):
        child = item.get_child()
        widget = child.get_parent() or child
        widget._song_context_item = None
        if field == "title" and getattr(item, "_playing_handler", None):
            item.get_item().disconnect(item._playing_handler)
            item._playing_handler = None

    def _search_changed(self, entry):
        self._query = entry.get_text()
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_count()

    def _update_count(self):
        count = self.filtered.get_n_items()
        total = self.store.get_n_items()
        total_text = f'{total} song' + ('' if total == 1 else 's')
        self._count.set_text(f'{count} of {total_text}' if self._query.strip() else total_text)
        self._empty.set_visible(count == 0)
        if self._query.strip():
            empty = 'No matching songs. Try a different search.'
        elif self._playlist_id is not None:
            empty = 'This playlist is empty.\nAdd songs from All Songs using Add to Playlist.'
        else:
            empty = 'No songs yet.\nAdd audio files to your music folder, then choose Rescan Library.'
        self._empty.set_text(empty)
        self._playlist_count.set_text(total_text)
        self._playlist_play.set_sensitive(any(item.song.available for item in self._items.values()))

    def _selected_songs(self):
        return [self.sorted.get_item(index).song for index in range(self.sorted.get_n_items())
                if self.selection.is_selected(index)]

    def _restore_selection(self, song_ids):
        self.selection.unselect_all()
        for index in range(self.sorted.get_n_items()):
            if self.sorted.get_item(index).song.id in song_ids:
                self.selection.select_item(index, False)

    def _sort_changing(self, sorter, change):
        self._sort_selection = {song.id for song in self._selected_songs()}

    def _playlist_sorted(self):
        return self._playlist_id is not None and self.view.get_sorter().get_primary_sort_column() is not None

    def _sort_changed(self, sorter, change):
        self._restore_selection(self._sort_selection)
        self._saved_order.set_visible(self._playlist_sorted())
        self._close_menu()
        # Discard move actions from a menu opened before the sort changed.
        self.lookup_action('playlist-up').set_enabled(False)
        self.lookup_action('playlist-down').set_enabled(False)
        if self.sorted.get_n_items():
            # Replace GTK's old-row scroll anchor without selecting or focusing it.
            self.view.scroll_to(0, None, Gtk.ListScrollFlags(0), None)

    def _song_item(self, song):
        item = SongItem(song)
        item.playing = self.controller.state == 'playing' and song.id == self.controller.current_song_id
        return item

    def _replace_songs(self, songs, selected=()):
        self._close_menu()
        self._items = {song.id: self._song_item(song) for song in songs}
        self.store.splice(0, self.store.get_n_items(), list(self._items.values()))
        self._restore_selection(set(selected))
        self._update_count()
        self._update_controls()

    def _reload_view(self, preserve_selection=True):
        selected = [song.id for song in self._selected_songs()] if preserve_selection else []
        if self._playlist_id is None:
            with LibraryDatabase(self._database) as db:
                songs = db.songs()
        else:
            songs = self.playlists.songs(self._playlist_id)
        self._replace_songs(songs, selected)

    def _update_songs(self, songs):
        if self._playlist_id is not None:
            self._reload_view()
            return
        selected = {song.id for song in self._selected_songs()}
        for song in songs:
            previous = self._items.get(song.id)
            item = self._song_item(song)
            self._items[song.id] = item
            if previous is not None:
                found, position = self.store.find(previous)
                if found:
                    self.store.splice(position, 1, [item])
            else:
                self.store.append(item)
        self._restore_selection(selected)
        self._update_count()

    def _select_id(self, song_id):
        for position in range(self.sorted.get_n_items()):
            if self.sorted.get_item(position).song.id == song_id:
                self.selection.select_item(position, True)
                break

    def _dispatch(self, callback, *args):
        def deliver():
            if not self._closed:
                callback(*args)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(deliver)

    def _start_scan(self):
        if self.scanner is not None and self.scanner.start():
            self._rescan.set_sensitive(False)
            self._scan_progress("Scanning library…")

    def _scan_progress(self, text):
        self._scan_label.set_text(text)
        self._scan_label.set_tooltip_text(text)

    def _scan_finished(self, removed, summary):
        if self._playlist_id is not None:
            self._reload_view()
        else:
            for song_id in removed:
                item = self._items.pop(song_id, None)
                if item is not None:
                    found, position = self.store.find(item)
                    if found:
                        self.store.remove(position)
        self._scan_progress(summary)
        self._rescan.set_sensitive(True)
        self._update_count()

    def _activate_song(self, view, position):
        item = self.sorted.get_item(position)
        if item is not None and item.song.available:
            self.selection.select_item(position, True)
            snapshot = [self.sorted.get_item(index).song for index in range(self.sorted.get_n_items())]
            self._perform(lambda: self.controller.activate(snapshot, position))

    def _load_and_play(self, song):
        self.controller.activate([song])

    def _play_selected(self):
        if self.controller.current is None and self.controller.audio.path is None:
            for position in range(self.sorted.get_n_items()):
                if self.selection.is_selected(position) and self.sorted.get_item(position).song.available:
                    self._activate_song(self.view, position)
                    break
        else:
            self.controller.play_pause()

    def _playback_changed(self, event):
        if self._closed or not hasattr(self, 'bar'):
            return
        self.bar.update(event)
        if event in ('track', 'state'):
            for item in self._items.values():
                item.playing = self.controller.state == 'playing' and item.song.id == self.controller.current_song_id
            if event == 'track' and self.controller.current_song_id is not None and len(self._selected_songs()) <= 1:
                self._select_id(self.controller.current_song_id)
        self._update_controls()

    def _update_controls(self):
        if hasattr(self, 'bar'):
            self.bar.play.set_sensitive(self.controller.current is not None or self.player.path is not None or
                                        any(song.available for song in self._selected_songs()))

    def _show_status(self, text):
        self._status.set_text(text)
        self._status.set_tooltip_text(text)

    def _tick(self):
        if self._closed:
            return GLib.SOURCE_REMOVE
        self.controller.tick()
        return GLib.SOURCE_CONTINUE

    def _perform(self, action):
        try:
            action()
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
            self._show_status(f"Error: {exc}")

    def _open_file(self, button):
        if self._closed or self._chooser is not None:
            return
        # Temporary GTK4 compatibility fallback. FileDialog's portal backend
        # can finish exporting the parent handle after cancellation/destruction
        # (GTK 4.22.5, Wayland). FileChooserDialog is deprecated since 4.10 but
        # stays in-process and has an application-owned destruction lifecycle.
        # See PORTAL_SHUTDOWN_INVESTIGATION.md before restoring FileDialog.
        chooser = Gtk.FileChooserDialog(title='Select local audio', transient_for=self,
                                        modal=True, destroy_with_parent=True,
                                        action=Gtk.FileChooserAction.OPEN)
        chooser.add_buttons('_Cancel', Gtk.ResponseType.CANCEL, '_Open', Gtk.ResponseType.ACCEPT)
        chooser.set_default_response(Gtk.ResponseType.ACCEPT)
        self._chooser = chooser
        self._chooser_response_id = chooser.connect('response', self._file_response)
        chooser.present()

    def _dismiss_file_chooser(self):
        chooser = self._chooser
        if chooser is not None:
            self._chooser = None
            chooser.disconnect(self._chooser_response_id)
            self._chooser_response_id = None
            chooser.destroy()

    def _file_response(self, chooser, response):
        if self._closed or chooser is not self._chooser:
            return
        try:
            if response != Gtk.ResponseType.ACCEPT:
                return
            file = chooser.get_file()
            path = file.get_path() if file is not None else None
            if path is None:
                raise ValueError("Only local files are supported.")
            self.controller.open_file(path)
        except (GLib.Error, OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
            self._show_status(f"Error: {exc}")
        finally:
            self._dismiss_file_chooser()

    def close_player(self):
        if self._closed:
            return
        self.set_focus(None)
        self._closed = True
        self._close_menu()
        if self._popover is not None:
            self._popover.unparent()
        for dialog in list(self._dialogs):
            self._finish_dialog(dialog)
        self._dialogs.clear()
        if hasattr(self, '_tick_source'):
            GLib.source_remove(self._tick_source)
        if hasattr(self, 'bar'):
            self.bar.cancel_seek()
        self.statistics.close()
        if self.scanner is not None:
            self.scanner.close()
        self._dismiss_file_chooser()
        self.controller.close()

    def _on_close(self, window):
        self.close_player()
        return False

    def _sidebar_row(self, name, icon, playlist_id):
        content = Gtk.Box(spacing=10)
        content.append(Gtk.Image.new_from_icon_name(icon) if isinstance(icon, str) else icon)
        label = Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, max_width_chars=16)
        label.set_tooltip_text(name)
        content.append(label)
        row = Gtk.ListBoxRow(child=content)
        row.playlist_id = playlist_id
        if playlist_id is not None:
            gesture = Gtk.GestureClick(button=3)
            gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            gesture.connect('pressed', lambda gesture, count, x, y: self._sidebar_context(gesture, row, x, y))
            row.add_controller(gesture)
        return row

    def _refresh_sidebar(self):
        self._close_menu()
        self._sidebar_refreshing = True
        for row in self._playlist_rows.values():
            self.sidebar.remove(row)
        self._playlist_rows.clear()
        for playlist in self.playlists.list():
            row = self._sidebar_row(playlist.name, 'view-list-symbolic', playlist.id)
            self._playlist_rows[playlist.id] = row
            self.sidebar.append(row)
        selected = self._playlist_rows.get(self._playlist_id, self._all_row)
        self.sidebar.select_row(self._statistics_row if self._statistics_visible else selected)
        self._sidebar_refreshing = False

    def _sidebar_selected(self, sidebar, row):
        if not self._sidebar_refreshing and row is not None:
            if row is self._statistics_row:
                self._close_menu()
                self._statistics_visible = True
                self._content_stack.set_visible_child_name('statistics')
                self.statistics.set_active(True)
            else:
                self._perform(lambda: self._show_view(row.playlist_id))

    def _show_view(self, playlist_id):
        self.statistics.set_active(False)
        self._statistics_visible = False
        self._content_stack.set_visible_child_name('library')
        if playlist_id == self._playlist_id:
            return
        if self._playlist_id is None:
            sorter = self.view.get_sorter()
            self._all_sort_column = sorter.get_primary_sort_column() or self._title_column
            self._all_sort_order = sorter.get_primary_sort_order()
        playlist = self.playlists.get(playlist_id) if playlist_id is not None else None
        self._playlist_id = playlist_id
        self._search.set_text('')
        self._query = ''
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._heading.set_text(playlist.name if playlist else 'All Songs')
        self._heading.set_tooltip_text(self._heading.get_text())
        self._playlist_count.set_visible(playlist is not None)
        self._playlist_play.set_visible(playlist is not None)
        self._rescan.set_visible(playlist is None)
        if playlist is None:
            self.view.sort_by_column(self._all_sort_column, self._all_sort_order)
        else:
            self.view.sort_by_column(None, Gtk.SortType.ASCENDING)
        self._saved_order.set_visible(False)
        self._reload_view(preserve_selection=False)
        self._sidebar_refreshing = True
        self.sidebar.select_row(self._playlist_rows.get(playlist_id, self._all_row))
        self._sidebar_refreshing = False

    def _play_playlist(self):
        if self._playlist_id is None:
            return
        # Play starts from saved order, independently of an active search filter.
        songs = self.playlists.songs(self._playlist_id)
        if not self.controller.play_queue(songs):
            self._scan_progress('No available songs in this playlist. Rescan the library after restoring missing files.')

    def _install_playlist_actions(self):
        bindings = {
            'playlist-add': ('x', lambda value: self._add_to_playlist(value.get_int64())),
            'playlist-create': (None, lambda _: self._name_dialog(song_ids=self._context_ids)),
            'playlist-remove': (None, lambda _: self._remove_from_playlist()),
            'playlist-up': (None, lambda _: self._move_playlist_song(-1)),
            'playlist-down': (None, lambda _: self._move_playlist_song(1)),
            'playlist-rename': ('x', lambda value: self._name_dialog(value.get_int64())),
            'playlist-delete': ('x', lambda value: self._delete_dialog(value.get_int64())),
        }
        for name, (signature, callback) in bindings.items():
            action = Gio.SimpleAction.new(name, GLib.VariantType.new(signature) if signature else None)
            action.connect('activate', lambda action, value, callback=callback: self._perform(lambda: callback(value)))
            self.add_action(action)

    def _attach_song_menu(self, widget, item):
        # Keep the wrapper alive while bound; _cell_unbind releases the back
        # reference so a retired ListItem and its cells can be freed.
        widget._song_context_item = item
        if getattr(widget, '_song_context_gesture', None) is not None:
            return
        gesture = Gtk.GestureClick(button=3)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        def pressed(gesture, count, x, y):
            widget = gesture.get_widget()
            item = widget._song_context_item
            if item is None:
                return
            position = item.get_position()
            if position != Gtk.INVALID_LIST_POSITION:
                self._show_song_menu(position, widget, x, y)
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        gesture.connect('pressed', pressed)
        widget.add_controller(gesture)
        widget._song_context_gesture = gesture

    def _show_song_menu(self, position, anchor=None, x=0, y=0):
        if self.sorted.get_item(position) is None:
            return None
        if not self.selection.is_selected(position):
            self.selection.select_item(position, True)
        self._context_ids = tuple(song.id for song in self._selected_songs())
        self._context_playlist_id = self._playlist_id
        menu = Gio.Menu()
        if self._playlist_id is None:
            add = Gio.Menu()
            playlists = self.playlists.list()
            for playlist in playlists:
                entry = Gio.MenuItem.new(playlist.name, None)
                entry.set_action_and_target_value('win.playlist-add', GLib.Variant('x', playlist.id))
                add.append_item(entry)
            if not playlists:
                add.append('Create Playlist…', 'win.playlist-create')
            menu.append_submenu('Add to Playlist', add)
        else:
            menu.append('Remove from Playlist', 'win.playlist-remove')
            menu.append('Move Up', 'win.playlist-up')
            menu.append('Move Down', 'win.playlist-down')
            ids = [song.id for song in self.playlists.songs(self._playlist_id)]
            single = len(self._context_ids) == 1 and not self._playlist_sorted()
            index = ids.index(self._context_ids[0]) if single else -1
            self.lookup_action('playlist-up').set_enabled(single and index > 0)
            self.lookup_action('playlist-down').set_enabled(single and index < len(ids) - 1)
        self._popup_menu(menu, anchor or self.view, x, y)
        return menu

    def _sidebar_context(self, gesture, row, x, y):
        menu = Gio.Menu()
        for label, action in (('Rename', 'playlist-rename'), ('Delete', 'playlist-delete')):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(f'win.{action}', GLib.Variant('x', row.playlist_id))
            menu.append_item(item)
        self._popup_menu(menu, row, x, y)
        if gesture is not None:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _keyboard_context_menu(self):
        if self._statistics_visible:
            return
        focus = self.get_focus()
        if focus is not None and (focus is self.sidebar or focus.is_ancestor(self.sidebar)):
            row = self.sidebar.get_selected_row()
            if row is not None and row.playlist_id is not None:
                self._sidebar_context(None, row, 0, row.get_height())
            return
        first = next((index for index in range(self.sorted.get_n_items()) if self.selection.is_selected(index)), None)
        if first is not None:
            self._show_song_menu(first)

    def _popup_menu(self, menu, anchor, x, y):
        self._close_menu()
        if not anchor.get_mapped():
            return
        root = self.get_child()
        if self._popover is None:
            self._popover = Gtk.PopoverMenu()
            self._popover.set_parent(root)
        self._popover.set_menu_model(menu)
        valid, bounds = anchor.compute_bounds(root)
        rectangle = Gdk.Rectangle()
        rectangle.x = int(x + bounds.get_x()) if valid else int(x)
        rectangle.y = int(y + bounds.get_y()) if valid else int(y)
        rectangle.width, rectangle.height = 1, 1
        self._popover.set_pointing_to(rectangle)
        self._popover.popup()

    def _close_menu(self):
        if self._popover is not None:
            self._popover.popdown()

    def _add_to_playlist(self, playlist_id):
        added = self.playlists.add(playlist_id, self._context_ids)
        if self._playlist_id == playlist_id:
            self._reload_view()
        if added:
            playlist = self.playlists.get(playlist_id)
            self._scan_progress(f'Added {added} song' + ('' if added == 1 else 's') + f' to {playlist.name}')

    def _remove_from_playlist(self):
        if self._context_playlist_id is not None:
            self.playlists.remove(self._context_playlist_id, self._context_ids)
            if self._playlist_id == self._context_playlist_id:
                self._reload_view()

    def _move_playlist_song(self, direction):
        if (self._context_playlist_id is not None and self._context_playlist_id == self._playlist_id
                and not self._playlist_sorted() and len(self._context_ids) == 1):
            self.playlists.move(self._context_playlist_id, self._context_ids[0], direction)
            if self._playlist_id == self._context_playlist_id:
                self._reload_view()

    def _dialog(self, title, accept_label):
        self._close_menu()
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dialog.set_default_size(340, -1)
        dialog.add_button('Cancel', Gtk.ResponseType.CANCEL)
        dialog.add_button(accept_label, Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        self._dialogs.add(dialog)
        return dialog

    def _finish_dialog(self, dialog):
        dialog.set_focus(None)
        self.view.grab_focus()
        dialog.set_visible(False)
        self._dialogs.discard(dialog)
        # Let native focus-out/input-method events run before releasing the entry.
        def release():
            dialog.destroy()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(release)

    def _name_dialog(self, playlist_id=None, song_ids=()):
        playlist = self.playlists.get(playlist_id) if playlist_id is not None else None
        dialog = self._dialog('Rename Playlist' if playlist else 'Create Playlist', 'Rename' if playlist else 'Create')
        content = dialog.get_content_area()
        content.set_spacing(8)
        self._margins(content, 16)
        content.append(Gtk.Label(label='Playlist name', xalign=0))
        entry = Gtk.Entry(text=playlist.name if playlist else '', activates_default=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ['Playlist name'])
        error = Gtk.Label(xalign=0, wrap=True, visible=False)
        error.add_css_class('error')
        content.append(entry)
        content.append(error)
        dialog.name_entry, dialog.error_label = entry, error
        def response(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                try:
                    if playlist is None:
                        self.playlists.create(entry.get_text(), song_ids)
                    else:
                        renamed = self.playlists.rename(playlist.id, entry.get_text())
                        if self._playlist_id == playlist.id:
                            self._heading.set_text(renamed.name)
                            self._heading.set_tooltip_text(renamed.name)
                except (ValueError, sqlite3.Error, OSError) as exc:
                    error.set_text(str(exc))
                    error.set_visible(True)
                    return
                self._refresh_sidebar()
            self._finish_dialog(dialog)
        dialog.connect('response', response)
        dialog.present()
        entry.grab_focus()
        return dialog

    def _delete_dialog(self, playlist_id):
        playlist = self.playlists.get(playlist_id)
        dialog = self._dialog('Delete Playlist', 'Delete')
        content = dialog.get_content_area()
        self._margins(content, 16)
        content.append(Gtk.Label(label=f'Delete “{playlist.name}”?\nYour music and listening history will be kept.', wrap=True, xalign=0))
        dialog.get_widget_for_response(Gtk.ResponseType.ACCEPT).add_css_class('destructive-action')
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        def response(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                def remove():
                    self.playlists.delete(playlist_id)
                    if self._playlist_id == playlist_id:
                        self._show_view(None)
                    self._refresh_sidebar()
                self._perform(remove)
            self._finish_dialog(dialog)
        dialog.connect('response', response)
        dialog.present()
        return dialog
