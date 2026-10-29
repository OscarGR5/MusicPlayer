"""Centralized in-app actions and accelerators. No desktop key capture."""

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gio, Gtk  # noqa: E402


class WindowShortcuts:
    def __init__(self, window):
        self.window = window
        controller = window.controller
        bindings = {
            'play-pause': (window._play_selected, None),
            'next': (controller.next, '<Primary>Right'),
            'previous': (controller.previous, '<Primary>Left'),
            'mute': (controller.toggle_mute, '<Primary>m'),
            'open': (lambda: window._open_file(None), '<Primary>o'),
            'search': (window._search.grab_focus, '<Primary>f'),
        }
        for name, (callback, accelerator) in bindings.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda action, parameter, callback=callback: window._perform(callback))
            window.add_action(action)
            if accelerator:
                window.get_application().set_accels_for_action(f'win.{name}', [accelerator])
        shortcut_controller = Gtk.ShortcutController.new()
        shortcut_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        shortcut_controller.add_shortcut(Gtk.Shortcut.new(
            Gtk.KeyvalTrigger.new(Gdk.KEY_space, Gdk.ModifierType(0)),
            Gtk.CallbackAction.new(self.space),
        ))
        window.add_controller(shortcut_controller)

    def space(self, widget=None, arguments=None, user_data=None):
        focused = self.window.get_focus()
        while focused is not None:
            if isinstance(focused, Gtk.Editable) or isinstance(focused, Gtk.TextView) and focused.get_editable():
                return False
            focused = focused.get_parent()
        self.window.activate_action('win.play-pause', None)
        return True
