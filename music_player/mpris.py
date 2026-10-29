"""MPRIS 2 adapter on Gio's session bus. PlaybackController remains authoritative.

Reference: https://specifications.freedesktop.org/mpris/latest/
The Properties interface is explicit so invalid writes return useful D-Bus errors
without relying on Python exceptions crossing a C property-setter callback.
"""

import logging
import math
import re
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

from gi.repository import Gio, GLib

from .metadata import SUPPORTED_EXTENSIONS

BUS_NAME = 'org.mpris.MediaPlayer2.musicplayer'
OBJECT_PATH = '/org/mpris/MediaPlayer2'
ROOT = 'org.mpris.MediaPlayer2'
PLAYER = ROOT + '.Player'
PROPERTIES = 'org.freedesktop.DBus.Properties'
DESKTOP_ENTRY = 'io.github.OscarGR5.MusicPlayer'
MIME_TYPES = ('audio/mpeg', 'audio/flac', 'audio/x-flac', 'audio/mp4', 'audio/x-m4a',
              'audio/ogg', 'application/ogg', 'audio/opus', 'audio/wav', 'audio/x-wav', 'audio/vnd.wave')
LOOPS = {'off': 'None', 'all': 'Playlist', 'one': 'Track'}
logger = logging.getLogger(__name__)

INTROSPECTION = '''<node>
<interface name="org.mpris.MediaPlayer2">
 <method name="Raise"/><method name="Quit"/>
 <property name="CanQuit" type="b" access="read"/>
 <property name="CanRaise" type="b" access="read"/>
 <property name="HasTrackList" type="b" access="read"/>
 <property name="Identity" type="s" access="read"/>
 <property name="DesktopEntry" type="s" access="read"/>
 <property name="SupportedUriSchemes" type="as" access="read"/>
 <property name="SupportedMimeTypes" type="as" access="read"/>
 <property name="Fullscreen" type="b" access="readwrite"/>
 <property name="CanSetFullscreen" type="b" access="read"/>
</interface>
<interface name="org.mpris.MediaPlayer2.Player">
 <method name="Next"/><method name="Previous"/><method name="Pause"/>
 <method name="PlayPause"/><method name="Stop"/><method name="Play"/>
 <method name="Seek"><arg name="Offset" type="x" direction="in"/></method>
 <method name="SetPosition"><arg name="TrackId" type="o" direction="in"/><arg name="Position" type="x" direction="in"/></method>
 <method name="OpenUri"><arg name="Uri" type="s" direction="in"/></method>
 <signal name="Seeked"><arg name="Position" type="x"/></signal>
 <property name="PlaybackStatus" type="s" access="read"/>
 <property name="LoopStatus" type="s" access="readwrite"/>
 <property name="Rate" type="d" access="readwrite"/>
 <property name="Shuffle" type="b" access="readwrite"/>
 <property name="Metadata" type="a{sv}" access="read"/>
 <property name="Volume" type="d" access="readwrite"/>
 <property name="Position" type="x" access="read"><annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="false"/></property>
 <property name="MinimumRate" type="d" access="read"/>
 <property name="MaximumRate" type="d" access="read"/>
 <property name="CanGoNext" type="b" access="read"/>
 <property name="CanGoPrevious" type="b" access="read"/>
 <property name="CanPlay" type="b" access="read"/>
 <property name="CanPause" type="b" access="read"/>
 <property name="CanSeek" type="b" access="read"/>
 <property name="CanControl" type="b" access="read"><annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="false"/></property>
</interface>
<interface name="org.freedesktop.DBus.Properties">
 <method name="Get"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
 <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
 <method name="Set"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/></method>
 <signal name="PropertiesChanged"><arg type="s"/><arg type="a{sv}"/><arg type="as"/></signal>
</interface>
</node>'''


def microseconds(seconds):
    return min(2**63 - 1, max(0, round(seconds * 1_000_000))) if seconds is not None and math.isfinite(seconds) else 0


def local_audio_path(uri):
    """Reject remote authorities, queries, fragments and malformed encodings."""
    parts = urlsplit(uri)
    if parts.scheme != 'file' or parts.netloc not in ('', 'localhost') or parts.query or parts.fragment:
        raise ValueError('Only local file URIs are supported.')
    if re.search(r'%(?![0-9A-Fa-f]{2})', parts.path):
        raise ValueError('Malformed percent encoding in file URI.')
    path = Path(unquote(parts.path, errors='strict'))
    if not path.is_absolute() or '\0' in str(path) or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError('Choose a supported local audio file URI.')
    return path


