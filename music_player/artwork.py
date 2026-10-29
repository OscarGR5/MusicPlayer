"""Create small, content-addressed artwork thumbnails on the scan worker."""

import hashlib
import os
import tempfile
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

from .metadata import MAX_ARTWORK_BYTES

THUMBNAIL_SIZE = 96


def cache_artwork(data, directory):
    if not data or len(data) > MAX_ARTWORK_BYTES:
        return None
    directory = Path(directory)
    target = directory / f"{THUMBNAIL_SIZE}-{hashlib.sha256(data).hexdigest()}.png"
    if target.is_file():
        return str(target)
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        loader = GdkPixbuf.PixbufLoader.new()

        def prepared(loader, width, height):
            scale = min(1, THUMBNAIL_SIZE / max(width, height))
            loader.set_size(max(1, int(width * scale)), max(1, int(height * scale)))

        loader.connect("size-prepared", prepared)
        try:
            loader.write(data)
            loader.close()
        except GLib.Error:
            # Close even when the decoder rejects its input.
            try:
                loader.close()
            except GLib.Error:
                pass
            return None
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            return None
        descriptor, temporary = tempfile.mkstemp(prefix=".cover-", suffix=".png", dir=directory)
        os.close(descriptor)
        pixbuf.savev(temporary, "png", [], [])
        os.replace(temporary, target)
        return str(target)
    except (OSError, GLib.Error, ValueError):
        return None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
