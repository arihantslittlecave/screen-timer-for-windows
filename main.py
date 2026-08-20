import os
import sys

from paths import user_data_path


def _boot_trace(label):
    """Writes one line with nothing but stdlib, before anything that could
    plausibly hang has been imported yet.

    Every log line elsewhere in this app goes through api.log_info, which
    needs win32api, winrt and webview already imported successfully. That is
    fine once startup is past this point, but it means an import itself
    hanging, which is exactly the kind of thing antivirus or Smart App
    Control scanning a freshly-launched exe at boot can cause, would leave
    nothing in the log at all: the code that would explain the silence is
    the same code that never got to run. This writes straight to disk with
    only `os`, bracketing each import below so a boot that goes silent still
    leaves a trail of exactly how far it got.
    """
    try:
        path = user_data_path("boot-trace.log")
        with open(path, "a", encoding="utf8") as f:
            import time as _t

            f.write(f"{_t.strftime('%Y-%m-%dT%H:%M:%S')}  pid={os.getpid()}  {label}\n")
    except OSError:
        pass


_boot_trace("process started, importing stdlib")

import ctypes
import threading
import time
import traceback
import winreg
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

_boot_trace("stdlib done, importing psutil/pystray")

import psutil
import pystray

_boot_trace("psutil/pystray done, importing pywin32")

import win32api
import win32con
import win32event
import win32gui
import winerror

_boot_trace("pywin32 done, importing winrt (toast notifications)")

from winrt.windows.data.xml.dom import XmlDocument
from winrt.windows.ui.notifications import ToastNotification, ToastNotificationManager

_boot_trace("winrt done, importing webview (WebView2 bridge)")

import webview

_boot_trace("webview done, importing this app's own modules")

import autostart
import first_run
import icon_art
import runtime
import storage
from active_window import get_active_process
from api import Api, log_info
from idle import get_idle_seconds
from paths import resource_path

_boot_trace("all imports done, entering main()")

IDLE_THRESHOLD_SECONDS = 60
TICK_INTERVAL_SECONDS = 1
SAVE_INTERVAL_SECONDS = 10
# How long writes may keep failing before the app says so out loud. Long
# enough that a brief lock at boot passes unremarked, short enough that a
# genuinely stuck app is reported while the user is still at the machine.
STALL_ALERT_SECONDS = 120
TRAY_ICON_SIZE = 24  # under icon_art.TRACK_MIN_SIZE, so pystray gets the
# already-simplified arc-only variant directly rather than a detailed 64px
# image that Windows would then have to shrink itself for the actual tray slot
APP_NAME = "Screen Timer for Windows"
# The window title doubles as the handle _focus_existing_instance() looks up
# with FindWindow, so both sides must read it from here.
WINDOW_TITLE = APP_NAME
SINGLE_INSTANCE_MUTEX_NAME = "ScreenTimer.SingleInstanceMutex"

# Notifications go through the real WinRT ToastNotificationManager, not
# plyer — plyer's Windows backend uses the legacy Shell_NotifyIconW balloon
# API, which Windows auto-upgrades into a toast but does NOT resolve the
# AppUserModelId's registered DisplayName/icon, so it showed the raw AUMID
# string (or "Python" with no AUMID set at all) instead of the app name.
# create_toast_notifier_with_id(app_id) does the proper registry lookup —
# confirmed by testing both paths.
#
# This is a hand-rolled ~10-line call rather than the win11toast library:
# win11toast declares OCR/speech-synthesis/media-playback/imaging as
# dependencies for features we never use, pulling in ~4.2MB of unused
# compiled WinRT bindings. Importing just these two winrt submodules gets
# the same result from ~1.5MB of actually-used binaries.
APP_USER_MODEL_ID = "ScreenTimer.DesktopApp"
_icon_path = None  # the .ico on disk, shared by the toast and the window icon
_icon_uri = None  # set once in _register_app_identity(); regenerating the
# multi-size .ico on every single notification would be wasteful