class MprisService:
    def __init__(self, controller, raise_window, quit_application, report=None):
        self.controller = controller
        self.raise_window, self.quit_application = raise_window, quit_application
        self.report = report or (lambda text: logger.warning('%s', text))
        self.connection = None
        self._owner = 0
        self._registrations = []
        self._closed = False
        self.ready = False
        self.track_id = self._new_track_id()
        self._last = {}
        controller.add_listener(self._changed)

    def _new_track_id(self):
        return '/io/github/OscarGR5/MusicPlayer/track/t_' + uuid.uuid4().hex

    def start(self, connection=None):
        if self._owner or self._closed:
            return
        flags = Gio.BusNameOwnerFlags.DO_NOT_QUEUE
        if connection is None:
            self._owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, flags,
                                         self._bus_acquired, self._name_acquired, self._name_lost)
        else:
            self._bus_acquired(connection, BUS_NAME)
            if not self._closed:
                self._owner = Gio.bus_own_name_on_connection(connection, BUS_NAME, flags,
                                                           self._name_acquired, self._name_lost)

    def _bus_acquired(self, connection, name):
        if self._closed:
            return
        self.connection = connection
        connection.set_exit_on_close(False)
        try:
            info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
            for interface in info.interfaces:
                self._registrations.append(connection.register_object(OBJECT_PATH, interface, self._method,
                                                                       self._get_property, None))
        except GLib.Error as exc:
            self.report(f'Desktop media controls unavailable: {exc.message}')
            self.close()

    def _name_acquired(self, connection, name):
        if not self._closed:
            self.ready = True
            self._changed('initial')

    def _name_lost(self, connection, name):
        if not self._closed:
            self.report('Desktop media controls unavailable: session D-Bus is unavailable or the MPRIS name is in use.')
            self.close()

    def _metadata(self):
        song = self.controller.current
        if song is None:
            return {}
        result = {'mpris:trackid': GLib.Variant('o', self.track_id),
                  'xesam:title': GLib.Variant('s', song.title),
                  'xesam:artist': GLib.Variant('as', [song.artist]),
                  'xesam:album': GLib.Variant('s', song.album)}
        duration = self.controller.duration
        if duration is not None and duration > 0:
            result['mpris:length'] = GLib.Variant('x', microseconds(duration))
        if song.artwork_path and Path(song.artwork_path).is_file():
            result['mpris:artUrl'] = GLib.Variant('s', Path(song.artwork_path).resolve().as_uri())
        return result

    def properties(self, interface):
        v = GLib.Variant
        if interface == ROOT:
            return {'CanQuit': v('b', True), 'CanRaise': v('b', True), 'HasTrackList': v('b', False),
                    'Identity': v('s', 'MusicPlayer'), 'DesktopEntry': v('s', DESKTOP_ENTRY),
                    'SupportedUriSchemes': v('as', ['file']), 'SupportedMimeTypes': v('as', MIME_TYPES),
                    'Fullscreen': v('b', False), 'CanSetFullscreen': v('b', False)}
        if interface != PLAYER:
            raise KeyError(interface)
        c = self.controller
        return {'PlaybackStatus': v('s', {'playing': 'Playing', 'paused': 'Paused'}.get(c.state, 'Stopped')),
                'LoopStatus': v('s', LOOPS[c.repeat]), 'Shuffle': v('b', c.shuffle),
                'Rate': v('d', 1.0), 'MinimumRate': v('d', 1.0), 'MaximumRate': v('d', 1.0),
                'Metadata': v('a{sv}', self._metadata()), 'Volume': v('d', 0.0 if c.audio.muted else c.audio.volume),
                'Position': v('x', microseconds(c.position)), 'CanControl': v('b', True),
                'CanPlay': v('b', c.can_play), 'CanPause': v('b', c.can_play),
                'CanSeek': v('b', c.can_seek), 'CanGoNext': v('b', c.can_go_next),
                'CanGoPrevious': v('b', c.can_go_previous)}

    def _get_property(self, connection, sender, path, interface, name):
        return self.properties(interface)[name]

    def _write(self, interface, name, value):
        expected = {'LoopStatus': 's', 'Shuffle': 'b', 'Volume': 'd', 'Rate': 'd'} if interface == PLAYER else {'Fullscreen': 'b'}
        if name not in expected:
            return 'PropertyReadOnly', f'{name} is read-only.'
        if value.get_type_string() != expected[name]:
            return 'InvalidArgs', f'{name} requires type {expected[name]}.'
        value = value.unpack()
        c = self.controller
        if name == 'Fullscreen':
            return 'NotSupported', 'Fullscreen is not supported.'
        if name == 'LoopStatus':
            modes = {external: internal for internal, external in LOOPS.items()}
            if value not in modes:
                return 'InvalidArgs', 'LoopStatus must be None, Playlist or Track.'
            c.set_repeat(modes[value])
        elif name == 'Shuffle':
            if c.shuffle != value:
                c.toggle_shuffle()
        elif name == 'Volume':
            if not math.isfinite(value):
                return 'InvalidArgs', 'Volume must be finite.'
            c.set_volume(min(1.0, max(0.0, value)))
            if c.audio.muted and value > 0:
                c.toggle_mute()
        elif name == 'Rate':
            if value == 0:
                if c.state in ('playing', 'loading', 'buffering'):
                    c.pause()
            elif value != 1.0:
                return 'NotSupported', 'Only playback rate 1.0 is supported.'
        return None

    def _property_method(self, method, parameters, invocation):
        interface = parameters.get_child_value(0).get_string()
        try:
            values = self.properties(interface)
        except KeyError:
            return self._error(invocation, 'UnknownInterface', 'Unknown interface.')
        if method == 'GetAll':
            invocation.return_value(GLib.Variant('(a{sv})', (values,)))
            return
        name = parameters.get_child_value(1).get_string()
        if name not in values:
            return self._error(invocation, 'UnknownProperty', 'Unknown property.')
        if method == 'Get':
            invocation.return_value(GLib.Variant('(v)', (values[name],)))
        else:
            error = self._write(interface, name, parameters.get_child_value(2).get_variant())
            if error:
                return self._error(invocation, *error)
            invocation.return_value(None)

    @staticmethod
    def _error(invocation, name, message):
        invocation.return_dbus_error('org.freedesktop.DBus.Error.' + name, message)

    def _method(self, connection, sender, path, interface, method, parameters, invocation):
        try:
            if interface == PROPERTIES:
                return self._property_method(method, parameters, invocation)
            c = self.controller
            args = parameters.unpack()
            if interface == ROOT:
                if method == 'Raise':
                    self.raise_window()
                elif method == 'Quit':
                    # Reply before releasing the name and stopping the main loop.
                    invocation.return_value(None)
                    GLib.idle_add(self._quit)
                    return
            elif method == 'Play':
                if c.can_play and c.state not in ('playing', 'loading', 'buffering'):
                    c.play_pause()
            elif method == 'Pause':
                if c.can_play and c.state in ('playing', 'loading', 'buffering'):
                    c.pause()
            elif method == 'PlayPause':
                if not c.can_play:
                    return self._error(invocation, 'NotSupported', 'No playable track is loaded.')
                c.play_pause()
            elif method == 'Stop':
                c.stop()
            elif method == 'Next':
                if c.can_go_next:
                    c.next()
            elif method == 'Previous':
                if c.can_go_previous:
                    c.previous()
            elif method == 'Seek' and c.can_seek:
                target = max(0, c.position + args[0] / 1_000_000)
                if c.duration is not None and target > c.duration:
                    c.next()
                else:
                    c.seek(target)
            elif method == 'SetPosition' and c.can_seek:
                track, position = args
                if track == self.track_id and position >= 0 and (c.duration is None or position <= microseconds(c.duration)):
                    c.seek(position / 1_000_000)
            elif method == 'OpenUri':
                c.open_file(local_audio_path(args[0]))
            invocation.return_value(None)
        except (OSError, ValueError, RuntimeError, sqlite3.Error, GLib.Error) as exc:
            self._error(invocation, 'InvalidArgs' if isinstance(exc, ValueError) else 'Failed', str(exc))

    def _quit(self):
        if not self._closed:
            self.quit_application()
        return GLib.SOURCE_REMOVE

    def _changed(self, event):
        if event == 'track':
            self.track_id = self._new_track_id()
        if not self.ready or self._closed:
            return
        try:
            values = self.properties(PLAYER)
            values.pop('Position')
            values.pop('CanControl')
            changed = {name: value for name, value in values.items() if value != self._last.get(name)}
            if changed:
                self.connection.emit_signal(None, OBJECT_PATH, PROPERTIES, 'PropertiesChanged',
                                            GLib.Variant('(sa{sv}as)', (PLAYER, changed, [])))
                self._last = values
            if event == 'seeked':
                self.connection.emit_signal(None, OBJECT_PATH, PLAYER, 'Seeked',
                                            GLib.Variant('(x)', (microseconds(self.controller.position),)))
        except GLib.Error as exc:
            self.report(f'Desktop media controls unavailable: {exc.message}')
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed, self.ready = True, False
        self.controller.remove_listener(self._changed)
        if self._owner:
            Gio.bus_unown_name(self._owner)
            self._owner = 0
        if self.connection is not None:
            for registration in self._registrations:
                self.connection.unregister_object(registration)
        self._registrations.clear()
        self.connection = None
