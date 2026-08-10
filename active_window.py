import ctypes
from ctypes import wintypes

import psutil

user32 = ctypes.windll.user32


def get_active_process():
    """(process_name, exe_path) of the focused window; either may be None."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None, None
    try:
        process = psutil.Process(pid.value)
        try:
            path = process.exe()
        except (psutil.AccessDenied, OSError):
            path = None
        return process.name(), path
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return None, None
