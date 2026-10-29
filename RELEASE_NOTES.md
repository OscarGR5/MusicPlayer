# MusicPlayer v0.7.0

Draft release notes for review. No release or tag has been published.

MusicPlayer is a lightweight native GTK4 player for your local music collection,
with a dark interface, playlists, and listening statistics. This first release
is primarily tested on Fedora GNOME/Wayland.

## Features

- Local music scanning with incremental refresh, metadata, and cover artwork.
- Search and column sorting, with improved table and sidebar scrolling.
- Persistent playlists and saved ordering. Temporary sorting leaves saved order
  intact; **Saved order** restores it without clearing search.
- Queue snapshots that remain stable while browsing or editing playlists.
- Shuffle, repeat, seeking, volume, mute, and in-app keyboard shortcuts.
- Listening history, qualified play counts, and monthly/all-time statistics.
- GNOME MPRIS controls and an optional user-local desktop launcher.
- Memory-leak and playback reliability fixes.

## Install and launch on Fedora

Follow the [README installation instructions](README.md#fedora-installation-and-launch)
for system packages, checkout setup, and launch commands. Python 3.10+, GTK4
4.12+, GStreamer, PyGObject, GdkPixbuf, and Mutagen are required. The optional
launcher runs from the existing checkout and virtual environment.

## Audio formats

MP3, FLAC, M4A, Ogg/OGA, Opus, and WAV. Playback requires suitable installed
GStreamer decoders; codec availability can vary within a file format/container.

## Known limitations

- Local music only, with no streaming or online accounts.
- Primarily tested on Fedora GNOME/Wayland; other platforms are not verified.
- Requires Python and native GTK4/GStreamer dependencies. No standalone RPM,
  Flatpak, other packaged executable, or compiled application bundle is provided.
- Hardware media-key routing depends on the desktop environment and other players.
- No queue editor, tag editor, or continuous filesystem watcher.

The upstream GTK portal shutdown crash remains unresolved. MusicPlayer avoids
that path using a tested, application-owned GTK file chooser with explicit
cleanup when dismissed or during shutdown. This temporary workaround uses the
deprecated `Gtk.FileChooserDialog` API; it does not fix GTK itself or require
system portal configuration changes. See the
[GTK compatibility investigation](PORTAL_SHUTDOWN_INVESTIGATION.md) for details.

## Before publication

No software license has been selected. Resolve licensing before public
distribution. These notes are a draft and do not authorize publication.
