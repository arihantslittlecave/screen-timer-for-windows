"""One-time setup on the very first launch.

The app is a background tracker: if it isn't running it isn't recording, and a
tracker the user has to remember to start produces numbers that are quietly
wrong. So the first launch turns start-on-login on and pins the tray icon,
rather than leaving both behind a settings screen nobody opens.

It runs exactly once, gated on settings["first_run_done"]. That gate is the
whole point: without it, a user who deliberately turns start-on-login off would
find it back on at the next launch, which is the behaviour people rightly
resent in background apps.
"""

import threading
import time
import winreg

import autostart
import storage

NOTIFY_ICON_KEY = r"Control Panel\NotifyIconSettings"
_PIN_ATTEMPTS = 10
_PIN_INTERVAL_SECONDS = 1.0


def _pin_tray_icon(exe_path):
    """Sets IsPromoted on our own tray entry so the icon sits in the taskbar
    corner instead of Windows 11's hidden overflow flyout.

    Returns True once the entry exists and has been set. Windows only creates
    the entry after the icon is first added, which races with startup — hence
    the retry loop in _pin_tray_icon_when_ready.
    """
    needle = exe_path.lower()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY_ICON_KEY) as root:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, index)
                except OSError:
                    return False
                index += 1
                with winreg.OpenKey(root, sub) as entry:
                    try:
                        path, _ = winreg.QueryValueEx(entry, "ExecutablePath")
                    except FileNotFoundError:
                        continue
                if str(path).lower() != needle:
                    continue
                full = f"{NOTIFY_ICON_KEY}\\{sub}"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, full, 0, winreg.KEY_SET_VALUE) as w:
                    winreg.SetValueEx(w, "IsPromoted", 0, winreg.REG_DWORD, 1)
                return True
    except OSError:
        return False


def _pin_tray_icon_when_ready(exe_path):
    for _ in range(_PIN_ATTEMPTS):
        if _pin_tray_icon(exe_path):
            return
        time.sleep(_PIN_INTERVAL_SECONDS)


def run(exe_path):
    """Best-effort: a failure here must never stop the app from starting."""
    try:
        settings = storage.load_settings()
        if settings.get("first_run_done"):
            return
        autostart.set_enabled(True)
        settings["first_run_done"] = True
        storage.save_settings(settings)
        # Off-thread: the tray entry doesn't exist until pystray has added the
        # icon, and blocking startup on a registry poll would delay the window.
        threading.Thread(
            target=_pin_tray_icon_when_ready, args=(exe_path,), daemon=True
        ).start()
    except Exception:
        pass
