# Changelog

## 0.7.0 - 2026-10-29

First release, licensed under the [MIT License](LICENSE).

- Native GTK4 interface with a dark appearance and a permanent player bar.
- Recursive local music scanning, incremental library refresh, metadata, and
  embedded cover artwork for MP3, FLAC, M4A, Ogg/OGA, Opus, and WAV.
- Search and column sorting, including numeric duration sorting.
- Persistent playlists with saved ordering, temporary column sorting, and a
  **Saved order** button that restores the stored order without clearing search.
- Playback queue snapshots, shuffle, repeat, seeking, volume, and mute.
- Listening-history tracking with qualified plays and actual listening time;
  monthly and all-time statistics with charts and song/artist/album rankings.
- GNOME MPRIS integration and an optional user-local desktop launcher.
- Memory-leak and playback reliability fixes, including retired table-row
  cleanup, buffering/pause handling, and cancellation of pending seeks.
- Tested application-owned file chooser to avoid the unresolved upstream GTK
  portal shutdown crash.
- Playlist sorting and scroll improvements, with selection preservation,
  return-to-top after sorting, and independently scrolling sidebar content.
