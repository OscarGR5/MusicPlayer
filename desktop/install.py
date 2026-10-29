#!/usr/bin/env python3
"""Optional user-only launcher installation. Never invoked by the application."""

import argparse
import os
import tempfile
from pathlib import Path

APP_ID = 'io.github.OscarGR5.MusicPlayer'
MANAGED = 'X-MusicPlayer-Managed=true'
ICON_MARKER = b'<!-- MusicPlayer user launcher icon.'


def data_home():
    value = Path(os.environ.get('XDG_DATA_HOME', ''))
    return value if value.is_absolute() else Path.home() / '.local/share'


def exec_argument(value):
    """Desktop Exec quoting, followed by KeyFile string escaping (not a shell)."""
    value = str(value)
    if any(char in value for char in '\n\r\0'):
        raise ValueError('Launcher paths cannot contain newline or NUL characters.')
    quoted = '"' + ''.join('\\' + char if char in '\\"`$' else char for char in value).replace('%', '%%') + '"'
    return quoted.replace('\\', '\\\\')


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            temporary.chmod(0o644)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def install(checkout, python, destination):
    checkout = Path(checkout).expanduser().resolve(strict=True)
    # Resolving the interpreter symlink would bypass the virtual environment.
    python = Path(python).expanduser().absolute() if python else checkout / '.venv/bin/python'
    if '=' in str(python):
        raise ValueError('The desktop Exec executable path cannot contain an equal sign.')
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError(f'Virtual environment interpreter unavailable: {python}')
    launch = checkout / 'desktop/launch.py'
    if not launch.is_file() or not (checkout / 'music_player/app.py').is_file():
        raise ValueError('Choose the MusicPlayer checkout directory.')
    desktop = destination / 'applications' / (APP_ID + '.desktop')
    icon = destination / 'icons/hicolor/scalable/apps' / (APP_ID + '.svg')
    check_owned(desktop, icon)
    content = (checkout / 'desktop' / (APP_ID + '.desktop.in')).read_text()
    content = content.replace('@EXEC@', '/usr/bin/env ' + exec_argument(python) + ' ' + exec_argument(launch))
    content = content.replace('@PYTHON@', str(python).replace('\\', '\\\\'))
    artwork = (checkout / 'music_player/assets' / (APP_ID + '.svg')).read_bytes()
    atomic_write(icon, artwork)
    atomic_write(desktop, content.encode())
    return desktop, icon


def check_owned(desktop, icon):
    for path, marker in ((desktop, MANAGED.encode()), (icon, ICON_MARKER)):
        if path.is_symlink() or path.exists() and marker not in path.read_bytes():
            raise ValueError(f'Refusing to change a file not managed by MusicPlayer: {path}')


def uninstall(destination):
    desktop = destination / 'applications' / (APP_ID + '.desktop')
    icon = destination / 'icons/hicolor/scalable/apps' / (APP_ID + '.svg')
    check_owned(desktop, icon)
    desktop.unlink(missing_ok=True)
    icon.unlink(missing_ok=True)
    return desktop, icon


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'uninstall'))
    parser.add_argument('--checkout', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--python', type=Path, help='Virtual environment interpreter (default: CHECKOUT/.venv/bin/python)')
    args = parser.parse_args()
    try:
        paths = install(args.checkout, args.python, data_home()) if args.action == 'install' else uninstall(data_home())
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    for path in paths:
        print(f'{"Installed" if args.action == "install" else "Removed"}: {path}')


if __name__ == '__main__':
    main()
