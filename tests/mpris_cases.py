"""Explicitly collected only by test_mpris.py inside dbus-run-session."""

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from gi.repository import Gio, GLib

from music_player.mpris import BUS_NAME, DESKTOP_ENTRY, OBJECT_PATH, PLAYER, PROPERTIES, ROOT, MprisService
from test_audio import wait_for
from test_controller import controller  # noqa: F401 (pytest fixture)

assert os.environ.get('MUSICPLAYER_ISOLATED_DBUS') == '1'


def connection():
    instance = Gio.DBusConnection.new_for_address_sync(
        os.environ['DBUS_SESSION_BUS_ADDRESS'],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    instance.set_exit_on_close(False)
    return instance


def call(client, interface, method, parameters=None, destination=BUS_NAME, path=OBJECT_PATH):
    results = []
    def finished(client, result):
        try:
            results.append(client.call_finish(result))
        except GLib.Error as exc:
            results.append(exc)
    client.call(destination, path, interface, method, parameters, None, Gio.DBusCallFlags.NONE, 2000, None, finished)
    wait_for(lambda: results)
    if isinstance(results[0], Exception):
        raise results[0]
    return results[0]


def get(client, name, interface=PLAYER):
    return call(client, PROPERTIES, 'Get', GLib.Variant('(ss)', (interface, name))).unpack()[0]


def set_property(client, name, value, interface=PLAYER):
    return call(client, PROPERTIES, 'Set', GLib.Variant('(ssv)', (interface, name, value)))


def owns_name(client):
    return call(client, 'org.freedesktop.DBus', 'NameHasOwner', GLib.Variant('(s)', (BUS_NAME,)),
                destination='org.freedesktop.DBus', path='/org/freedesktop/DBus').unpack()[0]


@pytest.fixture
def service(controller):
    c, clock, _ = controller
    server, client = connection(), connection()
    reports, raised, quits = [], [], []
    instance = MprisService(c, lambda: raised.append(True), lambda: quits.append(True), reports.append)
    instance.start(server)
    wait_for(lambda: instance.ready)
    yield instance, c, clock, client, reports, raised, quits
    instance.close()
    client.close_sync(None)
    if not server.is_closed():
        server.close_sync(None)


def test_registration_introspection_signatures_and_idempotent_start(service):
    s, c, clock, client, reports, *_ = service
    assert owns_name(client)
    registrations = tuple(s._registrations)
    s.start()
    assert tuple(s._registrations) == registrations
    xml = call(client, 'org.freedesktop.DBus.Introspectable', 'Introspect').unpack()[0]
    info = Gio.DBusNodeInfo.new_for_xml(xml)
    player = info.lookup_interface(PLAYER)
    assert player.lookup_method('Seek').in_args[0].signature == 'x'
    assert [arg.signature for arg in player.lookup_method('SetPosition').in_args] == ['o', 'x']
    assert player.lookup_property('Metadata').signature == 'a{sv}'
    assert player.lookup_signal('Seeked').args[0].signature == 'x'
    assert get(client, 'Identity', ROOT) == 'MusicPlayer'
    assert get(client, 'DesktopEntry', ROOT) == DESKTOP_ENTRY
    assert get(client, 'SupportedUriSchemes', ROOT) == ['file']
    for name in ('HasTrackList', 'CanSetFullscreen', 'Fullscreen'):
        assert not get(client, name, ROOT)
    for name in ('CanQuit', 'CanRaise'):
        assert get(client, name, ROOT)
    assert not reports


def test_empty_player_controls_and_metadata(service):
    s, c, clock, client, *_ = service
    assert get(client, 'Metadata') == {}
    assert get(client, 'PlaybackStatus') == 'Stopped'
    assert get(client, 'Position') == 0
    for name in ('CanPlay', 'CanPause', 'CanSeek', 'CanGoNext', 'CanGoPrevious'):
        assert not get(client, name)
    for method in ('Play', 'Pause', 'Next', 'Previous', 'Stop'):
        call(client, PLAYER, method)
    assert c.current is None
    with pytest.raises(GLib.Error, match='NotSupported'):
        call(client, PLAYER, 'PlayPause')
    assert get(client, 'CanControl')


def test_play_pause_next_previous_stop_and_history(service, indexed_songs):
    s, c, clock, client, *_ = service
    c.activate(indexed_songs, 1)
    assert get(client, 'PlaybackStatus') == 'Playing'
    call(client, PLAYER, 'Play')
    assert c.state == 'playing'
    call(client, PLAYER, 'Pause')
    call(client, PLAYER, 'Pause')
    assert c.state == 'paused'
    call(client, PLAYER, 'Next')
    assert c.current_song_id == indexed_songs[2].id and c.state == 'paused'
    call(client, PLAYER, 'Previous')
    assert c.current_song_id == indexed_songs[1].id and c.state == 'paused'
    c.audio.position = 4
    call(client, PLAYER, 'Previous')
    assert c.current_song_id == indexed_songs[1].id and c.position == 0
    call(client, PLAYER, 'PlayPause')
    assert c.state == 'playing'
    clock.advance(1)
    c.audio.position = 1
    c.tick()
    call(client, PLAYER, 'Stop')
    assert c.state == 'stopped'
    row = c.history.db.connection.execute('SELECT listened_seconds,end_reason FROM playback_sessions ORDER BY rowid DESC LIMIT 1').fetchone()
    assert tuple(row) == (1, 'stop')
    call(client, PLAYER, 'Play')
    assert c.state == 'playing' and c.position == 0


def test_seeking_microseconds_stale_position_boundaries_and_history(service, indexed_songs):
    s, c, clock, client, *_ = service
    c.activate(indexed_songs)
    track = get(client, 'Metadata')['mpris:trackid']
    clock.advance(1)
    c.audio.position = 1
    c.tick()
    call(client, PLAYER, 'Seek', GLib.Variant('(x)', (1_250_000,)))
    assert c.position == 2.25 and get(client, 'Position') == 2_250_000
    call(client, PLAYER, 'Seek', GLib.Variant('(x)', (-9_000_000,)))
    assert c.position == 0
    call(client, PLAYER, 'SetPosition', GLib.Variant('(ox)', (track, 3_123_456)))
    assert c.position == 3.123456
    for invalid in (-1, 121_000_000):
        call(client, PLAYER, 'SetPosition', GLib.Variant('(ox)', (track, invalid)))
        assert c.position == 3.123456
    call(client, PLAYER, 'Next')
    call(client, PLAYER, 'SetPosition', GLib.Variant('(ox)', (track, 5_000_000)))
    assert c.current_song_id == indexed_songs[1].id and c.position == 0
    call(client, PLAYER, 'Seek', GLib.Variant('(x)', (121_000_000,)))
    assert c.current_song_id == indexed_songs[2].id
    c.stop()
    assert c.history.db.connection.execute('SELECT SUM(seconds) FROM listening_intervals').fetchone()[0] == 1
    assert c.history.db.connection.execute('SELECT COUNT(*) FROM qualified_plays').fetchone()[0] == 0


def test_shuffle_repeat_volume_rate_and_persistence(service, indexed_songs):
    s, c, clock, client, *_ = service
    c.activate(indexed_songs)
    set_property(client, 'Shuffle', GLib.Variant('b', True))
    assert c.shuffle and get(client, 'Shuffle')
    for external, internal in (('Track', 'one'), ('Playlist', 'all'), ('None', 'off')):
        set_property(client, 'LoopStatus', GLib.Variant('s', external))
        assert c.repeat == internal and get(client, 'LoopStatus') == external
    c.toggle_mute()
    assert get(client, 'Volume') == 0
    set_property(client, 'Volume', GLib.Variant('d', 0.37))
    assert not c.audio.muted and c.audio.volume == 0.37
    c.history.flush()
    assert c.history.db.connection.execute('SELECT volume FROM player_preferences').fetchone()[0] == 0.37
    set_property(client, 'Volume', GLib.Variant('d', -1))
    assert c.audio.volume == 0
    set_property(client, 'Volume', GLib.Variant('d', 1.2))
    assert c.audio.volume == 1
    for name in ('Rate', 'MinimumRate', 'MaximumRate'):
        assert get(client, name) == 1.0
    set_property(client, 'Rate', GLib.Variant('d', 0))
    assert c.state == 'paused' and get(client, 'Rate') == 1


@pytest.mark.parametrize('name,value,error', [
    ('Shuffle', GLib.Variant('s', 'yes'), 'InvalidArgs'),
    ('LoopStatus', GLib.Variant('s', 'invalid'), 'InvalidArgs'),
    ('Volume', GLib.Variant('d', float('nan')), 'InvalidArgs'),
    ('Volume', GLib.Variant('d', float('inf')), 'InvalidArgs'),
    ('Rate', GLib.Variant('d', 2.0), 'NotSupported'),
    ('Position', GLib.Variant('x', 0), 'PropertyReadOnly'),
    ('Missing', GLib.Variant('b', False), 'UnknownProperty'),
])
def test_invalid_property_writes_return_dbus_errors(service, name, value, error):
    client = service[3]
    with pytest.raises(GLib.Error, match=error):
        set_property(client, name, value)


def test_metadata_types_covers_external_and_automatic_transitions(service, indexed_songs, artwork, tmp_path, wav_file):
    s, c, clock, client, *_ = service
    cover = tmp_path / 'cover # café.png'
    cover.write_bytes(artwork)
    c.activate([replace(indexed_songs[0], artwork_path=str(cover)), indexed_songs[1]])
    variant = call(client, PROPERTIES, 'Get', GLib.Variant('(ss)', (PLAYER, 'Metadata'))).get_child_value(0).get_variant()
    assert variant.lookup_value('mpris:trackid', None).get_type_string() == 'o'
    assert variant.lookup_value('mpris:length', None).get_type_string() == 'x'
    assert variant.lookup_value('xesam:artist', None).get_type_string() == 'as'
    metadata = variant.unpack()
    assert metadata['mpris:length'] == 120_000_000
    assert metadata['xesam:title'] == indexed_songs[0].title
    assert metadata['xesam:album'] == indexed_songs[0].album
    assert metadata['xesam:artist'] == [indexed_songs[0].artist]
    assert metadata['mpris:artUrl'] == cover.as_uri()
    assert not metadata['mpris:trackid'].startswith('/org/mpris')
    track = metadata['mpris:trackid']
    call(client, PLAYER, 'Pause')
    call(client, PLAYER, 'Play')
    assert get(client, 'Metadata')['mpris:trackid'] == track
    c.audio.event('eos')
    assert get(client, 'Metadata')['mpris:trackid'] != track
    call(client, PLAYER, 'OpenUri', GLib.Variant('(s)', (wav_file.as_uri(),)))
    assert c.current.id is None and c.audio.path == wav_file
    assert get(client, 'Metadata')['xesam:title'] == wav_file.stem
    assert c.history.active is None
    c.activate([])
    assert get(client, 'Metadata') == {}


def test_properties_notifications_no_position_updates_and_ui_seek_signal(service, indexed_songs):
    s, c, clock, client, *_ = service
    signals = []
    subscription = client.signal_subscribe(BUS_NAME, None, None, OBJECT_PATH, None,
                                            Gio.DBusSignalFlags.NONE,
                                            lambda *args: signals.append((args[4], args[5])))
    try:
        c.activate(indexed_songs)
        wait_for(lambda: any(name == 'PropertiesChanged' for name, _ in signals))
        c.pause()
        set_property(client, 'Volume', GLib.Variant('d', 0.25))
        c.seek(2.5)  # The same controller entry point used by the progress bar.
        wait_for(lambda: any(name == 'Seeked' for name, _ in signals))
        assert [value.unpack() for name, value in signals if name == 'Seeked'] == [(2_500_000,)]
        changed = [value.unpack()[1] for name, value in signals if name == 'PropertiesChanged']
        assert any(values.get('PlaybackStatus') == 'Paused' for values in changed)
        assert any(values.get('Volume') == 0.25 for values in changed)
        assert any('Metadata' in values for values in changed)
        for _ in range(4):
            c.tick()
        assert all('Position' not in values and 'CanControl' not in values for values in changed)
    finally:
        client.signal_unsubscribe(subscription)


def test_buffering_loading_error_status_and_unavailable_capabilities(service, indexed_songs):
    s, c, clock, client, *_ = service
    c.activate(indexed_songs)
    for state in ('loading', 'buffering', 'error', 'stopped'):
        c.state = state
        c._notify('state')
        assert get(client, 'PlaybackStatus') == 'Stopped'
    c.state = 'paused'
    assert get(client, 'PlaybackStatus') == 'Paused'
    for song in indexed_songs[1:]:
        Path(song.path).unlink()
    assert not get(client, 'CanGoNext')
    call(client, PLAYER, 'Next')
    assert c.current == indexed_songs[0]
    c.audio.can_seek = False
    assert not get(client, 'CanSeek')
    before = list(c.audio.seeks)
    call(client, PLAYER, 'Seek', GLib.Variant('(x)', (1_000_000,)))
    assert c.audio.seeks == before
    c.stop()
    Path(indexed_songs[0].path).unlink()
    assert not get(client, 'CanPlay') and not get(client, 'CanPause')


@pytest.mark.parametrize('uri', ['https://example.com/song.mp3', 'http://localhost/song.wav',
                               'file://remote/music.wav', 'ftp://example.com/song.mp3',
                               'file:///tmp/song.wav?download=1', 'file:///tmp/song.wav#fragment',
                               'file:relative.wav', 'file:///tmp/song.txt', 'file:///tmp/song%GG.wav'])
def test_remote_and_invalid_uris_rejected(service, uri):
    s, c, clock, client, *_ = service
    with pytest.raises(GLib.Error, match='InvalidArgs'):
        call(client, PLAYER, 'OpenUri', GLib.Variant('(s)', (uri,)))
    assert c.current is None


def test_root_raise_quit_and_fullscreen(service):
    s, c, clock, client, reports, raised, quits = service
    call(client, ROOT, 'Raise')
    assert raised == [True]
    with pytest.raises(GLib.Error, match='NotSupported'):
        set_property(client, 'Fullscreen', GLib.Variant('b', True), ROOT)
    call(client, ROOT, 'Quit')
    wait_for(lambda: quits)
    assert quits == [True]


def test_name_conflict_does_not_replace_or_queue_and_cleanup(service, indexed_songs):
    s, c, clock, client, *_ = service
    other = connection()
    reports = []
    duplicate = MprisService(c, lambda: None, lambda: None, reports.append)
    try:
        duplicate.start(other)
        wait_for(lambda: reports)
        assert not duplicate.ready and not duplicate._registrations
        assert s.ready and owns_name(client)
        c.activate(indexed_songs)
        assert c.state == 'playing'
        s.close()
        wait_for(lambda: not owns_name(client))
        assert not duplicate.ready
        assert not s._registrations and s._changed not in c._listeners
        s.close()
    finally:
        duplicate.close()
        other.close_sync(None)


def test_bus_disconnection_is_nonfatal(service, indexed_songs):
    s, c, clock, client, reports, *_ = service
    s.connection.close_sync(None)
    wait_for(lambda: reports)
    c.activate(indexed_songs)
    assert c.state == 'playing'
    assert not s.ready


def test_real_engine_window_controls_and_seek_signals(long_wav_file, user_directories):
    from test_library import scan
    from test_library_ui import open_window
    import shutil
    path = user_directories / 'Native integration example.wav'
    shutil.copyfile(long_wav_file, path)
    scan()
    with open_window() as window:
        wait_for(lambda: not window.scanner.running)
        server, client = connection(), connection()
        instance = MprisService(window.controller, window.present, window.close)
        signals = []
        subscription = client.signal_subscribe(BUS_NAME, PLAYER, 'Seeked', OBJECT_PATH, None,
                                                Gio.DBusSignalFlags.NONE, lambda *args: signals.append(args[5].unpack()[0]))
        try:
            instance.start(server)
            wait_for(lambda: instance.ready)
            call(client, PLAYER, 'OpenUri', GLib.Variant('(s)', (path.as_uri(),)))
            wait_for(lambda: get(client, 'PlaybackStatus') == 'Playing')
            assert window.bar.title.get_text() == path.stem
            call(client, PLAYER, 'Pause')
            wait_for(lambda: get(client, 'PlaybackStatus') == 'Paused')
            assert window.bar.play.get_tooltip_text().startswith('Play')
            call(client, PLAYER, 'SetPosition', GLib.Variant('(ox)', (instance.track_id, 2_125_000)))
            wait_for(lambda: signals)
            assert signals[-1] == pytest.approx(2_125_000, abs=10000)
            assert window.controller.position == pytest.approx(2.125, abs=0.01)
            window.controller.seek(4.25)
            wait_for(lambda: len(signals) == 2)
            assert signals[-1] == pytest.approx(4_250_000, abs=10000)
            set_property(client, 'Volume', GLib.Variant('d', 0.42))
            assert window.bar.volume.get_value() == pytest.approx(42)
            set_property(client, 'Shuffle', GLib.Variant('b', True))
            assert window.bar.shuffle.get_active()
            set_property(client, 'LoopStatus', GLib.Variant('s', 'Track'))
            assert window.bar.repeat.get_active()
            call(client, PLAYER, 'Play')
            wait_for(lambda: window.controller.state == 'playing')
            call(client, PLAYER, 'Stop')
            assert window.controller.state == 'stopped'
        finally:
            instance.close()
            client.signal_unsubscribe(subscription)
            client.close_sync(None)
            server.close_sync(None)


def test_launcher_cwd_reactivation_independent_gdbus_and_quit(long_wav_file):
    root = Path(__file__).resolve().parents[1]
    client = connection()
    launch = [sys.executable, str(root / 'desktop/launch.py')]
    process = subprocess.Popen(launch, cwd='/tmp', stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        wait_for(lambda: owns_name(client), timeout=10)
        owner = call(client, 'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (BUS_NAME,)),
                     destination='org.freedesktop.DBus', path='/org/freedesktop/DBus').unpack()[0]
        second = subprocess.run(launch, cwd='/', capture_output=True, text=True, timeout=10)
        assert second.returncode == 0, second.stderr
        assert process.poll() is None
        assert call(client, 'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (BUS_NAME,)),
                    destination='org.freedesktop.DBus', path='/org/freedesktop/DBus').unpack()[0] == owner
        response = subprocess.run(['gdbus', 'call', '--session', '--dest', BUS_NAME, '--object-path', OBJECT_PATH,
                                   '--method', PROPERTIES + '.Get', ROOT, 'Identity'], capture_output=True, text=True, timeout=5)
        assert response.returncode == 0 and 'MusicPlayer' in response.stdout
        call(client, PLAYER, 'OpenUri', GLib.Variant('(s)', (long_wav_file.as_uri(),)))
        wait_for(lambda: get(client, 'PlaybackStatus') == 'Playing')
        call(client, ROOT, 'Raise')
        call(client, ROOT, 'Quit')
        assert process.wait(timeout=10) == 0
        wait_for(lambda: not owns_name(client))
        assert not call(client, 'org.freedesktop.DBus', 'NameHasOwner', GLib.Variant('(s)', (DESKTOP_ENTRY,)),
                        destination='org.freedesktop.DBus', path='/org/freedesktop/DBus').unpack()[0]
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        process.stderr.close()
        client.close_sync(None)


def test_application_without_session_bus_still_plays_and_closes(long_wav_file):
    root = Path(__file__).resolve().parents[1]
    program = '''import sys
from gi.repository import GLib
from music_player.app import MusicPlayerApplication
from music_player.audio import Gst
app=MusicPlayerApplication()
failures=[]
def check():
 try:
  assert app.get_windows(), 'No application window'
  window=app.get_windows()[0]
  sink=Gst.ElementFactory.make('fakesink');sink.set_property('sync',True)
  window.player.pipeline.set_property('audio-sink',sink)
  window.controller.open_file(sys.argv[1])
  attempts=[0]
  def finish():
   attempts[0]+=1
   if window.controller.state=='loading' and attempts[0]<30:
    return True
   try:
    assert window.controller.state=='playing', (window.controller.state, window.controller.message, str(window.player.pipeline.get_state(0)))
    window.close()
    assert window.player.pipeline.get_state(0).state==Gst.State.NULL
   except BaseException as exc:
    failures.append(str(exc));app.quit()
   return False
  GLib.timeout_add(100,finish)
 except BaseException as exc:
  failures.append(str(exc));app.quit()
 return False
GLib.timeout_add(500,check)
code=app.run(['musicplayer-offline-test'])
assert code==0 and not failures,failures
print('offline playback and cleanup passed')
'''
    response = subprocess.run([sys.executable, '-c', program, str(long_wav_file)], cwd=root,
                              env={**os.environ, 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/nonexistent/musicplayer-test-bus'},
                              capture_output=True, text=True, timeout=10)
    assert response.returncode == 0, response.stdout + response.stderr
    assert 'offline playback and cleanup passed' in response.stdout
    assert 'Desktop media controls unavailable' in response.stderr


def test_losing_application_session_bus_keeps_audio_alive(long_wav_file):
    root = Path(__file__).resolve().parents[1]
    program = '''import os, signal, sys
from gi.repository import Gio, GLib
from music_player.app import MusicPlayerApplication
from music_player.audio import Gst
app=MusicPlayerApplication()
failures=[]
def prepare():
 try:
  window=app.get_windows()[0]
  sink=Gst.ElementFactory.make('fakesink');sink.set_property('sync',True)
  window.player.pipeline.set_property('audio-sink',sink)
  window.controller.open_file(sys.argv[1])
  attempts=[0]
  def disconnect():
   attempts[0]+=1
   if window.controller.state!='playing' and attempts[0]<30: return True
   try:
    assert window.controller.state=='playing'
    connection=app.get_dbus_connection()
    reply=connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus',
      'org.freedesktop.DBus','GetConnectionUnixProcessID',GLib.Variant('(s)',('org.freedesktop.DBus',)),
      None,Gio.DBusCallFlags.NONE,2000,None)
    os.kill(reply.unpack()[0],signal.SIGTERM)
    def finish():
     try:
      assert window.controller.state=='playing'
      assert app.mpris._closed
      window.close()
      assert window.player.pipeline.get_state(0).state==Gst.State.NULL
     except BaseException as exc:
      failures.append(repr(exc));app.quit()
     return False
    GLib.timeout_add(200,finish)
   except BaseException as exc:
    failures.append(repr(exc));app.quit()
   return False
  GLib.timeout_add(100,disconnect)
 except BaseException as exc:
  failures.append(repr(exc));app.quit()
 return False
GLib.timeout_add(500,prepare)
code=app.run(['musicplayer-disconnection-test'])
assert code==0 and not failures,failures
print('bus loss preserved playback and cleanup')
'''
    result = subprocess.run(['dbus-run-session', '--', sys.executable, '-c', program, str(long_wav_file)],
                            cwd=root, capture_output=True, text=True, timeout=12)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'bus loss preserved playback and cleanup' in result.stdout


def test_failed_seek_does_not_emit_seeked(service, indexed_songs, monkeypatch):
    s, c, clock, client, *_ = service
    c.activate(indexed_songs)
    signals = []
    subscription = client.signal_subscribe(BUS_NAME, PLAYER, 'Seeked', OBJECT_PATH, None,
                                            Gio.DBusSignalFlags.NONE, lambda *args: signals.append(args[5]))
    def fail(position):
        c._audio_event('seeked', False)
        raise ValueError('GStreamer rejected the seek')
    monkeypatch.setattr(c.audio, 'seek', fail)
    try:
        with pytest.raises(GLib.Error, match='InvalidArgs'):
            call(client, PLAYER, 'Seek', GLib.Variant('(x)', (1_000_000,)))
        assert get(client, 'PlaybackStatus') == 'Playing'
        assert signals == []
    finally:
        client.signal_unsubscribe(subscription)
