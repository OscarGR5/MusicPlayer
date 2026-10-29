# MusicPlayer

A lightweight native GTK4 music library and player for local files, with a dark
interface, persistent playlists, and listening statistics. Primarily tested on
Fedora GNOME/Wayland. No accounts, streaming, web server, or browser process.

MusicPlayer v0.7.0 is the first release being prepared. See the
[changelog](CHANGELOG.md) for its contents.

## Fedora installation and launch

Install the system dependencies and Git:

```sh
sudo dnf install git python3 python3-gobject gtk4 gdk-pixbuf2 gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good python3-mutagen
```

Python 3.10 or newer and GTK4 4.12 or newer are required. PyGObject, GTK4,
GStreamer, GdkPixbuf, and Cairo integration come from Fedora packages. Do not
compile PyGObject through pip. SQLite is included with Python; Mutagen is the
only additional Python runtime dependency. A graphical desktop and working audio
output are required. The sorting scroll fix uses
[`Gtk.ColumnView.scroll_to`](https://docs.gtk.org/gtk4/method.ColumnView.scroll_to.html),
available since GTK 4.12.

Clone and launch from the repository root:

```sh
git clone https://github.com/OscarGR5/MusicPlayer.git
cd MusicPlayer
python3 -m music_player
```

For a virtual environment that uses Fedora's system GI bindings:

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install 'mutagen>=1.46'
.venv/bin/python -m music_player
```

No pip installation of the project itself is needed. There is currently no RPM,
Flatpak, standalone executable, or compiled application bundle.

### Update

Close MusicPlayer, then run these commands from the checkout on `main`:

```sh
git pull --ff-only origin main
.venv/bin/python -m pip install 'mutagen>=1.46'
.venv/bin/python -m music_player
```

For the system-Python setup, omit the pip command and use `python3 -m music_player`.
Updates run directly from the checkout; the launcher does not contain a separate
copy of the application. Database migrations run automatically and preserve
existing library, playlists, and history.

## Music library

The first launch scans `~/Music`, including subdirectories. Later launches show
saved songs before refreshing in the background. Choose **Rescan Library** in
All Songs for a manual refresh. There is no continuous filesystem watcher or
folder-management interface.

Supported extensions are **MP3, FLAC, M4A, Ogg/OGA, Opus, and WAV**. Playback
depends on the installed GStreamer decoders; an extension alone does not guarantee
that every codec in that container can play. Missing decoders produce playback
errors. Mutagen reads title, artist, album, duration, and embedded cover artwork.
Untagged tracks use their filename and unknown artist/album values. Unreadable or
malformed new files are skipped, with details in the terminal. Source music files
are never written, renamed, moved, or deleted.

Refresh reuses unchanged metadata and stable song IDs. Missing songs disappear
from All Songs but keep their records and history; restoring a file at the same
path makes it available again. An unavailable folder or failed traversal retains
its existing records. Transient metadata errors retain existing songs for retry.
Directory symlinks are not traversed; file symlinks resolve to one canonical path.

Search matches words across title, artist, and album without regard to case.
Click **Title**, **Artist**, **Album**, or **Duration** to sort; click again to
reverse direction. Duration sorts numerically. Sorting preserves selected song
IDs and returns the table to the top. Scrollbars allow vertical browsing and
horizontal scrolling in narrow windows, and the playlist sidebar scrolls
independently. Double-click a song, or press Enter on a focused row, to play it.
A play icon identifies the currently playing row.

## Playback and shortcuts

Starting a song from the table creates a queue snapshot of its current filtered,
sorted view. Later searches, sorts, playlist edits, and refreshes leave that queue
intact. Starting another song creates a new snapshot. Navigation skips missing or
unreadable files. There is no separate queue editor.

The bottom player bar includes cover artwork, track details, shuffle, previous,
play/pause, next, repeat, seeking, volume, and mute. Previous restarts the track
after more than three seconds; otherwise it follows playback history or the
preceding sequential song. Previous and Next preserve paused playback.

Shuffle plays unheard tracks in random order; turning it off restores queue
order. Repeat cycles **Off → All → One**. Manual Next advances even with Repeat
One enabled. Dragging the seek slider commits one seek on release. Unknown
duration displays `-:--` and disables seeking. Volume is remembered across normal
launches; mute preserves the volume but is not persisted.

**Open audio file** uses a tested, application-owned GTK file chooser. Indexed
files retain their song IDs. Files outside the library can play with metadata
and artwork, but are not imported or included in library statistics. Only local
files are accepted. Closing the window cancels scanning, checkpoints history,
stops audio, and exits.

| Shortcut | Action |
| --- | --- |
| Space | Play/Pause in the song view; focused controls and text fields keep their normal Space behavior |
| Ctrl+Right | Next |
| Ctrl+Left | Previous |
| Ctrl+M | Mute/Unmute |
| Ctrl+O | Open local audio file |
| Ctrl+F | Focus search |
| Shift+F10 or Menu | Context menu for selected songs or a focused playlist sidebar row |

These are in-app shortcuts. Hardware media keys use the MPRIS integration below.

## Playlists

Use **+** beside **PLAYLISTS** to create a playlist. Names must be unique without
regard to case and contain 1–80 characters after trimming. Right-click a playlist
to rename or delete it. Deletion requires confirmation and removes memberships
only, preserving songs, music files, and listening history.

In All Songs, Ctrl+Click or Shift+Click selects multiple songs. Right-click and
choose **Add to Playlist**. Songs append in visible order; duplicates are skipped.
If no playlist exists, **Create Playlist** creates one and adds the selection.

Opening a playlist shows its saved order. Search applies within that playlist.
Its **Play** button starts the first available song in the full saved order,
regardless of search or temporary sorting. Double-clicking a song instead creates
a queue from the visible filtered and sorted playlist. Both retain shuffle and
repeat settings.

Click a column header to sort a playlist temporarily. **Saved order** appears
while sorted and restores the saved order without clearing search. Temporary
sorting preserves selected song IDs and never writes order to the database.
Opening a playlist always returns to saved order; All Songs retains its sorting.

In saved order, select one song and use **Move Up** or **Move Down** from its
context menu to change the persisted order. These actions are disabled while
temporarily sorted and at the corresponding boundaries. With search active,
moving still acts on the full saved order. **Remove from Playlist** removes the
selected memberships only.

Unavailable members remain dimmed and labeled, cannot be activated directly, and
are skipped during navigation. Restoring and rescanning their files makes them
available again. Playlists and saved ordering persist across restarts; the app
starts in All Songs. View changes and playlist edits leave active queues intact.

## Listening history and statistics

One playback session qualifies for one play after **30 seconds of actual
playback or 50% of the duration, whichever comes first**. Unknown duration uses
30 seconds. Short listens retain their listening time even when they do not
qualify as plays. Pause/resume and seeking stay within one session; replay after
the end of a song starts a new session. Pauses, buffering, seeks, stalls, and
uncertain suspend gaps do not count toward listening time.

History is checkpointed about every five seconds and on pause, stop, seek, track
switch, and normal exit. Sudden termination can lose the latest unflushed period.
Interrupted sessions retain recorded time and are not resumed. Storage errors
appear in the player status and terminal, with pending data retried while running.

Choose **Statistics** for **All Time** or a calendar month. It shows total
listening time, qualified plays, unique listened songs, top songs by play count,
and top artists/albums by listening time. The chart shows the last 12 months for
All Time, or every day of a selected month. Expand **Listening time values** for
exact seconds. Empty periods show zeros.

Statistics uses persisted listening intervals and historical local dates and
timezones, including daylight-saving boundaries. Artist/album rankings use
historical tag strings without artist normalization; tag edits do not rewrite
past identities. Invalid legacy data can limit date attribution, with recovery
limitations shown on the page. Statistics refreshes while visible, can lag a few
seconds behind playback, and reads history without modifying it. Switching views
keeps playback and the queue; returning from Statistics preserves the previous
song view's search and selection.

## GNOME media controls

While running, MusicPlayer exposes `org.mpris.MediaPlayer2.musicplayer` on the
session D-Bus. GNOME and compatible MPRIS clients can play, pause, stop, navigate,
seek, adjust volume, shuffle, and repeat. Metadata includes track details and
cached cover artwork. Local-file `OpenUri`, Raise, and Quit are supported.
Network URIs, variable playback speed, Fullscreen, and TrackList are unsupported.
Bus failure leaves local controls usable; exiting releases the service.

GNOME decides which player receives hardware media keys. Routing depends on the
desktop settings and other running players. Wayland focus policy also controls
whether Raise brings the window forward. MusicPlayer installs no global keyboard
hooks or background media-key process.

### Optional desktop launcher

From the checkout with an existing virtual environment:

```sh
.venv/bin/python desktop/install.py install
```

This installs only the managed launcher and icon beneath `$XDG_DATA_HOME`
(default `~/.local/share`):

- `applications/io.github.OscarGR5.MusicPlayer.desktop`
- `icons/hicolor/scalable/apps/io.github.OscarGR5.MusicPlayer.svg`

The launcher runs **directly from the existing checkout and virtual environment**,
using their absolute paths. Keep both at their installed locations. It works
independently of the terminal's working directory and preserves the environment's
interpreter symlink. For another environment, add `--python "/path to/venv/bin/python"`.
Repeated installation updates managed files and refuses unrelated files or
symlinks. It needs no sudo and makes no system-wide changes. Running the app does
not install the launcher automatically.

If the checkout moves, recreate the virtual environment as needed and rerun the
installer from its new directory. GNOME can briefly cache launcher entries.
An installed launcher supplies the icon association for MPRIS; D-Bus controls
work without it. Another normal launch presents the existing application window.
Close an older running instance before launching an updated checkout.

### Uninstall

Remove the optional launcher and icon before removing the checkout:

```sh
.venv/bin/python desktop/install.py uninstall
```

The command removes only MusicPlayer's managed launcher and icon. The checkout
and virtual environment can then be removed manually. Music, database, playlists,
and listening history are preserved. No global Python package was installed by
the setup above.

## Data locations

| Data | Location | Default |
| --- | --- | --- |
| Library, playlists, history, volume | `$XDG_DATA_HOME/musicplayer/library.sqlite3` | `~/.local/share/musicplayer/library.sqlite3` |
| Cached cover thumbnails | `$XDG_CACHE_HOME/musicplayer/covers/` | `~/.cache/musicplayer/covers/` |

Artwork is stored separately as cached PNG thumbnails. Missing or invalid covers
use a placeholder. Updates and launcher uninstall preserve personal data.

## Known limitations and GTK compatibility

- Local music only; no streaming, online accounts, tag editor, or queue editor.
- Primarily tested on Fedora GNOME/Wayland. Other platforms are not verified.
- Requires Python and native GTK4/GStreamer dependencies and suitable decoders.
  No standalone package is provided.
- The upstream GTK portal shutdown crash remains unresolved. MusicPlayer avoids
  the failing portal path with a tested, application-owned `Gtk.FileChooserDialog`
  and explicit cleanup on response and shutdown. This is a temporary workaround
  using a deprecated GTK4 API, not an upstream fix. No system portal settings or
  environment overrides are required. See the [investigation](PORTAL_SHUTDOWN_INVESTIGATION.md).
- Hardware media-key routing depends on the desktop environment and other players.
- Scans refresh on launch or request, rather than continuously watching files.
  Listening time measures progressing playback, not audible output quality.

The application interface has been checked by the user on Fedora GNOME/Wayland.
Automated tests do not certify every physical keyboard, media-key route, or audio
device. Earlier engineering measurements and test matrices are retained in
[Release Verification](RELEASE_VERIFICATION.md).

## Development tests

With the virtual environment above:

```sh
.venv/bin/python -m pip install 'pytest>=7'
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q music_player desktop tests
```

System-Python users can install `python3-pytest` with dnf and use `python3` instead.
Tests need `desktop-file-utils` and D-Bus tools (`dbus-run-session` and `gdbus`).
Optional format-fixture generation uses `ffmpeg`; those cases are skipped if it
is absent. GTK tests require a graphical display. With Xvfb installed, an isolated
headless run is:

```sh
xvfb-run -a dbus-run-session -- .venv/bin/python -m pytest -q
```

Tests use temporary databases, XDG directories, generated audio, and fake audio
sinks. MPRIS tests use a private D-Bus session. The suite covers scanning,
metadata/artwork, playback, queues, playlists and temporary sorting, scroll
behavior, history, statistics, chooser cleanup, MPRIS, and launcher lifecycle.
The GitHub Actions workflow runs under Xvfb on Ubuntu 24.04; passing X11 tests
alone does not establish Wayland behavior.

## Licensing

No software license has been selected or granted in this repository. A license
is a decision to make before public distribution; no license is added as part of
v0.7.0 preparation.
