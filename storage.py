import json
import os
import re
from datetime import date, timedelta

from paths import user_data_path

DATA_FILE = user_data_path("data.json")
SETTINGS_FILE = user_data_path("settings.json")
PATHS_FILE = user_data_path("app_paths.json")

DEFAULT_SETTINGS = {
    "break_interval_minutes": 30,
    "daily_goal_hours": 6,
    "app_limits": {},  # process_name -> minutes
    # Flipped true the first time first-run setup runs. Its only job is to
    # distinguish "never configured" from "configured, and the user chose off",
    # so turning start-on-login off actually sticks instead of being switched
    # back on at the next launch.
    "first_run_done": False,
}

# Windows shell surfaces that own the foreground window without being an app
# the user chose to use — the lock screen, the Start menu, search, the snip
# overlay, the IME candidate window. Left in, they crowd the top-apps list with
# things nobody considers "screen time".
#
# Skipped at tracking time rather than only hidden at render time, because the
# day total is the sum of per-app seconds: filtering at render would leave a
# total that no longer matches the rows explaining it. The time lost is a few
# seconds per Start-menu open, and lock-screen seconds shouldn't have counted
# at all. Matched case-insensitively.
IGNORED_PROCESSES = frozenset(
    {
        "lockapp.exe",
        "shellexperiencehost.exe",
        "startmenuexperiencehost.exe",
        "searchhost.exe",
        "searchapp.exe",
        "shellhost.exe",
        "screenclippinghost.exe",
        "textinputhost.exe",
        "applicationframehost.exe",
        # Checking your screen time is not screen time worth reporting, and
        # counting it puts this app in its own top-apps list.
        "screentimer.exe",
    }
)


def is_ignored_process(process_name):
    return bool(process_name) and process_name.lower() in IGNORED_PROCESSES


def _atomic_write_json(path, data):
    """Writes to a temp file then renames over the target. os.replace is
    atomic on Windows/NTFS, so a crash or power loss mid-write leaves either
    the old file or the new one intact — never a truncated, corrupted one."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_data(data):
    _atomic_write_json(DATA_FILE, data)


def _normalize_day(day):
    """Old format stored a plain int of seconds; new format is {total, apps}.
    A day with no entry yet arrives as {} (from data.get(key, {})) — dict, but
    missing both keys, so it needs the same backfill as the old-int case."""
    if isinstance(day, dict):
        day.setdefault("total", 0)
        day.setdefault("apps", {})
        return day
    return {"total": day if isinstance(day, int) else 0, "apps": {}}


def add_active_seconds(process_seconds):
    """process_seconds: dict of process_name -> seconds to add for today."""
    today = str(date.today())
    data = load_data()
    day = _normalize_day(data.get(today, {}))

    for process_name, seconds in process_seconds.items():
        day["total"] = day.get("total", 0) + seconds
        day["apps"][process_name] = day["apps"].get(process_name, 0) + seconds

    data[today] = day
    save_data(data)


def today_str():
    return str(date.today())


def get_day_total_seconds(day_str=None):
    day = _normalize_day(load_data().get(day_str or today_str(), {}))
    return day.get("total", 0)


def get_day_apps(day_str=None):
    day = _normalize_day(load_data().get(day_str or today_str(), {}))
    return day.get("apps", {})


def get_today_total_seconds():
    return get_day_total_seconds()


def get_today_apps():
    return get_day_apps()


def load_app_paths():
    if not os.path.exists(PATHS_FILE):
        return {}
    try:
        with open(PATHS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def remember_app_paths(new_paths):
    """Merges process_name -> exe_path entries, writing only when something changed."""
    known = load_app_paths()
    additions = {name: path for name, path in new_paths.items() if name not in known}
    if not additions:
        return
    known.update(additions)
    _atomic_write_json(PATHS_FILE, known)


def get_last_n_days(n):
    """Returns list of (date_str, total_seconds) for the last n days, oldest first."""
    data = load_data()
    result = []
    for i in range(n - 1, -1, -1):
        d = str(date.today() - timedelta(days=i))
        day = _normalize_day(data.get(d, {}))
        result.append((d, day.get("total", 0)))
    return result


def previous_day_str(day_str):
    return str(date.fromisoformat(day_str) - timedelta(days=1))


def weekday_label(day_str):
    return date.fromisoformat(day_str).strftime("%a")


def friendly_day_label(day_str):
    delta_days = (date.today() - date.fromisoformat(day_str)).days
    if delta_days == 0:
        return "Today"
    if delta_days == 1:
        return "Yesterday"
    return date.fromisoformat(day_str).strftime("%a %d %b")


def friendly_app_name(process_name):
    name = process_name[:-4] if process_name.lower().endswith(".exe") else process_name
    if not name:
        return name
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", name)
    return spaced[:1].upper() + spaced[1:]


def format_hms(total_seconds):
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r") as f:
            settings = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(settings)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings):
    _atomic_write_json(SETTINGS_FILE, settings)


def set_app_limit(process_name, minutes):
    """minutes=None clears the limit for that app."""
    settings = load_settings()
    limits = dict(settings.get("app_limits", {}))
    if minutes is None:
        limits.pop(process_name, None)
    else:
        limits[process_name] = minutes
    settings["app_limits"] = limits
    save_settings(settings)
