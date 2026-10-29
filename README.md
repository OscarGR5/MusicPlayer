# MusicPlayer

A lightweight native GTK4 music library and player for Fedora Linux. It plays
local files through GStreamer, without accounts, streaming, a web server, or a
browser process.

## Fedora setup

Required system runtime packages:

```sh
sudo dnf install python3 python3-gobject gtk4 gdk-pixbuf2 gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good python3-mutagen
```

PyGObject, GTK4, GStreamer, and GdkPixbuf use Fedora's system packages. Do not
compile PyGObject through pip. SQLite is included with Python. Mutagen is the
only additional Python runtime dependency. Playback format support depends on
your installed GStreamer decoders; unavailable decoders produce playback errors.
A graphical desktop and working audio output are required.

Clone and launch from the repository root:

```sh
git clone https://github.com/OscarGR5/MusicPlayer.git
cd MusicPlayer
python3 -m music_player
```

Alternatively, keep Python dependencies in a virtual environment while using
Fedora's existing GI bindings:

```sh
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install 'mutagen>=1.46' pytest
python -m music_player
```

No pip installation of the project itself is needed to run from the checkout.

## Music library

The first launch automatically scans `~/Music` (the initial folder is
`/home/admin/Music` on the project owner's machine), including subdirectories.
Supported extensions are MP3, FLAC, M4A, Ogg/OGA, Opus, and WAV. Mutagen reads
title, artist, album, duration, and embedded artwork. Untagged tracks use their
filename as the title and show unknown artist/album values. Malformed or
unreadable files are skipped, with a count in the scan status and details in
the terminal. Source music files are never written, renamed, moved, or deleted.
Directory symlinks are not traversed; file symlinks resolve to one canonical path.

Existing songs appear on later launches before a background refresh begins.
Refresh compares nanosecond modification times and file sizes, reuses unchanged
metadata, preserves IDs for unchanged paths, and adds or updates songs in
batches. Choose **Rescan Library** to refresh manually. Only one scan runs at a
time. There is no continuous filesystem watcher.

A successful scan removes records for deleted files from the index. If a folder
is unavailable or traversal fails, its existing records are retained. Playback
of a missing file reports an error. Transient metadata failures retain existing
records and are retried on the next scan.

The All Songs sidebar opens a native `Gtk.ColumnView`. Search matches words
across title, artist, and album without regard to case. Click the Title, Artist,
Album, or Duration headers to sort. Duration sorts numerically. Double-click a
song (or press Enter on a focused row) to load and play it. Selection uses the
system highlight, and a play icon identifies the playing row.

**Play / Resume** plays a newly selected track or resumes the loaded track;
**Pause** and **Stop** control playback. **Open audio file** retains Phase 1's
native local file chooser for playing an individual file without indexing it.
Closing the window cancels scanning, stops audio, and exits. Styling follows
the native GTK light/dark theme. Narrow windows can scroll the table horizontally.

## Stored data and architecture

- Database: `$XDG_DATA_HOME/musicplayer/library.sqlite3`, defaulting to
  `~/.local/share/musicplayer/library.sqlite3`.
- Covers: `$XDG_CACHE_HOME/musicplayer/covers/`, defaulting to
  `~/.cache/musicplayer/covers/`. Artwork is cached as content-addressed PNGs
  with a maximum dimension of 96 pixels, outside SQLite. Missing thumbnails are
  regenerated on refresh; invalid artwork uses a neutral placeholder. Decoding
  of embedded artwork runs on the scan worker; the UI reuses a bounded cache of
  small thumbnail textures.
- `audio.py` remains the Phase 1 widget-independent GStreamer engine.
  `metadata.py` reads tags without GTK. `library.py` owns SQLite access.
  `scanner.py` uses one cancellable worker and its own database connection.
  `window.py` applies worker results on GTK's main thread; `song_model.py`
  supplies the song objects, search matching, comparison, and thumbnail reuse.

Only folder configuration and song records are stored. No playlist, history,
statistics, or MPRIS implementation is included. No software license has been
selected. Environments, databases, secrets, caches, and test media are not
committed.

## Tests

pytest is a development dependency, separate from the runtime packages:

```sh
sudo dnf install python3-pytest
python3 -m pytest -q
```

Or run `.venv/bin/python -m pytest -q` after the virtual environment setup above.
Tests use temporary databases, XDG directories, and generated audio. Optional
MP3, FLAC, M4A, Ogg, and Opus fixture generation uses `ffmpeg` if available;
otherwise those cases are explicitly skipped. No personal music is used by the
automated tests. GTK tests are skipped without a graphical display. The Phase 1
lifecycle regression plays a short quiet tone through the desktop output;
other playback tests use synchronized fake sinks.

Coverage includes recursive discovery, tags and artwork for six formats,
fallbacks, corruption, duplicate prevention, stable IDs, changed/deleted files,
offline folders, metadata reuse, thumbnail regeneration, search, all sortable
columns, main-thread UI updates, responsiveness during scans, cancellation,
selected-song playback, and Phase 1 playback/lifecycle regressions.

## Later phases

Playlists, queues, progress and volume controls, shuffle/repeat, shortcuts,
hardware media keys, MPRIS, and listening statistics remain future work.
Future play counts require 30 seconds of actual playback or 50% of a track's
duration, whichever comes first. Pauses and seeks must not inflate listening
time. These features are not implemented in Phase 2.
