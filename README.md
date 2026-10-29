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

## Playlists

Use **+** beside **PLAYLISTS** in the sidebar to create a playlist. Names are
trimmed, must contain 1–80 characters, and must be unique without regard to case
(including Unicode case folding). Validation errors stay in the name dialog.
Right-click a playlist to rename or delete it. Deletion requires confirmation
and removes only that playlist and its memberships, keeping music and history.

In All Songs, use Ctrl+Click or Shift+Click to select songs, then right-click
and choose **Add to Playlist**. Right-clicking a selected row uses the whole
selection; right-clicking an unselected row selects just that song. Songs are
appended in current visible order. Repeated additions quietly skip existing
members. If there are no playlists, the menu offers **Create Playlist** and
adds the selected songs as part of the same transaction.

Choose a playlist in the sidebar to browse its saved order. Its heading shows
the name, total membership count, and a **Play** button. Search applies only to
that playlist. The playlist Play button starts the first available track in
saved order, independently of a search filter. Double-clicking a song instead
starts a queue from the currently visible, filtered playlist. Both reuse the
existing shuffle/repeat settings and permanent player bar.

Playlist columns do not apply temporary sorting. Select exactly one song and
use **Move Up** or **Move Down** from its context menu to change saved order;
the actions are disabled at the corresponding saved-order boundaries. With a
search active, moving still operates on the full saved order. Select one or
more songs and choose **Remove from Playlist** to remove memberships. No music
file or All Songs record is deleted. All Songs retains its column sorting.

Unavailable members remain in playlists with dimmed text and an Unavailable
label/tooltip. They cannot be activated directly and are skipped during
navigation. Rescanning after restoring files makes them available again with
their original song IDs. Use All Songs to access **Rescan Library**.

Switching views, searching, editing order or memberships, and deleting a
playlist do not change an active playback queue. A new playback activation
creates a fresh snapshot. Playlists and their saved order persist across
application restarts; the application starts in All Songs.

Use **Shift+F10** or the **Menu** key to open the song context menu for the
current selection, or the playlist menu when its sidebar row has focus. Space
is left to native menu handling while a popover has focus.

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
and system timezone, split at local midnight for daily/monthly totals.

## Statistics

Choose **Statistics** below All Songs. The page scrolls above the permanent
player bar. Switching among Statistics, All Songs, and playlists keeps the
playing song, queue, shuffle, and repeat. Opening Statistics and returning to the
same library or playlist view also preserves its search and selection.

**All Time** is the default. The period selector includes calendar months with
recorded listening or qualified plays, plus the current month, newest first.
Summary values and rankings cover the entire selected period:

- **Total Listening Time** sums persisted `listening_intervals.seconds`, including
  short partial listens and interrupted sessions. It never estimates from song
  duration, wall-clock time, session totals, or play counts. Durations display
  days/hours/minutes as needed, with seconds for listens under one minute; the
  summary tooltip and chart value list provide exact recorded seconds.
- **Total Plays** counts existing `qualified_plays` records. Qualification remains
  30 seconds of actual listening or half the song duration, whichever comes
  first. Statistics does not recalculate eligibility.
- **Unique Songs** counts distinct stable song IDs with positive listening time
  in that period. A qualified record alone does not add a unique listened song.

**Top Songs** shows up to 10 songs with qualified plays, ordered by play count,
then listening seconds, then stable song ID. Current available library metadata
is preferred; unavailable or deleted library records fall back to the latest
session's title/artist. Cached artwork is optional, with a native placeholder.
Identical titles never combine distinct song IDs.

**Top Artists** and **Top Albums** rank by actual listening time, including partial
listens. Artists use the exact historical session artist string, and albums use
historical artist plus album together. Tag edits do not rewrite these identities.
Ties sort by case-insensitive names followed by exact names (artist before album
for albums). Each row includes qualified plays and listening time. There is no
artist normalization, featured-artist splitting, or external metadata service.

Listening time uses each interval's saved `local_date`. Plays use the UTC
qualification instant converted through the recorded interval timezone at that
instant, falling back to the nearest preceding or following interval if needed.
This handles local midnight, month/year boundaries, and daylight-saving changes
without applying the computer's current timezone to old events. Invalid legacy
interval durations are ignored. Invalid dates contribute time only to All Time.
For an unknown timezone, a containing interval's local date is used when possible;
otherwise a valid qualification timestamp falls back to UTC. Undated qualified
records still count in All Time. Recovery limitations appear on the page.

The native bar chart shows **Last 12 Months** for All Time, including zero months;
the summary and rankings still include older history. A selected month shows
every calendar day, including zero days and February 29 in leap years. Expand
**Listening time values** for labeled durations and exact seconds outside the
chart. Empty periods show zero values and a short explanation.

Entering Statistics refreshes the data; visible pages refresh about every 10
seconds. The existing tracker checkpoints about every five seconds, so updates
can lag playback by a few seconds. SQLite reads run on one worker thread using a
read-only connection and a consistent transaction. Unchanged database versions
reuse the previous aggregate, and unchanged results preserve existing widgets.
The refresh timer stops while hidden. Temporary read errors appear on the page
and retry automatically, without touching playback.

Statistics makes no schema changes or migrations and writes no history,
preferences, playlists, or library rows. It adds no runtime dependencies.

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
- `statistics.py` returns ordinary immutable Python data from read-only history
  queries; `statistics_ui.py` handles the GTK page, worker, and native Cairo chart.

The database stores folders, songs with availability, playback sessions,
listening intervals, qualified plays, and a single volume preference. History
foreign keys do not cascade-delete song identities. Scans release SQLite write
locks before parsing the next song so history checkpoints can proceed.
`playlists.py` provides playlist persistence separately from GTK and playback.
Two additive tables store playlists and ordered memberships; a normalized name
key enforces Unicode case-insensitive uniqueness. Membership and position
constraints prevent duplicates, and all bulk edits use transactions. Deleting
a playlist cascades only to its membership rows. Phase 3 song and listening
history tables are not rewritten by this migration.

No MPRIS implementation is included. No software license has
been selected. Environments, databases, secrets, caches, and test media are not
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
waits 30 real seconds to verify a play-count threshold. Phase 4 adds playlist
validation, membership/order transactions, migration/history preservation,
native dialog/context-menu actions, multi-selection, missing-song restoration,
restart persistence, and queue snapshots during playlist edits and deletion.
Phase 5 adds controlled statistics databases for aggregation, stable identities,
historical tags, unavailable/deleted songs, deterministic top-10 ties, partial
listens, midnight/month/year boundaries, leap years, DST, historical timezones,
empty periods, read-only preservation, and refresh caching. GTK tests cover
navigation, period selection, summaries, rankings, chart values, refresh/retry,
worker responsiveness, continued playback, light/dark themes, and scrolling at
1040×640 and 720×480. Optional synthetic screenshots can be captured outside the
repository with `MUSICPLAYER_UI_CAPTURE_DIR=/tmp/musicplayer-ui` when running
`tests/test_statistics_ui.py`. Personal history and music are never test fixtures.

## Later phases

A queue editor, hardware media keys, MPRIS, and desktop installation remain
future work.
