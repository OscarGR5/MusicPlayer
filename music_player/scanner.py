"""One cancellable worker scans files; callbacks are dispatched to the UI."""

import os
import logging
import threading
import time
from pathlib import Path

from .artwork import cache_artwork
from .library import LibraryDatabase
from .metadata import SUPPORTED_EXTENSIONS, read_metadata

logger = logging.getLogger(__name__)


def discover_audio(folder, on_error=None):
    """Do not follow directory symlinks; deduplicate canonical file paths."""
    seen = set()
    for directory, directories, filenames in os.walk(folder, followlinks=False, onerror=on_error):
        directories.sort()
        for filename in sorted(filenames):
            if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                path = (Path(directory) / filename).resolve()
                if path not in seen:
                    seen.add(path)
                    yield path
            except (OSError, RuntimeError) as exc:
                if on_error is not None:
                    on_error(exc)


def scan_library(database, cache, cancelled, on_batch, on_progress):
    """Commit batches before publishing them. Never purge an inaccessible root."""
    checked = updated = skipped = 0
    removed = []
    warnings = []
    batch = []
    last_update = time.monotonic()
    with LibraryDatabase(database) as db:
        existing = {song.path: song for song in db.songs(include_unavailable=True)}

        def flush():
            nonlocal last_update
            db.commit()
            if batch:
                on_batch(list(batch))
                batch.clear()
            on_progress(f"Scanning library: {checked} checked, {updated} updated, {skipped} skipped")
            last_update = time.monotonic()

        for folder in db.folders():
            if cancelled.is_set():
                break
            root = Path(folder["path"])
            errors = []
            if not root.is_dir():
                warnings.append(f"Folder unavailable: {root}")
                continue
            seen = set()
            for path in discover_audio(root, errors.append):
                if cancelled.is_set():
                    break
                seen.add(str(path))
                checked += 1
                metadata = None
                restore_id = None
                try:
                    stat = path.stat()
                    previous = existing.get(str(path))
                    unchanged = previous and previous.mtime_ns == stat.st_mtime_ns and previous.size == stat.st_size
                    cached = previous and (not previous.artwork_path or Path(previous.artwork_path).is_file())
                    if unchanged and cached and not previous.available:
                        restore_id = previous.id
                    if not (unchanged and cached):
                        metadata = read_metadata(path)
                        thumbnail = cache_artwork(metadata.artwork, cache)
                        # A file being rewritten during a scan is retried next time.
                        after = path.stat()
                        if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                            skipped += 1
                            continue
                except Exception as exc:
                    # A malformed or unreadable song must not abort the library scan.
                    logger.warning("Skipping audio file %s: %s", path, exc)
                    skipped += 1
                    metadata = None
                    restore_id = None
                if restore_id is not None:
                    song = db.restore(restore_id)
                    db.commit()
                    existing[str(path)] = song
                    batch.append(song)
                    updated += 1
                if metadata is not None:
                    # Database failures abort visibly rather than being counted as bad media.
                    song = db.upsert(folder["id"], path, metadata, stat, thumbnail)
                    # Never hold the write lock while parsing the next file.
                    # Listening checkpoints use another connection on the GTK thread.
                    db.commit()
                    existing[str(path)] = song
                    batch.append(song)
                    updated += 1
                if len(batch) >= 32 or time.monotonic() - last_update >= 0.15:
                    flush()
            if errors:
                warnings.append(f"Some files in {root} could not be read; existing entries were retained")
            elif not cancelled.is_set():
                missing = [song.id for song in existing.values()
                           if song.available and song.folder_id == folder["id"] and song.path not in seen]
                db.remove(missing)
                removed.extend(missing)
        flush()
    summary = f"Library refreshed: {checked} checked, {updated} updated, {skipped} skipped, {len(removed)} removed"
    if cancelled.is_set():
        summary = "Library scan cancelled"
    if warnings:
        summary += ". " + ". ".join(warnings)
    return removed, summary


class LibraryScanner:
    def __init__(self, database, cache, dispatch, on_batch, on_progress, on_finished):
        self.database = database
        self.cache = cache
        self.dispatch = dispatch
        self.on_batch = on_batch
        self.on_progress = on_progress
        self.on_finished = on_finished
        self.cancelled = threading.Event()
        self.running = False
        self.thread = None

    def start(self):
        # Called only from the GTK thread, including completion delivery.
        if self.running or self.cancelled.is_set():
            return False
        self.running = True
        self.thread = threading.Thread(target=self._run, name="music-library-scan", daemon=True)
        self.thread.start()
        return True

    def _run(self):
        try:
            removed, summary = scan_library(
                self.database, self.cache, self.cancelled,
                lambda songs: self.dispatch(self.on_batch, songs),
                lambda status: self.dispatch(self.on_progress, status),
            )
        except Exception as exc:
            removed, summary = [], f"Library scan failed: {exc}. Use Rescan Library to retry."
        self.dispatch(self._finished, removed, summary)

    def _finished(self, removed, summary):
        self.running = False
        self.on_finished(removed, summary)

    def close(self):
        self.cancelled.set()
