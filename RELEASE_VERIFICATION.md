# Release Verification

Phase 7, October 29, 2026. Base: `79974f86297c4b5f80b876c326f0fe923a5ca213`.
Local and fetched remote `main` matched that Phase 6 merge before changes.

**Recommendation: do not merge or release yet.** The fixes below pass their
regressions, but a native GTK portal shutdown crash remains reproducible on the
target Fedora GNOME/Wayland environment. Physical desktop checks also remain open.

## Audit findings

All application modules, persistence, scanner/metadata/artwork, statistics,
MPRIS, installer/launcher, and existing tests were reviewed. Working code was
retained unless the checks below demonstrated a defect or deprecated API use.
Queue snapshots, history schema, and the 30-second/50% counting policy are unchanged.

| Severity | Finding | Disposition |
| --- | --- | --- |
| High | Retired song-table cells retained `Gtk.ListItem` objects through their context-menu references and callbacks. A 289-row synthetic index grew from 183 MiB to 1,118 MiB over 60 playlist/All Songs cycles. | Fixed: clear row references on every factory unbind, including cover cells, and avoid capturing the cell in its gesture closure. A regression checks object lifetime and rebound context-menu callbacks. |
| High, unresolved upstream | Closing during native chooser presentation crashes the GTK Wayland portal callback before the parent-window handle export completes. | Release blocker. Reproduced with MusicPlayer and a minimal GTK-only program. Both `FileChooserNative` and `FileDialog` reach the failing backend. |
| Medium | Pause during buffering can leave the engine stuck in Buffering because the pipeline is already PAUSED and emits no new state transition. | Fixed: publish the successful synchronous pause. Regression also checks that a late buffering completion cannot resume explicit pause. |
| Medium | Stop/error left pending or dragged seek edits active, allowing a stale seek against a stopped pipeline and inconsistent controls. | Fixed: cancel edits on terminal playback states. Four regression cases cover drag/debounce and stop/error. |
| Medium, accessibility | The capture-phase Space shortcut intercepted focused buttons, triggering playback instead of the button's action. | Fixed: defer to native button/menu/dropdown/editable behavior. Focus-guard callbacks are tested; physical key delivery remains unverified. |
| Low | Statistics drawing used deprecated style-context color APIs. Native file selection used a deprecated chooser API. | Updated to `Gtk.Widget.get_color` and asynchronous `Gtk.FileDialog`, both available since GTK 4.10. File-dialog cancellation, errors, duplicate requests, and late results are tested. |
| Test reliability | Application and launcher lifecycle tests depended on the desktop sound server; the one-second lifecycle fixture could finish before assertions under load. | Use synchronized fake output everywhere and a longer lifecycle fixture. No new runtime dependency. |

## Unresolved release blocker

Environment: Fedora 44, GTK 4.22.5, Python 3.14, GNOME/Wayland, session file-chooser
portal. Open the native chooser, then close the application before presentation
finishes. The process exits with SIGSEGV in GTK's `window_handle_exported`
(`gtkfilechoosernativeportal.c`), reached through `export_handle_done` and the
Wayland exported-handle callback. Waiting until presentation settles avoids the
crash in the minimal GTK reproduction; physical chooser interaction has not
been verified.

Reproduce without personal music/history, launcher installation, or MPRIS
registration, from the checkout on the affected graphical session:

```sh
PYTHONPATH=. .venv/bin/python tests/portal_shutdown_case.py
```

The script uses temporary XDG directories and an empty music folder. A crash
can leave that temporary directory behind. It is deliberately separate from
the default suite: an isolated Xvfb bus does not test the user's GNOME portal,
and a passing callback test cannot establish that the native backend is safe.

The `FileDialog` migration removes deprecated application API use but **does not
resolve this crash**. Deferring shutdown cleanup also failed. No timed workaround,
unparented chooser, system-library patch, or portal configuration change is shipped.
Before merging, verify an upstream GTK/portal fix or a safe application workaround
against this reproducer and normal file selection/cancellation on Wayland.

## Verification matrix

“GTK automated” means real widgets, layout, signals, or callback paths. It does
not mean physical mouse or keyboard input.

