import ctypes
import os
import sys
import threading
import time
import traceback
import winreg
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import psutil
import pystray
import webview
import win32api
import win32con
import win32event
import win32gui
import winerror
from winrt.windows.data.xml.dom import XmlDocument
from winrt.windows.ui.notifications import ToastNotification, ToastNotificationManager

import autostart
import first_run
import icon_art
import runtime
import storage
from active_window import get_active_process
from api import Api, log_info
from idle import get_idle_seconds
from paths import resource_path

IDLE_THRESHOLD_SECONDS = 60
TICK_INTERVAL_SECONDS = 1
SAVE_INTERVAL_SECONDS = 10
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
    global _icon_uri

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)

    icon_path = icon_art.write_ico()
    _icon_uri = "file:///" + Path(icon_path).resolve().as_posix()

    key_path = f"Software\\Classes\\AppUserModelId\\{APP_USER_MODEL_ID}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_path)


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

    while True:
        time.sleep(TICK_INTERVAL_SECONDS)

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
                    # Only clear pending after a successful write — if the
                    # disk write throws, keep accumulating and retry next
                    # flush rather than silently dropping tracked time.
                    storage.add_active_seconds(pending)
                    storage.remember_app_paths(pending_paths)
                    try:
                        check_app_limits(pending.keys())
                        check_daily_limit()
                    except Exception:
                        pass  # notifications are best-effort, never block on them
                    pending = {}
                    pending_paths = {}
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

    if not _acquire_single_instance_lock():
        _focus_existing_instance()
        sys.exit(0)

    # Logged here rather than at the end of startup. An instance once came up
    # after a reboot, took the lock, and then wedged before it reached the old
    # log call, so the log held nothing at all for that launch and there was no
    # way to tell how far it had got. Writing this first means the next launch
    # is on record even if everything after it hangs.
    start_hidden = autostart.STARTUP_FLAG in sys.argv[1:]
    log_info("startup", f"frozen={getattr(sys, 'frozen', False)} hidden={start_hidden}")

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