_TOAST_XML = """<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{title}</text>
      <text>{message}</text>
      <image placement="appLogoOverride" hint-crop="circle" src="{icon}"/>
    </binding>
  </visual>
</toast>"""


_XML_ATTR_ENTITIES = {'"': "&quot;", "'": "&apos;"}


def _notify(title, message):
    # title/message can contain a dynamic app name (from friendly_app_name),
    # which could theoretically include &, <, > — escape before embedding in
    # XML so a stray character doesn't silently break the whole toast. icon
    # sits inside a quoted attribute, not text content, so it also needs the
    # quote escaped (plain escape() only handles &, <, >).
    doc = XmlDocument()
    doc.load_xml(_TOAST_XML.format(
        title=xml_escape(title),
        message=xml_escape(message),
        icon=xml_escape(_icon_uri, _XML_ATTR_ENTITIES),
    ))
    notifier = ToastNotificationManager.create_toast_notifier_with_id(APP_USER_MODEL_ID)
    notifier.show(ToastNotification(doc))


icon = None
window = None
_limit_notified_day = None
_limit_notified_apps = set()
_daily_limit_notified_day = None
_last_tick_error_at = 0.0


def _focus_existing_instance():
    """A second instance shouldn't run its own tracking loop — two loops
    read-modify-write the same data.json independently and can double-count
    or clobber each other's updates. Bring the running window forward instead."""
    hwnd = win32gui.FindWindow(None, WINDOW_TITLE)
    if not hwnd:
        return
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    # SetForegroundWindow is blocked unless it looks like a real user input
    # event just happened — a throwaway Alt keypress satisfies that check.
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    win32gui.SetForegroundWindow(hwnd)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)


_instance_mutex = None  # module-level so the handle survives for the process's
# life; a mutex releases the instant its last handle closes, so letting this
# get garbage collected would silently drop the lock while still running


def _acquire_single_instance_lock():
    """Returns True if this process now owns the lock.

    A named Windows mutex, not a PID written to a file. The previous version
    compared a stored PID against psutil.pid_exists(), which has a real gap:
    Windows recycles PIDs, so a lock file left by a long-dead process can
    coincidentally match a PID now in use by something else and look "alive"
    forever, or the reverse — a genuinely running instance's lock can be
    misread as stale and a second full instance is allowed to start. That
    second case happened in practice: two trackers ended up running at once,
    each independently read-modify-writing data.json on its own timer, racing
    on the same file with no coordination between them.

    A mutex has no such gap. The OS releases it the instant the owning
    process exits for any reason, including a crash or a forced kill, so
    there is never stale state on disk to reconcile and never a window for a
    coincidental PID match. CreateMutex is also atomic: "does this exist" and
    "claim it" happen as one OS call, not two Python statements a second
    process could interleave with.
    """
    global _instance_mutex
    _instance_mutex = win32event.CreateMutex(None, False, SINGLE_INSTANCE_MUTEX_NAME)
    return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS


def _register_app_identity():
    """Claims our own AUMID and points it at our name/icon in the registry,
    so Windows attributes notifications to the app name instead of
    falling back to python.exe's identity. Must run before any window is
    created — SetCurrentProcessExplicitAppUserModelID only works pre-window
    and only once per process."""
    global _icon_uri, _icon_path

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)

    icon_path = icon_art.write_ico()
    _icon_path = icon_path
    _icon_uri = "file:///" + Path(icon_path).resolve().as_posix()

    key_path = f"Software\\Classes\\AppUserModelId\\{APP_USER_MODEL_ID}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_path)


# wParam values for WM_SETICON. Not in win32con, so named here rather than
# left as bare 0 and 1 at the call site.
_ICON_SMALL = 0
_ICON_BIG = 1