| Feature | Automated verification | Desktop/manual verification |
| --- | --- | --- |
| Playback and queue | Play, pause/resume, seek both directions, EOS, repeat/shuffle, paused navigation, stop, errors, missing/unreadable files, external files, rapid switching, reusable pipeline, shutdown | Read-only playback with local library and fake output; no audible-output sign-off |
| Listening history | Temporary SQLite and simulated clocks: thresholds, one play/session, replay, pause/seek/stall/buffer exclusion, error/exit checkpoints, retry idempotence, crash recovery, restart, midnight/month boundaries, migration/data retention | No personal history opened or modified |
| Statistics | Exact agreement with persisted intervals/plays after mixed playback and restart; aggregation, historical dates/timezones, read-only queries, caching, worker delivery | Synthetic screenshots reviewed, including the chart and scroll end |
| Library | Existing index, background scan, add/remove/restore, malformed tags/art, deduplication, stable IDs, search/sort, responsive delivery, scan cancellation | Local library scanned into a temporary index; source files read only |
| Playlists | Create/rename/delete, multiple memberships, saved order/moves, unavailable restoration, restart, queue preservation during edits/deletion, row cleanup | Synthetic long names, ellipsis, multi-selection appearance reviewed |
| GTK layout and controls | Both 1040 × 640 and 720 × 480, light/dark; player alignment, disabled states, tooltips, search/sort, focus guards, context-menu callbacks, dialog validation, statistics scrolling | Rendered GTK screenshots inspected; physical gestures and focus traversal unverified |
| File chooser | Asynchronous result/cancel/error/late-result ownership tested with controlled callbacks; standalone native shutdown reproduction fails on host GTK | Physical selection/cancel/reopen remains open; pre-presentation shutdown is blocked |
| MPRIS | Private D-Bus sessions: registration/cleanup, metadata/art, transport, seeking/signals, modes/volume, malformed calls, conflict/missing/lost bus, reactivation; independent `gdbus` and `busctl` | Actual GNOME panel, hardware media keys, and desktop focus policy unverified |
| Desktop installer | Temporary XDG only: validation, SVG rendering, other working directories, spaces/quotes/backslashes/percent paths, repeat install, relocation, missing interpreter, ownership/symlink protection, safe uninstall | No installation or removal in the real desktop profile |
| Shutdown/resources | Scan cancellation, close during an active statistics query with worker/connection cleanup, SQLite integrity, repeated switching/navigation, playback memory observation | Native chooser shutdown exception described above; multi-hour listening not measured |

## Resource measurements

One local Fedora run, using an isolated database/cache and synchronized fake
audio output. Numbers describe this machine, not performance guarantees. Only
aggregate counts/timings are published; no music filenames or listening data.

| Check | Result |
| --- | --- |
| Local files fingerprinted / indexed audio entries | 289 / 286 |
| Initial metadata/artwork scan into empty temporary index | 6.07 s |
| Indexed window mapped with songs visible | 1.98 s after window construction began; imports already loaded |
| Unchanged background rescan | 0.026 s; maximum 10 ms heartbeat gap 26 ms |
| Idle / paused CPU over separate 8 s native-main-loop samples | 3.29% / 0.83% of one CPU |
| 180 s playback RSS | 200.1 → 200.5 MiB |
| 100 track loads/prerolls, one pipeline | 2.40 s |
| 30 Statistics → playlist → All Songs cycles | 5.26 s, RSS growth 0.7 MiB |
| Separate 60-cycle row-leak regression measurement after fix | 185.1 → 185.6 MiB; live ListItems stayed at 1,025 |
| Normal close after resource run | 0.019 s; zero scan/statistics workers remaining |
| Persistence after resource run | Playlist order retained; SQLite `integrity_check` returned `ok` |

The playback sampling harness used polling, so its CPU figure is not used as
an idle-performance claim. Three minutes of playback and repeated navigation
showed no continuing memory growth after the row fix; this is not a multi-hour soak.
SHA-256, size, and modification-time comparisons confirmed all 289 original
music files unchanged after QA. No source-music writes are performed by the QA scripts.

## Warnings and compatibility

Application color drawing now uses the supported
[widget color API](https://docs.gtk.org/gtk4/method.Widget.get_color.html), and
file selection uses [asynchronous FileDialog](https://docs.gtk.org/gtk4/method.FileDialog.open.html).
GTK 4.10 or later is required. Existing custom-entry playlist dialogs still use
`Gtk.Dialog`, deprecated but supported in GTK4. Replacing their response/focus
lifecycle requires rebuilding that interaction; this pass retains it, especially
while physical keyboard checks are pending.

PyGObject emits Python 3.14 asyncio event-loop-policy deprecations and an import-time
`GLib.unix_signal_add_full` alias warning. Installer tests emit the upstream
`Gio.DesktopAppInfo` alias warning; application code does not use that alias.
Warnings are not suppressed. These warnings are separate from the native portal crash.

## Manual desktop checklist

After the release blocker is resolved, use the documented optional installer
only when you choose to install into your profile:

1. Launch from GNOME search and another working directory. Check the icon and
   that another launch presents the same window; move the checkout, reinstall,
   and check the updated launcher. Uninstall and confirm music/history remain.
2. At both sizes and in both themes, use the mouse to drag progress forward and
   backward and adjust volume. Use Ctrl+Click/Shift+Click, right-click song and
   playlist menus, and Move Up/Down. Check long names and missing-cover placeholders.
3. Tab through controls. Deliver Space to the song view, buttons, dropdowns,
   menus, search, and playlist-name entry; check Ctrl+F/O/M/Left/Right and Shift+F10.
4. Open/cancel/reopen the native chooser, select a local file, and test shutdown
   during presentation using the reproducer. Confirm no native crash or leftover dialog.
5. Play from All Songs and a playlist, pause/seek/skip/repeat/shuffle, close and
   reopen, and compare Statistics with the session. Test physical media keys and
   GNOME's media panel; verify reactivation and service removal on close.

Final Fedora suite: **132 passed** in 26.98 s, including the private-bus MPRIS
suite. Syntax compilation and `git diff --check` passed. The standalone native
portal shutdown case remains a separate failure. The initial baseline was 120
passing tests. CI status is recorded in the Phase 7 pull request. The Linux CI workflow uses standard
system packages, Xvfb/Openbox, fake audio output, and private D-Bus sessions, with no secrets.
Its passing status cannot clear the Wayland portal blocker or physical-input checks.
