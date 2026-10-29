# MusicPlayer

A lightweight native GTK4 desktop music player for Fedora Linux. Phase 1
provides a temporary interface for local audio playback through GStreamer.
There are no streaming services, accounts, web servers, or browser processes.

## Fedora setup

Required system runtime packages:

```sh
sudo dnf install python3 python3-gobject gtk4 gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good
```

These provide PyGObject and GStreamer introspection bindings. Do not compile
PyGObject through pip. Format support depends on the installed GStreamer
decoders; the base/good plugins support WAV, FLAC, and MP3 on Fedora.
The desktop also needs a working audio output device.

Clone and launch from the repository root:

```sh
git clone https://github.com/OscarGR5/MusicPlayer.git
cd MusicPlayer
python3 -m music_player
```

No Python package installation is needed to run from the checkout.
Select **Open audio file**, then **Play / Resume**, **Pause**, or **Stop**.
Opening a new file stops the previous one. Closing the window releases the
pipeline and exits. Network files are rejected; a file must have a local path.
Errors and end-of-stream status appear in the window.

## Development and tests

pytest is a development dependency, separate from the runtime packages:

```sh
sudo dnf install python3-pytest
python3 -m pytest -q
```

Alternatively use a virtual environment that can access Fedora's GI bindings:

```sh
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install pytest
python -m pytest -q
```

Tests generate temporary WAV tones. Engine tests use GStreamer's synchronized
fake audio sink to verify state transitions, EOS, errors, URI escaping, file
switching, cleanup, and unchanged input bytes. The GTK lifecycle test uses the
desktop audio output and is skipped without a display. It opens and closes a
test window and plays a short quiet tone. Audible quality and native chooser
interaction require a manual desktop check.

## Scope

Phase 1 includes one native window, a native file chooser, one reusable playbin,
play/pause/resume/stop controls, and asynchronous playback status/error handling.
The audio engine is separate from GTK widgets.

Later phases will add a scanned library, Mutagen metadata and covers, SQLite
storage, playlists, search/sorting, playback progress and volume, shuffle/repeat,
shortcuts and media keys, MPRIS, and listening statistics. None is implemented.
Future play counts will require 30 seconds of actual playback or 50% of the
track duration, whichever comes first; pauses and seeks must not inflate time.

No software license has been selected. Test media, environments, build output,
and local secrets are excluded from Git.

## Phase 1 verification

Verified on Fedora 44 with Python 3.14.8, GTK 4.22.5, and GStreamer 1.28.8:
eight pytest tests passed, with upstream PyGObject deprecation warnings.
The module entry point started without a file and exited when its window closed.
Separate generated WAV, MP3, and FLAC tones passed play, pause, resume, EOS,
replay, and stop checks using the default audio output. Input hashes did not
change. No media is included in this repository.

Audible quality and native chooser interaction have not been manually verified.
All runtime dependencies and pytest were already available; no packages were
installed during implementation.
