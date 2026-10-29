import hashlib
import time

import pytest
from gi.repository import GLib

from music_player.audio import Gst


def wait_for(predicate, timeout=5):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for GStreamer/GTK")


def test_play_pause_resume_stop_and_preserve_file(player, wav_file):
    engine, statuses = player
    original = hashlib.sha256(wav_file.read_bytes()).digest()
    engine.load(wav_file)
    assert engine.pipeline.get_property("uri") == wav_file.as_uri()
    assert "%23" in wav_file.as_uri()
    engine.play()
    wait_for(lambda: statuses[-1] == "Playing")
    engine.pause()
    wait_for(lambda: statuses[-1] == "Paused")
    engine.play()
    wait_for(lambda: statuses[-1] == "Playing")
    engine.stop()
    assert statuses[-1] == "Stopped"
    assert engine.pipeline.get_state(0).state == Gst.State.NULL
    assert hashlib.sha256(wav_file.read_bytes()).digest() == original


def test_end_of_stream_and_replay(player, wav_file):
    engine, statuses = player
    engine.load(wav_file)
    engine.play()
    wait_for(lambda: statuses[-1] == "Finished")
    assert engine.pipeline.get_state(0).state == Gst.State.NULL
    engine.play()
    wait_for(lambda: statuses[-1] == "Playing")


def test_switch_files_reuses_pipeline(player, wav_file, tmp_path):
    engine, statuses = player
    second = tmp_path / "second.wav"
    second.write_bytes(wav_file.read_bytes())
    pipeline = engine.pipeline
    engine.load(wav_file)
    engine.play()
    wait_for(lambda: statuses[-1] == "Playing")
    engine.load(second)
    assert engine.pipeline is pipeline
    assert pipeline.get_state(0).state == Gst.State.NULL
    assert pipeline.get_property("uri") == second.as_uri()
    engine.play()
    wait_for(lambda: statuses[-1] == "Playing")


def test_invalid_missing_directory_and_remote_paths(player, tmp_path):
    engine, statuses = player
    with pytest.raises(ValueError, match="Select"):
        engine.play()
    with pytest.raises(FileNotFoundError):
        engine.load(tmp_path / "missing.wav")
    with pytest.raises(ValueError, match="regular"):
        engine.load(tmp_path)
    with pytest.raises(FileNotFoundError):
        engine.load("https://example.com/song.mp3")
    invalid = tmp_path / "invalid.wav"
    invalid.write_text("This is not audio.")
    engine.load(invalid)
    engine.play()
    wait_for(lambda: statuses[-1].startswith("Error:"))
    assert engine.pipeline.get_state(0).state == Gst.State.NULL


def test_missing_file_after_selection(player, wav_file):
    engine, statuses = player
    engine.load(wav_file)
    wav_file.unlink()
    engine.play()
    wait_for(lambda: statuses[-1].startswith("Error:"))


def test_close_during_playback_is_idempotent(player, wav_file):
    engine, statuses = player
    engine.load(wav_file)
    engine.play()
    wait_for(lambda: statuses[-1] == "Playing")
    engine.close()
    engine.close()
    assert engine.pipeline.get_state(0).state == Gst.State.NULL
    assert not engine._bus.handler_is_connected(engine._handler)
    with pytest.raises(RuntimeError, match="closed"):
        engine.play()


def test_position_duration_seek_volume_and_mute(player, wav_file):
    engine, statuses = player
    engine.load(wav_file)
    engine.play()
    wait_for(lambda: statuses[-1] == 'Playing')
    assert engine.duration == pytest.approx(1)
    assert 0 <= engine.position < 1
    pipeline = engine.pipeline
    engine.seek(0.5)
    wait_for(lambda: not engine.seeking)
    assert engine.position == pytest.approx(0.5, abs=0.2)
    engine.pause()
    wait_for(lambda: statuses[-1] == 'Paused')
    engine.seek(0.2)
    wait_for(lambda: not engine.seeking)
    assert engine.position == pytest.approx(0.2, abs=0.02)
    for value in (-1, float('nan'), float('inf'), 2):
        with pytest.raises(ValueError):
            engine.seek(value)
    engine.volume = 0.35
    engine.muted = True
    assert engine.volume == pytest.approx(0.35) and engine.muted
    engine.muted = False
    assert engine.volume == pytest.approx(0.35) and not engine.muted
    with pytest.raises(ValueError):
        engine.volume = 1.1
    assert engine.pipeline is pipeline


def test_buffering_and_structured_errors(player, wav_file, tmp_path):
    engine, statuses = player
    events = []
    engine._on_event = lambda event, detail=None: events.append((event, detail))
    engine.load(wav_file)
    engine.play()
    wait_for(lambda: statuses[-1] == 'Playing')
    engine._on_message(engine._bus, Gst.Message.new_buffering(engine.pipeline, 20))
    assert events[-1][0] == 'buffering'
    engine._on_message(engine._bus, Gst.Message.new_buffering(engine.pipeline, 100))
    wait_for(lambda: events[-1][0] == 'playing')
    invalid = tmp_path / 'bad.wav'
    invalid.write_bytes(b'not audio')
    engine.load(invalid)
    engine.play()
    wait_for(lambda: events[-1][0] == 'error')
    assert events[-1][1]
    assert engine.state == 'error'
    assert engine.pipeline.get_state(0).state == Gst.State.NULL


def test_requested_levels_survive_sound_server_stream_restore(player, wav_file):
    engine, statuses = player
    engine.load(wav_file)
    engine.volume = 0.37
    engine.muted = False
    engine.play()
    wait_for(lambda: statuses[-1] == 'Playing')
    engine.pipeline.set_property('volume', 0.9)
    engine.pipeline.set_property('mute', True)
    engine._on_message(engine._bus, Gst.Message.new_state_changed(
        engine.pipeline, Gst.State.PAUSED, Gst.State.PLAYING, Gst.State.VOID_PENDING))
    assert engine.volume == 0.37 and not engine.muted
    assert engine.pipeline.get_property('volume') == pytest.approx(0.37)
    assert not engine.pipeline.get_property('mute')


def test_pause_while_buffering_then_resume(player, long_wav_file):
    engine, statuses = player
    engine.load(long_wav_file)
    engine.play()
    wait_for(lambda: engine.state == 'playing')
    engine._on_message(engine._bus, Gst.Message.new_buffering(engine.pipeline, 20))
    wait_for(lambda: engine.pipeline.get_state(0).state == Gst.State.PAUSED)
    assert engine.state == 'buffering'
    engine.pause()
    wait_for(lambda: engine.state == 'paused', timeout=1)
    # A late buffering completion must not override the explicit pause.
    engine._on_message(engine._bus, Gst.Message.new_buffering(engine.pipeline, 100))
    assert engine.pipeline.get_state(0).state == Gst.State.PAUSED
    engine.play()
    wait_for(lambda: engine.state == 'playing')


def test_rapid_switches_discard_old_bus_events_and_keep_one_pipeline(player, long_wav_file, tmp_path):
    engine, statuses = player
    second = tmp_path / 'another.wav'
    second.write_bytes(long_wav_file.read_bytes())
    pipeline = engine.pipeline
    for index in range(40):
        engine.load(long_wav_file if index % 2 else second)
        engine.play()
    engine.pause()
    wait_for(lambda: engine.state == 'paused')
    assert engine.path == long_wav_file
    assert engine.pipeline is pipeline
    assert engine.pipeline.get_state(0).state == Gst.State.PAUSED
    assert not any(status.startswith('Error:') for status in statuses)
    engine.stop()
    assert engine.pipeline.get_state(0).state == Gst.State.NULL