def _apply_window_icon():
    """Gives the window its own icon, which is what the taskbar and Alt-Tab show.

    Frozen, the exe's embedded icon covers this for nothing. Run from source
    the host process is pythonw.exe, so Windows falls back to Python's icon and
    the app sits in the taskbar looking like something else entirely.
    pywebview's start(icon=...) does not reach the Windows backend, so the icon
    is set on the window handle directly.

    Polls because create_window() returns before the native window exists, and
    gives up quietly: a missing icon is a cosmetic problem and must never be
    the reason startup fails.
    """
    if not _icon_path or not os.path.exists(_icon_path):
        return

    hwnd = None
    deadline = time.time() + 15
    while time.time() < deadline:
        hwnd = win32gui.FindWindow(None, WINDOW_TITLE)
        if hwnd:
            break
        time.sleep(0.25)
    if not hwnd:
        return

    try:
        for size, which in ((16, _ICON_SMALL), (32, _ICON_BIG)):
            handle = win32gui.LoadImage(
                0, _icon_path, win32con.IMAGE_ICON, size, size, win32con.LR_LOADFROMFILE
            )
            win32gui.SendMessage(hwnd, win32con.WM_SETICON, which, handle)
        log_info("window-icon", "set from " + _icon_path)
    except Exception:
        log_info("window-icon-failed", traceback.format_exc())


def on_open(icon_obj, item):
    window.show()


def on_quit(icon_obj, item):
    icon_obj.stop()
    window.destroy()
    # No lock file to clean up: the mutex releases itself when this process
    # exits, which is the whole point of using one.


def build_menu():
    today_seconds = storage.get_today_total_seconds()
    label = f"Today: {storage.format_hms(today_seconds)}"
    return pystray.Menu(
        pystray.MenuItem(label, None, enabled=False),
        pystray.MenuItem("Open", on_open, default=True),
        pystray.MenuItem("Quit", on_quit),
    )


