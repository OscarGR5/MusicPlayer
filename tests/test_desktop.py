import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from gi.repository import Gio

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('desktop_install', ROOT / 'desktop/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / 'Checkout with spaces $ cash % café'
    (root / 'desktop').mkdir(parents=True)
    (root / 'music_player/assets').mkdir(parents=True)
    (root / '.venv/bin').mkdir(parents=True)
    for name in ('install.py', 'launch.py', installer.APP_ID + '.desktop.in'):
        shutil.copyfile(ROOT / 'desktop' / name, root / 'desktop' / name)
    shutil.copyfile(ROOT / 'music_player/assets' / (installer.APP_ID + '.svg'), root / 'music_player/assets' / (installer.APP_ID + '.svg'))
    (root / 'music_player/app.py').write_text('# test checkout')
    python = root / '.venv/bin/python'
    python.write_text('''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
Path(os.environ['MUSICPLAYER_LAUNCH_LOG']).write_text(json.dumps({'argv':sys.argv, 'cwd':os.getcwd()}))
''')
    python.chmod(0o755)
    return root


def test_install_validate_launch_repeat_move_and_uninstall(checkout, tmp_path, monkeypatch):
    destination = tmp_path / 'xdg data'
    log = tmp_path / 'launch.json'
    monkeypatch.setenv('MUSICPLAYER_LAUNCH_LOG', str(log))
    desktop, icon = installer.install(checkout, None, destination)
    assert desktop.name == installer.APP_ID + '.desktop'
    assert icon.read_bytes() == (ROOT / 'music_player/assets' / (installer.APP_ID + '.svg')).read_bytes()
    result = subprocess.run(['desktop-file-validate', str(desktop)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    info = Gio.DesktopAppInfo.new_from_filename(str(desktop))
    assert info.get_name() == 'MusicPlayer'
    assert info.get_executable() == '/usr/bin/env'
    assert info.get_boolean('Terminal') is False
    assert info.get_boolean('DBusActivatable') is False
    info.launch([], None)
    from test_audio import wait_for
    wait_for(log.exists)
    assert json.loads(log.read_text())['argv'] == [str(checkout / '.venv/bin/python'), str(checkout / 'desktop/launch.py')]
    # A second install is safe and does not touch unrelated desktop files.
    unrelated = desktop.parent / 'unrelated.desktop'
    unrelated.write_text('unchanged')
    installer.install(checkout, None, destination)
    moved = checkout.with_name('Moved checkout with spaces')
    checkout.rename(moved)
    installer.install(moved, None, destination)
    assert str(moved / '.venv/bin/python') in Gio.DesktopAppInfo.new_from_filename(str(desktop)).get_commandline()
    installer.uninstall(destination)
    installer.uninstall(destination)
    assert not desktop.exists() and not icon.exists()
    assert unrelated.read_text() == 'unchanged'


@pytest.mark.parametrize('name', ['Quotes " and apostrophe\' and backtick`', 'Backslash \\ and percent %f'])
def test_exec_quoting_special_paths(checkout, tmp_path, monkeypatch, name):
    moved = checkout.with_name(name)
    checkout.rename(moved)
    log = tmp_path / 'special.json'
    monkeypatch.setenv('MUSICPLAYER_LAUNCH_LOG', str(log))
    desktop, _ = installer.install(moved, None, tmp_path / 'xdg')
    info = Gio.DesktopAppInfo.new_from_filename(str(desktop))
    assert info is not None
    info.launch([], None)
    from test_audio import wait_for
    wait_for(log.exists)
    assert json.loads(log.read_text())['argv'][1] == str(moved / 'desktop/launch.py')


def test_install_refuses_unowned_files_symlinks_and_missing_venv(checkout, tmp_path):
    destination = tmp_path / 'xdg'
    desktop = destination / 'applications' / (installer.APP_ID + '.desktop')
    desktop.parent.mkdir(parents=True)
    desktop.write_text('unrelated')
    with pytest.raises(ValueError, match='not managed'):
        installer.install(checkout, None, destination)
    with pytest.raises(ValueError, match='not managed'):
        installer.uninstall(destination)
    assert desktop.read_text() == 'unrelated'
    desktop.unlink()
    desktop.symlink_to(tmp_path / 'outside')
    with pytest.raises(ValueError, match='not managed'):
        installer.install(checkout, None, destination)
    desktop.unlink()
    (checkout / '.venv/bin/python').unlink()
    with pytest.raises(ValueError, match='interpreter unavailable'):
        installer.install(checkout, None, destination)
    assert not desktop.exists()


def test_cli_user_xdg_install_uninstall(checkout, tmp_path):
    env = {**os.environ, 'XDG_DATA_HOME': str(tmp_path / 'private-xdg')}
    for action in ('install', 'install', 'uninstall', 'uninstall'):
        result = subprocess.run([sys.executable, str(ROOT / 'desktop/install.py'), action, '--checkout', str(checkout)],
                                env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    assert not (Path(env['XDG_DATA_HOME']) / 'applications' / (installer.APP_ID + '.desktop')).exists()


def test_original_svg_renders_at_small_and_large_sizes():
    from music_player.artwork import GdkPixbuf
    icon = ROOT / 'music_player/assets' / (installer.APP_ID + '.svg')
    assert '<text' not in icon.read_text() and 'gradient' not in icon.read_text().lower()
    for size in (16, 32, 64, 128, 256):
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon), size, size, True)
        assert pixbuf.get_width() == pixbuf.get_height() == size
