"""Native Statistics view. Database work stays on a single background worker.

Extends the existing GTK theme: compact summary, plain ranked rows, and one
short activity chart. The window owns navigation and the permanent player bar.
"""

import logging
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from .song_model import thumbnail_texture
from .statistics import StatisticsReader, listening_time, month_label

logger = logging.getLogger(__name__)


class StatisticsIcon(Gtk.DrawingArea):
    """Theme-colored bar chart, including themes without a statistics icon."""

    def __init__(self):
        super().__init__(width_request=16, height_request=16, valign=Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def _draw(self, area, context, width, height):
        color = self.get_style_context().get_color()
        context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        for x, y, h in ((1, 9, 6), (6, 5, 10), (11, 1, 14)):
            context.rectangle(x, y, 3, h)
        context.fill()


class ActivityChart(Gtk.DrawingArea):
    def __init__(self):
        super().__init__(hexpand=True, content_height=180)
        self.values = ()
        self.monthly = False
        self.set_draw_func(self._draw)
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Listening time bar chart. Exact values follow below.'])

    def update(self, activity, monthly):
        self.values, self.monthly = activity, monthly
        self.queue_draw()

    def _draw(self, area, context, width, height):
        if not self.values:
            return
        color = self.get_style_context().get_color()
        context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        context.select_font_face('Sans')
        context.set_font_size(11)
        peak = max(item.seconds for item in self.values)
        # Rounded scale in minutes or hours, with headroom for short listens.
        unit, suffix = (3600, 'h') if peak >= 3600 else (60, 'min')
        ceiling = max(1, math.ceil(peak / unit))
        top, bottom, left, right = 14, height - 36, 50, width - 8
        usable = max(1, bottom - top)
        for fraction in (0, 0.5, 1):
            y = bottom - usable * fraction
            context.set_source_rgba(color.red, color.green, color.blue, color.alpha * 0.2)
            context.set_line_width(1)
            context.move_to(left, y)
            context.line_to(right, y)
            context.stroke()
            context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
            context.move_to(0, y + 4)
            context.show_text(f'{ceiling * fraction:g} {suffix}')
        step = max(1, (right - left) / len(self.values))
        for index, item in enumerate(self.values):
            x = left + index * step
            bar_height = usable * item.seconds / (ceiling * unit)
            context.set_source_rgba(color.red, color.green, color.blue, color.alpha * 0.75)
            context.rectangle(x + step * 0.18, bottom - bar_height, step * 0.64, bar_height)
            context.fill()
            if self.monthly:
                stamp = date.fromisoformat(item.period + '-01')
                label = stamp.strftime('%b')
                show = step >= 34 or index % 2 == 0 or index == len(self.values) - 1
            else:
                label = str(index + 1)
                show = index == 0 or (index + 1) % 5 == 0 and index < len(self.values) - 3 or index == len(self.values) - 1
            if show:
                context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
                extents = context.text_extents(label)
                context.move_to(x + step / 2 - extents.width / 2, bottom + 17)
                context.show_text(label)
                if self.monthly and (index == 0 or stamp.month == 1):
                    context.move_to(x + step / 2 - 13, bottom + 31)
                    context.show_text(str(stamp.year))


class StatisticsPage(Gtk.ScrolledWindow):
    REFRESH_SECONDS = 10

    def __init__(self, database):
        super().__init__(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.reader = StatisticsReader(database)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='statistics')
        self._future = None
        self._timer = None
        self._active = False
        self._closed = False
        self._refresh_again = False
        self._updating_selector = False
        self.period = None
        self.data = None
        self._periods = [None]
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(content, f'set_margin_{side}')(16)
        self.set_child(content)
        heading = Gtk.Label(label='Statistics', xalign=0)
        heading.add_css_class('title-2')
        content.append(heading)
        filter_row = Gtk.Box(spacing=12)
        filter_row.append(Gtk.Label(label='Period', xalign=0))
        self.selector = Gtk.DropDown.new_from_strings(['All Time'])
        self.selector.update_property([Gtk.AccessibleProperty.LABEL], ['Statistics period'])
        self.selector.connect('notify::selected', self._period_changed)
        filter_row.append(self.selector)
        content.append(filter_row)
        self.message = Gtk.Label(label='Loading statistics…', xalign=0, wrap=True)
        self.message.add_css_class('dim-label')
        content.append(self.message)
        summary = Gtk.Box(spacing=12, homogeneous=True)
        self.metrics = []
        for title in ('Total Listening Time', 'Total Plays', 'Unique Songs'):
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            label = Gtk.Label(label=title, xalign=0, wrap=True)
            label.add_css_class('dim-label')
            value = Gtk.Label(label='0 s' if not self.metrics else '0', xalign=0, wrap=True)
            value.add_css_class('title-3')
            group.append(label)
            group.append(value)
            summary.append(group)
            self.metrics.append(value)
        content.append(summary)
        self.results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.append(self.results)
        self.rankings = {}
        for title in ('Top Songs', 'Top Artists', 'Top Albums'):
            heading = Gtk.Label(label=title, xalign=0, margin_top=12)
            heading.add_css_class('heading')
            self.results.append(heading)
            rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.results.append(rows)
            self.rankings[title] = rows
        self.chart_heading = Gtk.Label(label='Listening Activity · Last 12 Months', xalign=0, margin_top=12, wrap=True)
        self.chart_heading.add_css_class('heading')
        self.results.append(self.chart_heading)
        self.chart = ActivityChart()
        self.results.append(self.chart)
        self.activity_details = Gtk.Expander(label='Listening time values')
        self.activity_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.activity_details.set_child(self.activity_rows)
        self.results.append(self.activity_details)

    def set_active(self, active):
        if self._closed:
            return
        self._active = active
        if active:
            self.refresh()
            if self._timer is None:
                self._timer = GLib.timeout_add_seconds(self.REFRESH_SECONDS, self._on_timer)
        elif self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def _on_timer(self):
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def _period_changed(self, selector, _property):
        if self._updating_selector:
            return
        self.period = self._periods[selector.get_selected()]
        self.message.set_text('Loading statistics…')
        self.message.set_visible(True)
        self.results.set_visible(False)
        for metric in self.metrics:
            metric.set_text('…')
        self.refresh()

    def refresh(self):
        if not self._active or self._closed:
            return
        if self._future is not None:
            self._refresh_again = True
            return
        period = self.period
        self._future = self._executor.submit(self.reader.read, period)
        self._future.add_done_callback(lambda future: GLib.idle_add(self._deliver, future, period)
                                       if not self._closed else None)

    def _deliver(self, future, period):
        self._future = None
        if self._closed:
            return GLib.SOURCE_REMOVE
        if self._active and period == self.period:
            try:
                data = future.result()
            except (OSError, sqlite3.Error, ValueError) as exc:
                logger.warning('Statistics read failed: %s', exc)
                self.message.set_text('Could not read listening statistics. Retrying automatically in a few seconds.')
                self.message.set_visible(True)
            else:
                if data != self.data or not self.results.get_visible():
                    self._render(data)
                else:
                    self._show_message(data)
        if self._refresh_again:
            self._refresh_again = False
            self.refresh()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _clear(box):
        while box.get_first_child() is not None:
            box.remove(box.get_first_child())

    def _show_message(self, data):
        messages = list(data.warnings)
        if not data.seconds and not data.plays:
            messages.insert(0, 'No listening activity in this month.' if self.period else
                            'No listening history yet. Play some music to start tracking your statistics.')
        self.message.set_text('\n'.join(messages))
        self.message.set_visible(bool(messages))

    def _render(self, data):
        self.data = data
        periods = [None, *sorted(set(data.months) | ({self.period} if self.period else set()), reverse=True)]
        if periods != self._periods:
            self._updating_selector = True
            self._periods = periods
            self.selector.set_model(Gtk.StringList.new(['All Time', *(month_label(month) for month in periods[1:])]))
            self.selector.set_selected(periods.index(self.period))
            self._updating_selector = False
        for widget, text in zip(self.metrics, (listening_time(data.seconds), str(data.plays), str(data.unique_songs))):
            widget.set_text(text)
        self.metrics[0].set_tooltip_text(f'{data.seconds:,.2f} seconds of recorded listening')
        self._show_message(data)
        self.results.set_visible(True)
        for title, entries in (('Top Songs', data.songs), ('Top Artists', data.artists), ('Top Albums', data.albums)):
            box = self.rankings[title]
            self._clear(box)
            if not entries:
                label = Gtk.Label(label='No qualified plays yet.' if title == 'Top Songs' else 'No activity yet.', xalign=0)
                label.add_css_class('dim-label')
                box.append(label)
            for rank, entry in enumerate(entries, 1):
                box.append(self._ranking_row(rank, entry, title != 'Top Artists'))
        self.chart_heading.set_text('Listening Activity · ' + (month_label(self.period) if self.period else 'Last 12 Months'))
        self.chart.update(data.activity, self.period is None)
        self._clear(self.activity_rows)
        for entry in data.activity:
            label = month_label(entry.period) if self.period is None else date.fromisoformat(entry.period).strftime('%b %-d')
            row = Gtk.Label(label=f'{label}: {listening_time(entry.seconds)} ({entry.seconds:,.2f} s)', xalign=0, wrap=True)
            self.activity_rows.append(row)

    @staticmethod
    def _ranking_row(rank, entry, cover):
        row = Gtk.Box(spacing=10)
        row.append(Gtk.Label(label=str(rank), width_chars=2, xalign=1))
        if cover:
            image = Gtk.Image(pixel_size=32)
            image.set_size_request(32, 32)
            texture = thumbnail_texture(entry.artwork_path)
            if texture is None:
                image.set_from_icon_name('audio-x-generic-symbolic')
            else:
                image.set_from_paintable(texture)
            row.append(image)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=entry.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        title.set_tooltip_text(entry.title)
        text.append(title)
        if cover:
            artist = Gtk.Label(label=entry.artist, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            artist.add_css_class('dim-label')
            artist.set_tooltip_text(entry.artist)
            text.append(artist)
        row.append(text)
        counts = Gtk.Label(label=f'{entry.plays} ' + ('play' if entry.plays == 1 else 'plays') +
                           f'\n{listening_time(entry.seconds)}', xalign=1, justify=Gtk.Justification.RIGHT)
        counts.set_tooltip_text(f'{entry.seconds:,.2f} seconds')
        row.append(counts)
        return row

    def close(self):
        self.set_active(False)
        self._closed = True
        self._executor.submit(self.reader.close)
        self._executor.shutdown(wait=False)
