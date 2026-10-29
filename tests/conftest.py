import math
import struct
import wave

import pytest

from music_player.audio import AudioPlayer, Gst
from music_player.library import LibraryDatabase
from music_player.paths import database_path
from music_player.artwork import GdkPixbuf


@pytest.fixture(autouse=True)
def user_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    music = tmp_path / "Music"
    music.mkdir()
    with LibraryDatabase(database_path()) as db:
        db.configure_default_folder(music)
    return music


@pytest.fixture
def artwork():
    image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 400, 200)
    image.fill(0x356A91FF)
    success, data = image.save_to_bufferv("png", [], [])
    assert success
    return bytes(data)


@pytest.fixture
def wav_file(tmp_path):
    path = tmp_path / "test tone # café.wav"
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(
            struct.pack("<h", int(1000 * math.sin(2 * math.pi * 440 * i / 8000)))
            for i in range(8000)
        ))
    return path


@pytest.fixture
def player():
    statuses = []
    instance = AudioPlayer(statuses.append)
    sink = Gst.ElementFactory.make("fakesink")
    sink.set_property("sync", True)
    instance.pipeline.set_property("audio-sink", sink)
    yield instance, statuses
    instance.close()
