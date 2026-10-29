"""Terminal entry point: python3 -m music_player."""

import sys


def main():
    try:
        from .app import MusicPlayerApplication

        app = MusicPlayerApplication()
        result = app.run(sys.argv)
        return 1 if app.startup_failed else result
    except (ImportError, ValueError, RuntimeError) as exc:
        print(f"MusicPlayer could not start: {exc}", file=sys.stderr)
        print("Check the Fedora runtime dependencies listed in README.md.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