def backfill_app_paths():
    """Fills in exe paths for already-tracked apps that happen to be running now,
    so their icons appear immediately instead of only after the next focus."""
    missing = set(storage.get_today_apps()) - set(storage.load_app_paths())
    if not missing:
        return

    found = {}
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            name = proc.info["name"]
            if name in missing and proc.info["exe"]:
                found[name] = proc.info["exe"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    storage.remember_app_paths(found)


def check_app_limits(just_updated):
    """Notifies once per app per day when its usage crosses its configured
    limit. `just_updated` restricts the scan to apps active this flush,
    since a limit only matters while you're actually in that app."""
    global _limit_notified_day, _limit_notified_apps

    limits = storage.load_settings().get("app_limits", {})
    if not limits:
        return

    today = storage.today_str()
    if today != _limit_notified_day:
        _limit_notified_apps = set()
        _limit_notified_day = today

    today_apps = storage.get_today_apps()
    for process_name in just_updated:
        limit_minutes = limits.get(process_name)
        if not limit_minutes or process_name in _limit_notified_apps:
            continue
        if today_apps.get(process_name, 0) >= limit_minutes * 60:
            _limit_notified_apps.add(process_name)
            _notify(
                "App limit reached",
                f"{storage.friendly_app_name(process_name)} has hit your {limit_minutes}-minute limit for today.",
            )


def check_daily_limit():
    """Notifies once per day when today's total crosses the daily limit —
    the same treatment check_app_limits gives individual apps."""
    global _daily_limit_notified_day

    goal_hours = storage.load_settings().get("daily_goal_hours", 0)
    if not goal_hours:
        return

    today = storage.today_str()
    if _daily_limit_notified_day == today:
        return

    goal_seconds = int(goal_hours * 3600)
    if storage.get_today_total_seconds() >= goal_seconds:
        _daily_limit_notified_day = today
        _notify(
            "Daily limit reached",
            f"You've hit your {storage.format_hms(goal_seconds)} daily limit for today.",
        )


def tracking_loop():
    pending = {}
    pending_paths = {}
    since_save = 0
    last_saved_at = time.time()
    stall_reported = False

    while True:
        time.sleep(TICK_INTERVAL_SECONDS)

        # Deferring a write is normal and self-healing: a file locked for a
        # few seconds at boot gets retried on the next flush. Deferring for
        # minutes is not, and is exactly the state this app used to sit in
        # while looking perfectly healthy — tray icon fine, window fine,
        # counting nothing, and no clue on disk as to why. Say so, once,
        # rather than let it pass for normal.
        if pending and time.time() - last_saved_at > STALL_ALERT_SECONDS:
            if not stall_reported:
                stall_reported = True
                held = sum(pending.values())
                log_info(
                    "stalled",
                    f"no successful save for "
                    f"{int(time.time() - last_saved_at)}s, holding {held}s of "
                    f"unwritten time. the data folder is most likely locked.",
                )
                try:
                    _notify(
                        "Screen Timer can't save right now",
                        "Something is blocking its data folder. Time is still "
                        "being counted and will be written once that clears.",
                    )
                except Exception:
                    pass

        # A single bad tick (a psutil hiccup, a locked settings file, a
        # notification backend failure) must never kill this thread — it's
        # daemon and has no supervisor, so a silent death here means tracking
        # stops forever while the tray icon and window look perfectly normal.
        try:
            if get_idle_seconds() < IDLE_THRESHOLD_SECONDS:
                runtime.add_active_seconds(TICK_INTERVAL_SECONDS)
                process_name, exe_path = get_active_process()
                if process_name and not storage.is_ignored_process(process_name):
                    pending[process_name] = pending.get(process_name, 0) + TICK_INTERVAL_SECONDS
                    if exe_path:
                        pending_paths[process_name] = exe_path

            since_save += TICK_INTERVAL_SECONDS
            if since_save >= SAVE_INTERVAL_SECONDS:
                since_save = 0
                if pending:
                    # If this write throws, pending is left intact and the
                    # next flush retries with the seconds still accumulated,
                    # so a file that is briefly unreadable defers the write
                    # instead of losing time or overwriting history.
                    flushed = pending
                    storage.add_active_seconds(flushed)

                    # Cleared the instant the write succeeds. Anything below
                    # that throws must not leave these seconds pending, or
                    # the next flush would add them to the total a second
                    # time on top of the copy already written.
                    pending = {}

                    if stall_reported:
                        log_info(
                            "recovered",
                            f"writing again after "
                            f"{int(time.time() - last_saved_at)}s stalled, "
                            f"nothing lost.",
                        )
                    last_saved_at = time.time()
                    stall_reported = False

                    try:
                        storage.remember_app_paths(pending_paths)
                        pending_paths = {}
                    except Exception:
                        pass  # icon paths are cosmetic, retry on the next flush
                    try:
                        check_app_limits(flushed.keys())
                        check_daily_limit()
                    except Exception:
                        pass  # notifications are best-effort, never block on them
                else:
                    # Nothing pending is not a failure to write, it's an idle
                    # machine with nothing to write yet. Leaving the clock
                    # running through an idle stretch meant the first second
                    # of activity after a two-minute break arrived already
                    # "overdue" and fired the stall warning instantly —
                    # observed twice in one evening, both times holding a
                    # single second. The alert has to measure writes that are
                    # failing, not time spent with nothing to say.
                    last_saved_at = time.time()
                if icon:
                    icon.menu = build_menu()

            break_interval_seconds = storage.load_settings()["break_interval_minutes"] * 60
            if runtime.active_since_break >= break_interval_seconds:
                runtime.reset_break()
                _notify(
                    "Time for a break",
                    f"You've been active for {break_interval_seconds // 60} minutes. Stretch, look away, hydrate.",
                )
        except Exception:
            # Swallowing this silently meant a fault that recurs every tick
            # stopped tracking forever while the window and tray icon carried
            # on looking perfectly healthy, with nothing on disk to say why.
            # Logged once, then at most every 10 minutes, so a persistent
            # fault is visible without filling the log with a line a second.
            global _last_tick_error_at
            now = time.time()
            if now - _last_tick_error_at > 600:
                _last_tick_error_at = now
                log_info("tracking-error", traceback.format_exc())


def main():
    global icon, window

    # These three lines bracket the one stretch of startup that had no
    # instrumentation at all, and a launch was once seen to wedge inside
    # exactly that gap: boot-trace recorded "entering main()", and then
    # nothing was ever written again — not the startup line below, not a
    # crash, nothing. Both calls here can plausibly block rather than fail
    # (a mutex handle, and a foreground-window handoff that waits on another
    # process's message loop), and a call that blocks leaves no trace at all
    # unless something was written before it. boot-trace is used rather than
    # log_info because log_info is itself downstream of a file open that
    # could be the thing hanging.
    _boot_trace("main(): claiming single-instance lock")
    if not _acquire_single_instance_lock():
        _boot_trace("main(): another instance holds the lock, focusing it")
        _focus_existing_instance()
        _boot_trace("main(): focused existing instance, exiting")
        sys.exit(0)
    _boot_trace("main(): lock acquired, writing first log line")

    # Logged here rather than at the end of startup. An instance once came up
    # after a reboot, took the lock, and then wedged before it reached the old
    # log call, so the log held nothing at all for that launch and there was no
    # way to tell how far it had got. Writing this first means the next launch
    # is on record even if everything after it hangs.
    start_hidden = autostart.STARTUP_FLAG in sys.argv[1:]
    log_info("startup", f"frozen={getattr(sys, 'frozen', False)} hidden={start_hidden}")
    _boot_trace("main(): first log line written, startup proceeding")

    _register_app_identity()
    first_run.run(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    autostart.refresh_path_if_enabled()
    # psutil has to open every process to read its exe path, which right after
    # a cold boot can be slow while the disk and antivirus are busy. Bracketed
    # by log lines so a stall here is obvious in the log instead of looking
    # like the app never started.
    log_info("startup", "scanning running apps for icons")
    backfill_app_paths()
    log_info("startup", "app scan done, creating window")

    # start_hidden is read at the top of main(), before the startup log:
    # launched by Windows at login this comes up quietly in the tray, launched
    # by hand it must show its window, since double-clicking an app and getting
    # no visible response reads as "it didn't work". Windows 11 also files new
    # tray icons into the overflow flyout where they go unnoticed.
    ui_path = resource_path("ui", "index.html")
    window = webview.create_window(
        WINDOW_TITLE,
        ui_path,
        js_api=Api(),
        width=370,
        height=700,
        min_size=(320, 480),
        hidden=start_hidden,
        resizable=True,
    )

    def on_closing():
        window.hide()
        return False

    window.events.closing += on_closing

    icon = pystray.Icon(
        "screen-timer", icon_art.make_badge(TRAY_ICON_SIZE), APP_NAME, build_menu()
    )

    def run_tray():
        # A bare thread target that raises dies silently: the tray icon never
        # appears and nothing anywhere says why. This is the app's only handle
        # once the window is hidden, so a failure here has to be recorded.
        try:
            log_info("tray", "starting pystray icon")
            icon.run()
            log_info("tray", "pystray icon.run() returned")
        except Exception:
            log_info("tray-failed", traceback.format_exc())

    threading.Thread(target=tracking_loop, daemon=True).start()
    threading.Thread(target=run_tray, daemon=True).start()
    threading.Thread(target=_apply_window_icon, daemon=True).start()

    # The last line before the UI takes over the main thread. A log that ends
    # at an earlier checkpoint says the app hung during startup and points at
    # which step; a log with this line says startup completed and anything
    # wrong afterwards is the running app, not its launch.
    log_info(
        "ready",
        f"tracking started, ui_path={ui_path} ui_exists={os.path.exists(ui_path)}",
    )
    webview.start(icon=icon_art.write_ico())


if __name__ == "__main__":
    main()
