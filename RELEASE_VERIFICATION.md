# Release Verification

Phase 7, October 29, 2026. Base: `79974f86297c4b5f80b876c326f0fe923a5ca213`.
Local and fetched remote `main` matched that Phase 6 merge before changes.

**Keep PR #6 draft and unmerged; do not release.** The GTK portal shutdown
blocker is avoided by a validated temporary application-owned chooser. The
underlying GTK-only native reproducer still crashes. Physical desktop checks
remain open. See [PORTAL_SHUTDOWN_INVESTIGATION.md](PORTAL_SHUTDOWN_INVESTIGATION.md)
for the exact environment, comparison matrix, backtraces, and restoration criteria.

## Audit findings

All application modules, persistence, scanner/metadata/artwork, statistics,
MPRIS, installer/launcher, and existing tests were reviewed. Working code was
retained unless the checks below demonstrated a defect or deprecated API use.
Queue snapshots, history schema, and the 30-second/50% counting policy are unchanged.

| Severity | Finding | Disposition |
| --- | --- | --- |
| High | Retired song-table cells retained `Gtk.ListItem` objects through their context-menu references and callbacks. A 289-row synthetic index grew from 183 MiB to 1,118 MiB over 60 playlist/All Songs cycles. | Fixed: clear row references on every factory unbind, including cover cells, and avoid capturing the cell in its gesture closure. A regression checks object lifetime and rebound context-menu callbacks. |
| High, upstream defect avoided | Closing during native chooser presentation crashes the GTK Wayland handle-export completion after cancellation or parent destruction. | Temporary direct `Gtk.FileChooserDialog` fallback, with explicit destruction and response-handler cleanup. Native GTK-only reproduction still fails; application reproduction now passes. |
| Medium | Pause during buffering can leave the engine stuck in Buffering because the pipeline is already PAUSED and emits no new state transition. | Fixed: publish the successful synchronous pause. Regression also checks that a late buffering completion cannot resume explicit pause. |
| Medium | Stop/error left pending or dragged seek edits active, allowing a stale seek against a stopped pipeline and inconsistent controls. | Fixed: cancel edits on terminal playback states. Four regression cases cover drag/debounce and stop/error. |
| Medium, accessibility | The capture-phase Space shortcut intercepted focused buttons, triggering playback instead of the button's action. | Fixed: defer to native button/menu/dropdown/editable behavior. Focus-guard callbacks are tested; physical key delivery remains unverified. |
| Low | Statistics drawing used deprecated style-context color APIs. Native file selection used a deprecated chooser API. | Updated to `Gtk.Widget.get_color` and asynchronous `Gtk.FileDialog`, both available since GTK 4.10. The follow-up temporarily replaces FileDialog with the deprecated direct chooser after native lifetime testing; real browse/select/cancel/close paths are covered. |
| Test reliability | Application and launcher lifecycle tests depended on the desktop sound server; the one-second lifecycle fixture could finish before assertions under load. | Use synchronized fake output everywhere and a longer lifecycle fixture. No new runtime dependency. |
| Test reliability | Exact-size checks compared requested window allocation with content size excluding theme borders, failing under GTK 4.14. | Compare window bounds including theme borders, retaining exact 1040 × 640 and 720 × 480 checks across themes. |

## Portal shutdown follow-up

The affected configuration is Fedora 44, GTK 4.22.5, GLib 2.88.4, Python 3.14,
PyGObject 3.56.3, GNOME Shell/Mutter 50.6 on Wayland, xdg-desktop-portal 1.22.1,
and xdg-desktop-portal-gnome 50.0. GDB with matching Fedora GTK symbols confirms
both a null surface in `export_handle_done` after parent destruction and null
`mode_data` in `window_handle_exported` after cancellation with the parent alive.
These are native failures, independent of Python chooser ownership.

The GTK-only comparison ran ten fresh processes per variant and timing:
parented FileDialog crashed 10/10 immediate closes and passed 10/10 timed settled
closes; unparented FileDialog exited 0 but emitted invalid-connection GLib criticals
in all 10 immediate cases; process-local `GDK_DEBUG=no-portals` and the directly
presented GTK chooser each passed 10 immediate and 10 settled closes. The direct
chooser was mapped before each settled close. Exit 0 with criticals did not count
as safe. No production environment override or shutdown delay was added.

The temporary `Gtk.FileChooserDialog` preserves parent/modal behavior, ordinary
browsing, local path validation, cancellation, and existing playback integration.
The window disconnects the response handler and destroys the chooser before
releasing it, guards stale responses, and rejects duplicate/post-close requests.
GTK's deprecated chooser/dialog APIs are used deliberately until a native fix
passes the documented Wayland tests. No player architecture or history policy changed.

Run the application regression explicitly on Wayland:

```sh
PYTHONPATH=. .venv/bin/python tests/portal_shutdown_case.py
PYTHONPATH=. .venv/bin/python tests/portal_shutdown_case.py --timing mapped --requests 30
PYTHONPATH=. .venv/bin/python tests/portal_shutdown_case.py --quit --requests 30
```

