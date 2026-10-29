"""Permanent GTK player bar. Playback policy lives in PlaybackController."""

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from .song_model import format_duration, thumbnail_texture


class _PlayerScale(Gtk.Scale):
    """Prefer a longer trough while allowing GTK to shrink it at narrow widths."""

    def __init__(self, maximum, step, natural_width):
        self._natural_width = natural_width
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL,
                         adjustment=Gtk.Adjustment(lower=0, upper=maximum, step_increment=step,
                                                   page_increment=step * 10), draw_value=False)

    def do_measure(self, orientation, for_size):
        minimum, natural, _, _ = Gtk.Scale.do_measure(self, orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            natural = max(minimum, self._natural_width)
        return minimum, natural, -1, -1


class PlayerBar(Gtk.Box):
    def __init__(self, controller, perform):
        super().__init__(spacing=12)
        self.controller, self.perform = controller, perform
        self._updating = False
        self._dragging = False
        self._seek_source = None
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(self, f'set_margin_{side}')(10)
        # Equal outer columns keep playback centered even with long metadata.
        self.layout = Gtk.Grid(column_homogeneous=True, hexpand=True)
        self.append(self.layout)
        left = Gtk.Box(spacing=10, hexpand=True)
        self.cover = Gtk.Image(pixel_size=40)
        self.cover.set_size_request(40, 40)
        left.append(self.cover)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True,
                          valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(label='Nothing playing', xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.status = Gtk.Label(label='Select a song to play.', xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.set_max_width_chars(32)
        self.artist.set_max_width_chars(32)
        self.status.set_max_width_chars(32)
        self.artist.add_css_class('dim-label')
        self.status.add_css_class('dim-label')
        for label in (self.title, self.artist, self.status):
            details.append(label)
        left.append(details)
        self.layout.attach(left, 0, 0, 1, 1)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                         valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        center.set_size_request(260, -1)
        controls = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        self.shuffle = self._button('media-playlist-shuffle-symbolic', 'Shuffle off', controller.toggle_shuffle, True)
        self.previous = self._button('media-skip-backward-symbolic', 'Previous (Ctrl+Left)', controller.previous)
        self.play = self._button('media-playback-start-symbolic', 'Play (Space)', self._play_clicked)
        self.next = self._button('media-skip-forward-symbolic', 'Next (Ctrl+Right)', controller.next)
        self.repeat = self._button('media-playlist-repeat-symbolic', 'Repeat off', controller.cycle_repeat, True)
        for button in (self.shuffle, self.previous, self.play, self.next, self.repeat):
            controls.append(button)
        center.append(controls)
        timeline = Gtk.Box(spacing=4)
        self.elapsed = Gtk.Label(label='0:00', width_chars=5, xalign=1)
        self.total = Gtk.Label(label='-:--', width_chars=5, xalign=0)
        self.progress = _PlayerScale(1, 0.1, 300)
        self.progress.set_hexpand(True)
        self.progress.set_size_request(130, -1)
        self.progress.set_tooltip_text('Playback position')
        self.progress.update_property([Gtk.AccessibleProperty.LABEL], ['Playback position'])
        self.progress.connect('value-changed', self._progress_changed)
        # Observe press/release without taking the scale's own drag gesture away.
        pointer = Gtk.GestureDrag.new()
        pointer.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        pointer.connect('drag-begin', lambda *_: self.begin_seek())
        pointer.connect('drag-end', lambda *_: self.finish_seek())
        pointer.connect('cancel', lambda *_: self.cancel_seek())
        self.progress.add_controller(pointer)
        # Share the scale's native drag sequence so it cannot deny our release.
        controllers = self.progress.observe_controllers()
        for index in range(controllers.get_n_items()):
            native = controllers.get_item(index)
            if isinstance(native, Gtk.GestureDrag) and native is not pointer:
                pointer.group(native)
        for child in (self.elapsed, self.progress, self.total):
            timeline.append(child)
        center.append(timeline)
        self.layout.attach(center, 1, 0, 2, 1)

        right = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
        self.mute = self._button('audio-volume-high-symbolic', 'Mute (Ctrl+M)', controller.toggle_mute)
        right.append(self.mute)
        self.volume = _PlayerScale(100, 1, 140)
        self.volume.set_size_request(70, -1)
        self.volume.set_tooltip_text('Volume')
        self.volume.update_property([Gtk.AccessibleProperty.LABEL], ['Volume'])
        self.volume.connect('value-changed', self._volume_changed)
        right.append(self.volume)
        self.layout.attach(right, 3, 0, 1, 1)
        self.update('track')
        self.update('modes')
        self.update('volume')

    def _button(self, icon, tooltip, action, toggle=False):
        button = Gtk.ToggleButton() if toggle else Gtk.Button()
        button.set_icon_name(icon)
        button.set_tooltip_text(tooltip)
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
        button.add_css_class('flat')
        button.connect('clicked', lambda _: self.perform(action))
        return button

    def _play_clicked(self):
        # The window supplies selection-to-queue behavior only when no track is loaded.
        self.activate_action('win.play-pause', None)

    def update(self, event):
        controller = self.controller
        if event == 'state' and controller.state in ('stopped', 'error', 'eos'):
            self.cancel_seek()
        if event == 'track':
            self.cancel_seek()
            song = controller.current
            self.title.set_text(song.title if song else 'Nothing playing')
            self.artist.set_text(f'{song.artist} · {song.album}' if song else '')
            for label in (self.title, self.artist):
                label.set_tooltip_text(label.get_text())
            texture = thumbnail_texture(song.artwork_path) if song else None
            if texture:
                self.cover.set_from_paintable(texture)
            else:
                self.cover.set_from_icon_name('audio-x-generic-symbolic')
        if event in ('track', 'state', 'message'):
            self.status.set_text(controller.message)
            self.status.set_tooltip_text(controller.message)
            active = controller.state in ('playing', 'loading', 'buffering')
            self.play.set_icon_name('media-playback-pause-symbolic' if active else 'media-playback-start-symbolic')
            label = 'Pause (Space)' if active else 'Play (Space)'
            self.play.set_tooltip_text(label)
            self.play.update_property([Gtk.AccessibleProperty.LABEL], [label])
            self.previous.set_sensitive(bool(controller.queue))
            self.next.set_sensitive(bool(controller.queue))
        if event == 'modes':
            self.shuffle.set_active(controller.shuffle)
            label = 'Shuffle on' if controller.shuffle else 'Shuffle off'
            self.shuffle.set_tooltip_text(label)
            self.shuffle.update_property([Gtk.AccessibleProperty.LABEL], [label])
            self.repeat.set_active(controller.repeat != 'off')
            self.repeat.set_icon_name('media-playlist-repeat-song-symbolic' if controller.repeat == 'one'
                                      else 'media-playlist-repeat-symbolic')
            self.repeat.set_tooltip_text(f'Repeat {controller.repeat}')
            self.repeat.update_property([Gtk.AccessibleProperty.LABEL], [f'Repeat {controller.repeat}'])
        if event == 'volume':
            self._updating = True
            self.volume.set_value(controller.audio.volume * 100)
            self._updating = False
            muted, volume = controller.audio.muted, controller.audio.volume
            level = 'muted' if muted or volume == 0 else 'low' if volume < 0.33 else 'medium' if volume < 0.67 else 'high'
            self.mute.set_icon_name(f'audio-volume-{level}-symbolic')
            self.mute.set_tooltip_text('Unmute (Ctrl+M)' if muted else 'Mute (Ctrl+M)')
            self.mute.update_property([Gtk.AccessibleProperty.LABEL], [self.mute.get_tooltip_text()])
            self.volume.set_tooltip_text(f'Volume: {volume:.0%}')
        self.update_progress()

    def update_progress(self):
        if self._dragging or self._seek_source is not None:
            return
        duration = self.controller.duration
        position = self.controller.position
        self._updating = True
        self.progress.set_range(0, duration or 1)
        self.progress.set_sensitive(bool(duration and self.controller.state in ('playing', 'paused', 'buffering')))
        self.progress.set_value(min(position, duration) if duration else 0)
        self._updating = False
        self.elapsed.set_text(format_duration(position))
        self.total.set_text(format_duration(duration) if duration else '-:--')

    def begin_seek(self):
        self._dragging = True
        if self._seek_source is not None:
            GLib.source_remove(self._seek_source)
            self._seek_source = None

    def finish_seek(self):
        if self._dragging:
            self._dragging = False
            self._schedule_seek(1)

    def _progress_changed(self, scale):
        if self._updating:
            return
        self.elapsed.set_text(format_duration(scale.get_value()))
        if not self._dragging:
            self._schedule_seek(150)

    def _schedule_seek(self, delay):
        if self._seek_source is not None:
            GLib.source_remove(self._seek_source)
        self._seek_source = GLib.timeout_add(delay, self._commit_seek)

    def _commit_seek(self):
        self._seek_source = None
        self.perform(lambda: self.controller.seek(self.progress.get_value()))
        return GLib.SOURCE_REMOVE

    def cancel_seek(self):
        self._dragging = False
        if self._seek_source is not None:
            GLib.source_remove(self._seek_source)
            self._seek_source = None

    def _volume_changed(self, scale):
        if not self._updating:
            self.perform(lambda: self.controller.set_volume(scale.get_value() / 100))
