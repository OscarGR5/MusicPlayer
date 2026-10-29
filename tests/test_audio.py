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
