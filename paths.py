"""Resolves two different kinds of paths that PyInstaller's one-file mode
splits apart: bundled read-only resources (ui/, assets/) vs. user-writable
data (data.json, settings.json, the lock file). In a one-file build,
__file__ resolves under sys._MEIPASS, a fresh temp folder extracted on
every launch — fine for bundled resources, but writable files put there
would silently reset (history, settings, single-instance lock) every run.
"""

import os
import sys

APP_NAME = "ScreenTimer"


def resource_path(*parts):
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def user_data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def user_data_path(*parts):
    return os.path.join(user_data_dir(), *parts)
