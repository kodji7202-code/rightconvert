"""Standalone executable entry used by PyInstaller on Windows."""

from rightconvert.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
