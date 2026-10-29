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

A successful scan hides deleted files from All Songs using an availability flag.
Song records and IDs are retained, including their listening history. Returning
files at the same path regain the same ID without reparsing unchanged metadata.
Phase 2 databases migrate automatically without losing songs or metadata. If a folder
is unavailable or traversal fails, its existing records are retained. Playback
of a missing file reports an error. Transient metadata failures retain existing
records and are retried on the next scan.

The All Songs sidebar opens a native `Gtk.ColumnView`. Search matches words
across title, artist, and album without regard to case. Click the Title, Artist,
Album, or Duration headers to sort. Duration sorts numerically. Double-click a
song (or press Enter on a focused row) to load and play it. Selection uses the
system highlight, and a play icon identifies the playing row.

## Playback

Double-clicking a song creates a queue snapshot from the current filtered,
sorted view and starts that song. Later searches, sorts, and library refreshes
do not change the active queue. Starting another song from the table creates a
new snapshot. Navigation skips missing or unreadable files.

The permanent bottom bar displays the current cover, title, artist/album,
Shuffle, Previous, Play/Pause, Next, Repeat, elapsed/total time, a seek slider,
and volume/mute. Previous restarts the track after more than three seconds;
otherwise it follows actual playback history, or the preceding sequential song.
Previous and Next preserve paused playback. The play icon follows engine state.

Shuffle plays the remaining unheard tracks in random order without changing the
current track. Turning it off restores the original queue order. Previous follows
actual playback history. Repeat cycles Off → All → One → Off. Off stops at the
end; All starts a new cycle (a new shuffle avoids the last track at the boundary
when possible); One replays the current song at EOS. Manual Next advances even
under Repeat One. There is no separate queue editor in this phase.

Click or drag the progress slider to seek. Dragging commits one seek on release;
keyboard/wheel slider changes are debounced. Unknown duration displays `-:--`
and disables seeking. Volume is 0–100%, remembered across normal app launches.
Mute preserves that volume; unmuting restores it. Mute itself is not persisted.

**Open audio file** uses the native local file chooser. Already indexed paths
use their stable song ID; files outside the index are playable with metadata
and cover art but are excluded from library statistics and are not imported.
Closing the window cancels scanning, checkpoints history, stops audio, and exits.
Styling follows the GTK light/dark theme. The player bar fits the 720-pixel
minimum width; the library table can scroll horizontally in narrow windows.

| Shortcut | Action |
| --- | --- |
| Space | Play/Pause, except while typing in an editable control |
| Ctrl+Right | Next |
| Ctrl+Left | Previous |
| Ctrl+M | Mute/Unmute |
| Ctrl+O | Open local audio file |
| Ctrl+F | Focus search |

These are in-app GTK actions. No global media keys or desktop keyboard hooks
are implemented; MPRIS is reserved for a later phase.

## Listening history

Listening time and play counts are separate. One session qualifies for one play
after **30 seconds of actual playback or 50% of duration, whichever comes first**.
Unknown duration uses 30 seconds. A skipped track still retains its actual
listening seconds even if it never qualifies. Replaying after EOS starts a new
session; pause/resume and seeking remain within the same session. A manual stop
or decoder error is recorded with its own end reason, never as EOS completion.

Tracking samples GStreamer state and position against a monotonic clock at
250 ms intervals. Pauses, buffering, seeks, and stalled position do not count.
Unannounced position jumps and scheduler/suspend gaps over two seconds are
excluded conservatively, so uncertain gaps can undercount rather than invent
listening time. This measures progressing playback, not audible output quality.

SQLite checkpoints run about every five seconds and on pause, stop, seek, track
switch, EOS, and normal exit. Each session has a unique ID and each interval a
unique sequence; atomic upserts and unique qualified-play records make retries
idempotent. A restart marks an unclosed session interrupted and never resumes
its old counter. A sudden termination loses at most the latest unflushed period
under normal storage operation. Storage errors appear in the player status and
terminal; pending data is retained for retry while the application is running.

Session records snapshot the song ID, title, artist, album, duration, and UTC
start/end timestamps. Listening intervals also retain their local calendar date
and system timezone, split at local midnight so daily/monthly totals remain
possible. The Statistics interface is not implemented yet.

## Stored data and architecture

- Database: `$XDG_DATA_HOME/musicplayer/library.sqlite3`, defaulting to
  `~/.local/share/musicplayer/library.sqlite3`.
- Covers: `$XDG_CACHE_HOME/musicplayer/covers/`, defaulting to
  `~/.cache/musicplayer/covers/`. Artwork is cached as content-addressed PNGs
  with a maximum dimension of 96 pixels, outside SQLite. Missing thumbnails are
  regenerated on refresh; invalid artwork uses a neutral placeholder. Decoding
  of embedded artwork runs on the scan worker; the UI reuses a bounded cache of
  small thumbnail textures.
- `audio.py` extends the existing widget-independent GStreamer engine with
  position, duration, seek, volume, mute, and structured playback events.
  `metadata.py` reads tags without GTK. `library.py` owns SQLite access.
  `scanner.py` uses one cancellable worker and its own database connection.
  `window.py` applies worker results on GTK's main thread; `song_model.py`
  supplies the song objects, search matching, comparison, and thumbnail reuse.
- `controller.py` owns queue snapshots, playback state, navigation, modes, and
  transitions. `player_bar.py` renders controls; `shortcuts.py` centralizes GTK
  actions. `history.py` owns listening accounting and atomic persistence. GTK
  widgets do not issue GStreamer operations directly.

The database stores folders, songs with availability, playback sessions,
listening intervals, qualified plays, and a single volume preference. History
foreign keys do not cascade-delete song identities. Scans release SQLite write
locks before parsing the next song so history checkpoints can proceed.
No playlists, Statistics UI, or MPRIS implementation is included. No software license has been
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
selected-song playback, and Phase 1 playback/lifecycle regressions. Phase 3
adds deterministic queue/shuffle/repeat/navigation tests, audio seeking and
buffering checks, player controls/actions/seek debounce tests, and simulated
clock tests for thresholds, skips, seeks, stalls, midnight/month boundaries,
metadata snapshots, migration, checkpoint retries, and crash recovery. No test
waits 30 real seconds to verify a play-count threshold.

## Later phases

Playlists, a queue editor, hardware media keys, MPRIS, and the Statistics page
remain future work. Phase 3 stores the underlying listening history now, without
adding those interfaces or integrations.
