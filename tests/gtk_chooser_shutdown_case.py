"""GTK-only portal comparison, run explicitly on the affected desktop.

Examples (each invocation is a fresh process):
    python tests/gtk_chooser_shutdown_case.py parented immediate
    python tests/gtk_chooser_shutdown_case.py unparented settled
    GDK_DEBUG=no-portals python tests/gtk_chooser_shutdown_case.py parented immediate
    python tests/gtk_chooser_shutdown_case.py direct immediate

The settled case allows 1.5 s for presentation, solely as an experiment. It is
not a shutdown workaround. No MusicPlayer modules or personal files are used.
"""

import argparse
import os
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('variant', choices=('parented', 'unparented', 'direct'))
    parser.add_argument('timing', choices=('immediate', 'settled', 'cancel-only'))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='musicplayer-gtk-chooser-') as directory:
        root = Path(directory)
        for key, folder in (('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache'),
                            ('XDG_CONFIG_HOME', 'config'), ('XDG_STATE_HOME', 'state')):
            os.environ[key] = str(root / folder)

        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Gdk', '4.0')
        from gi.repository import Gdk, Gio, GLib, Gtk

        app = Gtk.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
        state = {}

        def response(chooser, result):
            try:
                chooser.open_finish(result)
            except GLib.Error as exc:
                print('result:', exc.message, flush=True)

        def close(*_):
            print('closing:', args.timing, flush=True)
            if args.variant == 'direct':
                print('chooser mapped:', state['chooser'].get_mapped(), flush=True)
                state['chooser'].destroy()
            else:
                state['cancel'].cancel()
            if args.timing != 'cancel-only':
                state['window'].destroy()
            else:
                GLib.timeout_add(300, lambda: state['window'].destroy())
            return GLib.SOURCE_REMOVE

        def show():
            window = state['window']
            if args.variant == 'direct':
                chooser = Gtk.FileChooserDialog(title='GTK chooser shutdown QA',
                                                transient_for=window, modal=True,
                                                action=Gtk.FileChooserAction.OPEN)
                chooser.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Open', Gtk.ResponseType.ACCEPT)
                chooser.connect('response', close)
                state['chooser'] = chooser
                chooser.present()
            else:
                chooser = Gtk.FileDialog(title='GTK chooser shutdown QA')
                state['chooser'] = chooser
                state['cancel'] = Gio.Cancellable()
                chooser.open(window if args.variant == 'parented' else None,
                             state['cancel'], response)
            print('requested:', args.variant, flush=True)
            if args.timing in ('immediate', 'cancel-only'):
                close()
            else:
                GLib.timeout_add(1500, close)
            return GLib.SOURCE_REMOVE

        def activate(_):
            window = Gtk.ApplicationWindow(application=app, title='GTK shutdown QA')
            state['window'] = window
            window.present()
            print('display:', type(Gdk.Display.get_default()).__name__, flush=True)
            GLib.timeout_add(300, show)

        app.connect('activate', activate)
        return app.run(['gtk-chooser-shutdown-qa'])


if __name__ == '__main__':
    raise SystemExit(main())
