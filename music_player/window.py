"""Compact All Songs library with native GTK4 filtering and column sorting."""

import sqlite3
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from .controller import PlaybackController
from .player_bar import PlayerBar
from .shortcuts import WindowShortcuts
from .library import LibraryDatabase
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
        self.scanner = None
        self._items = {}
        self._query = ""
        self._database = database_path()
        try:
            self.controller = PlaybackController(self._database, self._playback_changed)
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError(f'Could not open listening history: {exc}') from exc
        self.player = self.controller.audio
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
        sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        sidebar.add_css_class("navigation-sidebar")
        sidebar.set_size_request(160, -1)
        navigation = Gtk.Box(spacing=10)
        navigation.append(Gtk.Image.new_from_icon_name("audio-x-generic-symbolic"))
        navigation.append(Gtk.Label(label="All Songs", xalign=0))
        row = Gtk.ListBoxRow(child=navigation)
        sidebar.append(row)
        sidebar.select_row(row)
        body.append(sidebar)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        body.append(main)
        toolbar = Gtk.Box(spacing=12)
        self._margins(toolbar, 12)
        heading = Gtk.Label(label="All Songs", xalign=0, hexpand=True)
        heading.add_css_class("title-2")
        toolbar.append(heading)
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
        self.selection = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.selection.connect("notify::selected-item", lambda *_: self._update_controls())
        self.view = Gtk.ColumnView(model=self.selection, show_row_separators=True)
        self.view.set_single_click_activate(False)
        self.view.connect("activate", self._activate_song)
        self._add_columns()
        self.sorted.set_sorter(self.view.get_sorter())
        self.view.sort_by_column(self._title_column, Gtk.SortType.ASCENDING)
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
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
            texture = thumbnail_texture(item.get_item().song.artwork_path)
            if texture is None:
                image.set_from_icon_name("audio-x-generic-symbolic")
            else:
                image.set_from_paintable(texture)

        cover_factory.connect("setup", cover_setup)
        cover_factory.connect("bind", cover_bind)
        self.view.append_column(Gtk.ColumnViewColumn(title="", factory=cover_factory, fixed_width=64))

        for title, field, width in (("Title", "title", 220), ("Artist", "artist", 160),
                                    ("Album", "album", 180), ("Duration", "duration", 90)):
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", self._cell_setup, field)
            factory.connect("bind", self._cell_bind, field)
            factory.connect("unbind", self._cell_unbind, field)
            column = Gtk.ColumnViewColumn(title=title, factory=factory, resizable=True,
                                          expand=field != "duration", fixed_width=width)
            column.set_sorter(Gtk.CustomSorter.new(lambda a, b, _data, field=field: compare_songs(a, b, field)))
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
        label = child.get_last_child() if field == "title" else child
        value = getattr(song_item.song, field)
        text = format_duration(value) if field == "duration" else value
        label.set_text(text)
        label.set_tooltip_text(text)
        if field == "title":
            marker = child.get_first_child()
            marker.set_visible(song_item.playing)
            item._playing_handler = song_item.connect("notify::playing", lambda song, _: marker.set_visible(song.playing))

    def _cell_unbind(self, factory, item, field):
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
        self._count.set_text(f"{count} of {total} songs" if self._query.strip() else f"{total} songs")
        self._empty.set_visible(count == 0)
        self._empty.set_text("No matching songs. Try a different search." if self._query.strip() else
                             "No songs yet.\nAdd audio files to your music folder, then choose Rescan Library.")

    def _update_songs(self, songs):
        selected = self.selection.get_selected_item()
        selected_id = selected.song.id if selected else None
        for song in songs:
            previous = self._items.get(song.id)
            item = SongItem(song)
            item.playing = bool(previous and previous.playing)
            self._items[song.id] = item
            if previous is not None:
                found, position = self.store.find(previous)
                if found:
                    self.store.splice(position, 1, [item])
            else:
                self.store.append(item)
        if selected_id is not None:
            self._select_id(selected_id)
        self._update_count()

    def _select_id(self, song_id):
        for position in range(self.sorted.get_n_items()):
            if self.sorted.get_item(position).song.id == song_id:
                self.selection.set_selected(position)
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
        if self.sorted.get_item(position) is not None:
            self.selection.set_selected(position)
            snapshot = [self.sorted.get_item(index).song for index in range(self.sorted.get_n_items())]
            self._perform(lambda: self.controller.activate(snapshot, position))

    def _load_and_play(self, song):
        self.controller.activate([song])

    def _play_selected(self):
        if self.controller.current is None and self.controller.audio.path is None:
            position = self.selection.get_selected()
            if position != Gtk.INVALID_LIST_POSITION:
                self._activate_song(self.view, position)
        else:
            self.controller.play_pause()

    def _playback_changed(self, event):
        if self._closed or not hasattr(self, 'bar'):
            return
        self.bar.update(event)
        if event in ('track', 'state'):
            for item in self._items.values():
                item.playing = self.controller.state == 'playing' and item.song.id == self.controller.current_song_id
            if event == 'track' and self.controller.current_song_id is not None:
                self._select_id(self.controller.current_song_id)
        self._update_controls()

    def _update_controls(self):
        if hasattr(self, 'bar'):
            self.bar.play.set_sensitive(self.controller.current is not None or self.player.path is not None or
                                        self.selection.get_selected_item() is not None)

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
        except (OSError, ValueError, RuntimeError) as exc:
            self._show_status(f"Error: {exc}")

    def _open_file(self, button):
        if self._chooser is not None:
            self._chooser.show()
            return
        self._chooser = Gtk.FileChooserNative.new(
            "Select local audio", self, Gtk.FileChooserAction.OPEN, "Open", "Cancel"
        )
        self._chooser.connect("response", self._file_response)
        self._chooser.show()

    def _file_response(self, chooser, response):
        try:
            if response == Gtk.ResponseType.ACCEPT:
                file = chooser.get_file()
                path = file.get_path() if file is not None else None
                if path is None:
                    raise ValueError("Only local files are supported.")
                self.controller.open_file(path)
        except (OSError, ValueError, RuntimeError) as exc:
            self._show_status(f"Error: {exc}")
        finally:
            chooser.destroy()
            self._chooser = None

    def close_player(self):
        if self._closed:
            return
        self._closed = True
        if hasattr(self, '_tick_source'):
            GLib.source_remove(self._tick_source)
        if hasattr(self, 'bar'):
            self.bar.cancel_seek()
        if self.scanner is not None:
            self.scanner.close()
        if self._chooser is not None:
            self._chooser.destroy()
            self._chooser = None
        self.controller.close()

    def _on_close(self, window):
        self.close_player()
        return False
