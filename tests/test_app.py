import pytest
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from music_player.app import MusicPlayerApplication
from music_player.audio import Gst
from test_audio import wait_for


@pytest.mark.skipif(not Gtk.init_check(), reason="No graphical display available")
def test_window_startup_reactivation_and_close(long_wav_file, monkeypatch):
    app = MusicPlayerApplication()
    app.set_application_id("io.github.OscarGR5.MusicPlayer.Tests")
    failures = []
    windows = []

    def exercise():
        try:
            window = app.get_active_window()
            assert window is not None
            windows.append(window)
            assert window.get_title() == "MusicPlayer"
            assert window.player.path is None
            # Reactivation must reuse an unfocused/not-yet-focused window too.
            monkeypatch.setattr(app, 'get_active_window', lambda: None)
            app.activate()
            assert len(app.get_windows()) == 1
            assert Gtk.Window.get_default_icon_name() == 'io.github.OscarGR5.MusicPlayer'
            from gi.repository import Gdk
            assert Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).has_icon('io.github.OscarGR5.MusicPlayer')
            sink = Gst.ElementFactory.make('fakesink')
            sink.set_property('sync', True)
            window.player.pipeline.set_property('audio-sink', sink)
            window.player.load(long_wav_file)
            window.player.play()
            wait_for(lambda: window._status.get_text() == "Playing")
            window.close()
            assert window.player.pipeline.get_state(0).state == Gst.State.NULL
        except BaseException as exc:
            failures.append(exc)
            app.quit()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(200, exercise)
    assert app.run(["music-player-tests"]) == 0
    assert windows
    assert not failures, str(failures)
    assert not app.get_windows()


@pytest.mark.skipif(not Gtk.init_check(), reason="No graphical display available")
def test_startup_failure_is_reported(monkeypatch, capsys):
    def fail(application):
        raise RuntimeError("GStreamer playbin is unavailable.")

    monkeypatch.setattr("music_player.app.MusicPlayerWindow", fail)
    app = MusicPlayerApplication()
    app.set_application_id("io.github.OscarGR5.MusicPlayer.FailureTest")
    app.run(["music-player-tests"])
    assert app.startup_failed
    assert "playbin is unavailable" in capsys.readouterr().err