The GTK-only native failure remains available in
`tests/gtk_chooser_shutdown_case.py`. Details and minimal upstream bug-report
information are in [PORTAL_SHUTDOWN_INVESTIGATION.md](PORTAL_SHUTDOWN_INVESTIGATION.md).

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
| File chooser | Real GTK folder browsing, generated local file selection/playback, cancel/reopen, window close, stale responses, repeated requests, error paths, immediate/mapped shutdown; temporary XDG directories | Application shutdown blocker avoided; physical pointer/keyboard and focus behavior remain unverified |
| MPRIS | Private D-Bus sessions: registration/cleanup, metadata/art, transport, seeking/signals, modes/volume, malformed calls, conflict/missing/lost bus, reactivation; independent `gdbus` and `busctl` | Actual GNOME panel, hardware media keys, and desktop focus policy unverified |
| Desktop installer | Temporary XDG only: validation, SVG rendering, other working directories, spaces/quotes/backslashes/percent paths, repeat install, relocation, missing interpreter, ownership/symlink protection, safe uninstall | No installation or removal in the real desktop profile |
| Shutdown/resources | Scan cancellation, close during an active statistics query with worker/connection cleanup, SQLite integrity, repeated switching/navigation, playback memory observation | Application chooser shutdown regression passes; underlying native GTK defect retained as a separate reproducer; multi-hour listening not measured |

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
[widget color API](https://docs.gtk.org/gtk4/method.Widget.get_color.html), and GTK 4.10 or later is required. File selection temporarily uses
[Gtk.FileChooserDialog](https://docs.gtk.org/gtk4/class.FileChooserDialog.html),
its `Gtk.FileChooser` interface, and inherited dialog button/response APIs,
deprecated since GTK 4.10 but available in GTK4. This deliberate compatibility
fallback avoids the confirmed portal lifetime defect. Deprecation warnings remain
visible. Existing custom-entry playlist dialogs also retain `Gtk.Dialog`.
Restore FileDialog only after an upstream fix passes the native and application
Wayland cases; no version-based assumption is made.

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
4. Physically open/cancel/reopen the application-owned chooser and select a local
   file. Automated browsing/playback and immediate/mapped shutdown pass; confirm
   physical input and GNOME focus behavior without a leftover dialog.
5. Play from All Songs and a playlist, pause/seek/skip/repeat/shuffle, close and
   reopen, and compare Statistics with the session. Test physical media keys and
   GNOME's media panel; verify reactivation and service removal on close.

Final follow-up Fedora Wayland suite: **135 passed** in 29.92 s, including the
private-bus MPRIS suite. The five real chooser regressions also passed ten successive runs
(**50/50**) with `G_DEBUG=fatal-criticals`. Generated-file selection and playback retained
identical SHA-256, size, and mtime. No personal music or listening database was
used by follow-up tests. The initial Phase 7 baseline was 120 passing tests;
the pre-follow-up head had 132.

The Fedora application shutdown matrix passed **80/80** fresh processes with
`G_DEBUG=fatal-criticals`: ten each for immediate/mapped × one/30 requests ×
window-close/application-quit. A private D-Bus session with no service directories
confirmed `org.freedesktop.portal.Desktop` was neither owned nor activatable;
all five chooser tests and **20/20** more immediate/mapped shutdown processes
passed there (`GTK_A11Y=none` on that private bus). All processes exited within their watchdog deadlines. GTK toplevel
assertions found no remaining chooser after cleanup. A production-path D-Bus
trace contained no `org.freedesktop.portal.FileChooser.OpenFile` call. Final
process inspection found no chooser probes or private test buses; the session
portal request subtree was empty. Syntax compilation and `git diff --check` passed.

The Linux CI workflow uses standard system packages, Xvfb, fake audio output,
and private D-Bus sessions, with no secrets. Passing X11 CI cannot establish
native Wayland portal safety or physical-input sign-off.

## Dark appearance and player slider follow-up

The complete Fedora suite passes **135 tests** in 18.91 s under nested Xwayland
with a private D-Bus session after the dark-startup and player layout changes.
Optional capture callbacks timed out for some occluded windows on the host
Wayland session; the isolated display run passed with captures enabled.
Startup sets GTK's application dark preference before window
creation. A separate process with an isolated GNOME light preference rendered a
light baseline window followed by dark library, playlist, statistics, and playlist
dialog surfaces. The real desktop appearance setting was unchanged.

Progress/volume drawing widths measured **300/140 px at 1040×640** and
**228/111 px at 720×480**, including live resizing back to the larger size.
The player bar remained 70 px high. Synthetic light/dark captures and bounds
checks confirmed centered playback controls, truncated long metadata, and no
player-bar clipping or overlap.

XTest pointer events in a nested Xwayland session verified progress dragging,
one seek on release, volume dragging, and mute/unmute at both sizes in both
themes, using generated audio and a temporary database. This exposed a legacy
event observer receiving a null event through the GTK binding. A grouped native
drag gesture now tracks begin/end/cancel without taking over the scale's drag.
Automated regressions cover release, cancellation, and debounced keyboard/wheel
edits. These checks do not replace physical Wayland input sign-off.
