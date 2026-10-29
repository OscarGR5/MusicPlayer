# GTK portal shutdown investigation

October 29, 2026. Baseline: `3efd4b44de4a80a268857287c6b7c4e2872bcfc6`.

## Decision

Temporarily use a directly presented, parented `Gtk.FileChooserDialog` for local
file selection. GTK owns its file-browsing widgets; MusicPlayer owns the dialog
and disconnects its response handler before destroying it on response or shutdown.
Duplicate requests and requests after shutdown are ignored. Accepted local paths
still go through `PlaybackController.open_file`; playback, queue, history, and
music-file handling are unchanged.

This avoids the failing portal/export path rather than attempting to catch a
native crash. No shutdown delay, crash handler, environment override, system
package installation, or GTK/portal configuration change is shipped.

`Gtk.FileChooserDialog`, its `Gtk.FileChooser` interface, and inherited dialog
button/response APIs are [deprecated since GTK 4.10](https://docs.gtk.org/gtk4/class.FileChooserDialog.html).
This is a temporary GTK4 compatibility measure. Restore `Gtk.FileDialog` only
after testing an upstream fix against both native failure cases below and the
normal browsing/cancellation/shutdown matrix. Do not infer a fix from a version
number or a successful X11 run. The chooser retains standard folder browsing and
single-file Open behavior. Remote URIs remain rejected by MusicPlayer.

## Exact affected configuration

| Component | Tested value |
| --- | --- |
| OS | Fedora Linux 44, x86_64 |
| GTK | `gtk4-4.22.5-2.fc44.x86_64`, runtime 4.22.5 |
| GTK ELF build ID | `b3413ec96610368a1281548155c59bce1fb47735` |
| GLib | `glib2-2.88.4-1.fc44.x86_64` |
| Python / PyGObject | Python 3.14 / `python3-gobject-3.56.3-1.fc44.x86_64` |
| Introspection | `gobject-introspection-1.86.0-3.fc44.x86_64` |
| Session / GDK | GNOME, Wayland / `GdkWaylandDisplay`, `GDK_BACKEND=wayland` |
| Shell / compositor | `gnome-shell-50.6-1.fc44.x86_64`, `mutter-50.6-2.fc44.x86_64` |
| Portal | `xdg-desktop-portal-1.22.1-1.fc44.x86_64` |
| GNOME portal backend | `xdg-desktop-portal-gnome-50.0-1.fc44.x86_64` |

Tests used temporary XDG data, cache, configuration, and state directories.
Application shutdown probes configured an empty temporary music folder and
prevented MPRIS registration. File-selection tests used generated WAV audio and
a synchronized GStreamer fake audio sink. No personal music or listening database
was opened by these application tests. Hash, size, and mtime of the selected
generated audio were unchanged after playback.

## Confirmed native failures

1. The original MusicPlayer reproducer exited with SIGSEGV before the change.
   Its symbolized stack shows the async export completion using a null parent
   surface after the window was destroyed:

   ```text
   SIGSEGV
   #0 GDK_TOPLEVEL_GET_IFACE (ptr=0x0) at gdk/gdktoplevel.h:148
   #1 gdk_toplevel_export_handle_finish (toplevel=0x0, ...) at gdk/gdktoplevel.c:847
   #2 export_handle_done (...) at gtk/gtkwindow.c:6615
   #3 g_task_return_now
   #4 complete_in_idle_cb
   #5 g_idle_dispatch
   #6 g_main_context_dispatch_unlocked
   #7 g_main_context_iterate_unlocked
   #8 g_main_context_iteration
   #9 g_application_run
   ```

2. A GTK-only variant cancels the dialog immediately but keeps the parent alive
   until a later test callback. It also crashes. GDB confirms cleared chooser
   state, independently of MusicPlayer or parent destruction:

   ```text
   SIGSEGV
   #0 window_handle_exported (...) at gtk/gtkfilechoosernativeportal.c:462
      self = <GtkFileChooserNative>, data = 0x0, self->mode_data = 0x0
   #1 export_handle_done (...) at gtk/gtkwindow.c:6621
   #2 g_task_return_now
   #3 g_task_return
   #4 xdg_exported_handle_v1 (...) at gdk/wayland/gdktoplevel-wayland.c:1996
   #5 ffi_call_unix64
   #6 ffi_call_int
   #7 ffi_call
   #8 wl_closure_invoke
   #9 dispatch_event
   #10 wl_display_dispatch_queue_pending
   #11 _gdk_wayland_display_queue_events
   ```

Fedora debuginfod supplied matching GTK debug symbols and supplementary DWARF
in the user cache; no packages were installed. Full local logs are under
`/tmp/musicplayer-portal-investigation/` (`confirmed-gdb.txt` and
`confirmed-cancel-gdb.txt`). Paths above omit build-directory prefixes.

The [GTK 4.22.5 portal source](https://github.com/GNOME/gtk/blob/4.22.5/gtk/gtkfilechoosernativeportal.c)
clears the connection and `mode_data` in the hide path; the exported-handle
callback later dereferences that state. The
[window export implementation](https://github.com/GNOME/gtk/blob/4.22.5/gtk/gtkwindow.c)
reads the current window surface on completion. These observations confirm a
native asynchronous lifetime defect. They do not establish that every GTK or
portal version has the same defect, or identify an already released upstream fix.

## Isolated comparison

`tests/gtk_chooser_shutdown_case.py` imports GTK only, with no player modules.
Each row below represents ten fresh processes for each timing on the session
above. Immediate closes synchronously after `open`/`present`. Settled permits
1.5 s of presentation before close; this timeout exists only in the experiment.
Direct chooser runs confirmed `get_mapped() == True` before the settled close.
Native settled runs are timed presentation probes, not physical-input sign-off.

| Variant | Immediate: 10 runs | Settled: 10 runs |
| --- | --- | --- |
| Parented `Gtk.FileDialog` | 10 SIGSEGV | 10 exit 0 |
| Unparented `Gtk.FileDialog` | 10 exit 0, all with invalid-connection GLib criticals | 10 exit 0 |
| Parented `Gtk.FileDialog`, process-local `GDK_DEBUG=no-portals` | 10 exit 0 | 10 exit 0 |
| Direct parented `Gtk.FileChooserDialog` | 10 exit 0 | 10 exit 0, all mapped |

No direct-chooser run emitted a GLib critical. An additional unparented D-Bus
trace showed `Close` returning `UnknownMethod` because the request object did
not yet exist, followed by `g_dbus_connection_send_message_with_reply_finish`
asserting that its cleared connection was invalid. Exit 0 alone is insufficient
for this variant. It also loses the transient parent relationship.

The process-local diagnostic is documented by
[GDK](https://docs.gtk.org/gtk4/running.html). It is not used by production code.
Keeping Python references alive did not protect GTK-only cancellation: the
comparison script retains its chooser, cancellable, and window in a dictionary.
There is no public FileDialog presentation-complete callback to synchronize
shutdown with handle export. Blocking shutdown until a file response or guessing
a safe timeout is not an acceptable application fix.

Reproduce from the checkout in the affected graphical session:

```sh
.venv/bin/python tests/gtk_chooser_shutdown_case.py parented immediate
.venv/bin/python tests/gtk_chooser_shutdown_case.py parented cancel-only
.venv/bin/python tests/gtk_chooser_shutdown_case.py unparented immediate
GDK_DEBUG=no-portals .venv/bin/python tests/gtk_chooser_shutdown_case.py parented immediate
.venv/bin/python tests/gtk_chooser_shutdown_case.py direct immediate
.venv/bin/python tests/gtk_chooser_shutdown_case.py direct settled
```

The first two commands intentionally still crash the host GTK. They are kept
outside pytest and are sufficient GTK-only upstream bug-report attachments,
along with this environment table and the two stacks. No upstream report was
submitted as part of this task.

## Application verification

The original `tests/portal_shutdown_case.py` now passes through the fallback.
It also accepts `--timing mapped`, `--requests 30`, and `--quit`. Unlike the native
comparison's timed settled case, mapped waits for the actual chooser map state;
its five-second deadline fails the test rather than delaying application shutdown.
It asserts that the chooser is released and no chooser toplevel remains after
application exit. Callback assertion failures propagate to the process exit code.

The complete Fedora Wayland suite passed **135/135** tests in 29.92 s. The five
chooser regressions passed ten successive runs (**50/50**) with
`G_DEBUG=fatal-criticals`. The application
shutdown matrix passed **80/80** fresh processes: ten per immediate/mapped ×
one/30 requests × window-close/application-quit. A private bus without service
directories confirmed the portal was neither owned nor activatable; five chooser
tests and **20/20** immediate/mapped shutdown processes passed on that bus
with `GTK_A11Y=none` and `G_DEBUG=fatal-criticals`. All
subprocesses exited within their watchdog deadlines. A fallback D-Bus trace
contained no FileChooser `OpenFile` request. Final process inspection found no
chooser probes or private test buses, and introspection of the session portal
request subtree returned an empty node. Syntax compilation and whitespace
checks passed.

For the complete suite, set all four XDG homes before GTK is imported, in addition
to the per-test temporary directories. This also isolates GLib's cached paths:

```sh
qa_root=$(mktemp -d /tmp/musicplayer-suite-XXXXXX)
XDG_DATA_HOME="$qa_root/data" XDG_CACHE_HOME="$qa_root/cache" \
XDG_CONFIG_HOME="$qa_root/config" XDG_STATE_HOME="$qa_root/state" \
GDK_BACKEND=wayland .venv/bin/python -m pytest -q
```

Results are also recorded in `RELEASE_VERIFICATION.md`. Automated GTK calls drive the
real browse, selection, cancel, and close paths; they do not certify physical
mouse/keyboard input, audible output, or GNOME focus behavior. No X11 or older
GTK compatibility result is claimed by this Fedora follow-up. PR #6 must remain
draft and unmerged; no release is authorized.
