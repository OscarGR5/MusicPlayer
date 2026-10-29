import math
import struct
import wave

import pytest

from music_player.audio import AudioPlayer, Gst


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
