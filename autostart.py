"""Start-on-login, via the per-user Run key.

HKCU\\...\\Run rather than a scheduled task or the machine-wide HKLM Run key:
it needs no admin rights, affects only this user, and a user who wants it gone
can see and delete it from Task Manager's Startup tab. A tracker that only runs
when the user remembers to open it undercounts most of the day, so this is
worth having — but it stays opt-in and defaults to off, because an app that
silently installs itself into startup is exactly the behaviour that makes
unsigned indie tools feel untrustworthy.
"""

import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ScreenTimer"
# Marks a login launch, which starts in the tray. A manual launch has no flag
# and shows its window — an app that appears to do nothing when double-clicked
# reads as broken. Lives here rather than in main so autostart, which main
# already imports, doesn't have to import main back.
STARTUP_FLAG = "--startup"


def _launch_command():
    """The command Windows should run at login.

    Frozen: the exe path, quoted. Running from source there is no single
    launchable file, so python.exe is invoked against main.py — usable for
    testing the toggle, though a real install is expected to be the exe.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {STARTUP_FLAG}'
    import os

    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return f'"{sys.executable}" "{main_py}" {STARTUP_FLAG}'


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return bool(value)


def set_enabled(enabled):
    """Returns the state actually achieved, so a failed registry write shows
    up in the UI as the toggle staying put rather than as a silent lie."""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        return is_enabled()
    return enabled


def refresh_path_if_enabled():
    """Rewrites the stored command if it's gone stale — otherwise moving or
    updating the exe leaves a Run entry pointing at a file that isn't there,
    and login silently starts nothing."""
    if not is_enabled():
        return
    current = _launch_command()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            stored, _ = winreg.QueryValueEx(key, VALUE_NAME)
        if stored != current:
            set_enabled(True)
    except OSError:
        pass
