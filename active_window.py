import ctypes
import os
from ctypes import wintypes

import psutil

user32 = ctypes.windll.user32
_OWN_PID = os.getpid()

# (hwnd, pid) -> (process_name, exe_path) for the last foreground window.
# The tracking loop asks every second, and the answer almost never changes
# between two seconds, so psutil (which opens the process each time) only
# runs when focus actually moves to a different window.
_last_key = None
_last_result = (None, None)


def get_active_process():
    """(process_name, exe_path) of the focused window; either may be None."""
    global _last_key, _last_result

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    # Our own window. The ignore list catches ScreenTimer.exe, but run from
    # source the process is pythonw.exe, which can't be ignored by name.
    if not pid.value or pid.value == _OWN_PID:
        return None, None

    key = (hwnd, pid.value)
    if key == _last_key:
        return _last_result

    try:
        process = psutil.Process(pid.value)
        try:
            path = process.exe()
        except (psutil.AccessDenied, OSError):
            path = None
        result = (process.name(), path)
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        # Not cached: a process that was mid-launch or mid-exit may answer
        # properly on the next tick.
        return None, None

    _last_key, _last_result = key, result
    return result
