import json
import os
import shutil
import time
import re
import calendar
from datetime import date, datetime, timedelta

from paths import user_data_path

DATA_FILE = user_data_path("data.json")
SETTINGS_FILE = user_data_path("settings.json")
PATHS_FILE = user_data_path("app_paths.json")

# How far ahead of the system clock a recorded day may be before it is treated
# as a bad date rather than as evidence the clock is wrong. See today_str().
MAX_CLOCK_SLIP_DAYS = 2

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
    """Writes to a temp file, forces it to disk, then renames over the target.

    os.replace is atomic, but that alone only guarantees the *rename* is
    all-or-nothing — not that the bytes ever reached the platter. Without the
    fsync below, NTFS can commit the rename and the new file's length while
    the contents are still in the write-back cache, so an unclean shutdown
    leaves a file of exactly the right size full of zeros. That is not
    hypothetical: it happened here on 2026-08-19, and the history came back
    as 3379 NUL bytes.

    The previous copy is kept alongside as .bak before the rename, so a file
    that does come back unreadable has somewhere to be recovered from instead
    of the app starting over from an empty history.
    """
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    if os.path.exists(path):
        try:
            shutil.copy2(path, f"{path}.bak")
        except OSError:
            pass  # a missing backup must never stop the actual write

    _replace_with_retry(tmp_path, path)


# Long enough to outlast an antivirus or indexer holding the file open for a
# moment, short enough that the tracking loop's own 10-second flush interval
# still governs. Seen in the wild as PermissionError/WinError 5 out of
# os.replace, which stalled writes for over an hour before it cleared.
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SECONDS = 0.2


def _replace_with_retry(src, dst):
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SECONDS * (attempt + 1))


def _quarantine_corrupt_file(path):
    """Renames a corrupt store aside so a fresh one can take its place.

    Never deletes: the file is the user's history, and a copy that cannot be
    parsed automatically may still be readable by hand. Best-effort, since
    failing to rename must not stop the app from carrying on.
    """
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        os.replace(path, f"{path}.corrupt-{stamp}")
    except OSError:
        pass


def _recover_from_backup(path):
    """Last copy that was known to parse, or {} if there isn't one.

    Quarantining a corrupt file used to mean starting from nothing, which on
    2026-08-19 turned one unclean shutdown into ten days of history apparently
    vanishing — the file was zeroed by the crash, and the app dutifully began
    a fresh one. The .bak written before every replace is at most one save
    behind (ten seconds of tracking), so recovering from it costs almost
    nothing and saves everything.

    Returns {} rather than raising if the backup is missing or is itself
    unreadable: by this point the primary is already gone, and refusing to
    start would leave the user with an app that will not track at all.
    """
    backup = f"{path}.bak"
    try:
        with open(backup, "r") as f:
            recovered = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    # Put it back as the live file, so the next write extends the recovered
    # history rather than overwriting it with today alone.
    try:
        _atomic_write_json(path, recovered)
    except OSError:
        pass  # still return it: tracking can continue in memory either way
    return recovered


class StoreUnreadable(Exception):
    """A stored file exists but could not be read this time.

    Deliberately distinct from "this file is empty or absent". Collapsing the
    two is what made this dangerous: every store here is read, modified, then
    written back, so a caller that treats a temporary read failure as an empty
    file will write back a file containing only the newest entry, destroying
    everything already in it. A file locked for a moment at boot, which
    antivirus and Smart App Control both do, was enough to trigger it.

    Read-only callers still get a safe empty default; only the read-modify-
    write paths ask for the exception, and they retry rather than overwrite.
    """


# path -> ((mtime_ns, size), parsed). The tracking loop reads settings every
# second and a single UI refresh used to parse data.json half a dozen times;
# all of that now costs one os.stat unless the file actually changed.
_read_cache = {}


def _read_json_cached(path):
    """Parsed contents of `path`, re-read only when its mtime or size changes.

    The returned object is shared between callers, so it must be treated as
    read-only. Writers never use this: they read fresh, so an unsaved
    in-memory change can never leak into the cache and get written twice.
    """
    st = os.stat(path)
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _read_cache.get(path)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    with open(path, "r") as f:
        value = json.load(f)
    _read_cache[path] = (stamp, value)
    return value


def load_data(default_on_error=True):
    """Reads the history file.

    default_on_error=True is for read-only callers, where showing an empty
    day briefly is harmless and better than an error. Those get the cached,
    shared copy and must not modify it. Anything that writes back must pass
    False, which reads fresh from disk and raises on failure instead of
    silently looking like an empty history.
    """
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        if default_on_error:
            return _read_json_cached(DATA_FILE)
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        # Locked, busy, permission denied: transient by nature, so the caller
        # should back off and retry rather than act on a wrong answer.
        if default_on_error:
            return {}
        raise StoreUnreadable(str(exc)) from exc
    except json.JSONDecodeError:
        # Corrupt is a different problem entirely: unlike a locked file it
        # will never become readable on its own, so refusing to write would
        # block tracking permanently instead of for a few seconds. Move the
        # bad file aside and start a fresh one. Renamed rather than deleted
        # so the damaged history is still there to inspect or salvage.
        _quarantine_corrupt_file(DATA_FILE)
        return _recover_from_backup(DATA_FILE)


def save_data(data):
    _atomic_write_json(DATA_FILE, data)


def _normalize_day(day):
    """Old format stored a plain int of seconds; new format is {total, apps}.
    A day with no entry yet arrives as {} (from data.get(key, {})) — dict, but
    missing both keys, so it needs the same backfill as the old-int case.

    Builds a new dict rather than filling in the one passed in: that one is
    usually the shared cached copy, which readers must not modify."""
    if isinstance(day, dict):
        return {"total": day.get("total", 0), "apps": day.get("apps", {})}
    return {"total": day if isinstance(day, int) else 0, "apps": {}}


def add_active_seconds(process_seconds):
    """process_seconds: dict of process_name -> seconds to add for today.

    Raises StoreUnreadable rather than writing if the existing history could
    not be read. The caller retries on the next flush with its pending
    seconds still accumulated, so nothing is lost by refusing: the write is
    merely deferred until the file is readable again. Writing regardless
    would replace the whole history with today alone.
    """
    today = today_str()
    data = load_data(default_on_error=False)
    day = _normalize_day(data.get(today, {}))
    total = day["total"]
    apps = dict(day["apps"])

    for process_name, seconds in process_seconds.items():
        total += seconds
        apps[process_name] = apps.get(process_name, 0) + seconds

    data[today] = {"total": total, "apps": apps}
    save_data(data)


def today_str():
    """The current date, guarded against a real clock quirk seen on at least
    one machine this ran on: this app's autostart fires right at boot, and on
    that machine the system clock reliably read several hours behind reality
    for a moment right then, every single morning, before an RTC sync
    corrected it seconds later (confirmed via Windows' own System event log,
    which recorded the exact jump each time). Caught in that window,
    date.today() would report a day that has already been fully recorded as
    "today", and anything tracked in that moment would file itself under an
    already-completed day.

    A real clock only ever moves forward, so if the computed date is earlier
    than the most recent day already on record, that is proof of a bad
    reading rather than proof time went backwards. Falling back to that
    latest recorded day keeps tracking pointed at the right place; the
    problem is self-correcting; the next call after the RTC sync lands will
    see the true date again on its own.
    """
    computed = str(date.today())
    try:
        existing = load_data()
    except Exception:
        return computed
    if not existing:
        return computed

    latest = max(existing)
    if computed >= latest:
        return computed

    # Only tolerate a small discrepancy. The boot glitch this defends against
    # is a few hours, so a stored day more than a couple of days ahead is not
    # a clock blip, it is a bad date that got written (a clock can jump
    # forward too). Trusting it unconditionally would be a trap with no way
    # out: one bogus future entry would pin every later reading to that date
    # permanently, even once the clock was correct again.
    try:
        gap = (date.fromisoformat(latest) - date.fromisoformat(computed)).days
    except ValueError:
        return computed
    return latest if gap <= MAX_CLOCK_SLIP_DAYS else computed


def get_today_total_seconds():
    return _normalize_day(load_data().get(today_str(), {}))["total"]


def get_today_apps():
    return _normalize_day(load_data().get(today_str(), {}))["apps"]


def load_app_paths(default_on_error=True):
    """Cached and shared when default_on_error=True, so read-only there."""
    if not os.path.exists(PATHS_FILE):
        return {}
    try:
        if default_on_error:
            return _read_json_cached(PATHS_FILE)
        with open(PATHS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        if default_on_error:
            return {}
        raise StoreUnreadable(str(exc)) from exc


def remember_app_paths(new_paths):
    """Merges process_name -> exe_path entries, writing only when something changed.

    Entries are refreshed, not just added. Recording a path once and never
    revisiting it looks harmless until an app updates: Store apps and Electron
    apps both carry their version in the install path, so
    Claude_1.26832.0.0_x64 becomes Claude_1.32885.1.0_x64 overnight and the
    stored path points at a folder that no longer exists. Icon extraction then
    fails forever and the app quietly falls back to a letter tile, with nothing
    to suggest the path is simply stale. Observed paths come from the running
    process, so the newest one seen is always the correct one.
    """
    # This runs on every 10-second flush and almost always has nothing new;
    # checking the cached copy first skips the fresh read in that common case.
    cached = load_app_paths()
    if all(cached.get(name) == path for name, path in new_paths.items()):
        return
    known = load_app_paths(default_on_error=False)
    changed = {name: path for name, path in new_paths.items() if known.get(name) != path}
    if not changed:
        return
    known.update(changed)
    _atomic_write_json(PATHS_FILE, known)


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
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def load_settings(default_on_error=True):
    """default_on_error=False for callers that write settings back, so a
    failed read cannot silently reset the user's goal, break interval and
    per-app limits to defaults."""
    if not os.path.exists(SETTINGS_FILE):
        return dict(DEFAULT_SETTINGS)
    try:
        if default_on_error:
            settings = _read_json_cached(SETTINGS_FILE)
        else:
            with open(SETTINGS_FILE, "r") as f:
                settings = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(settings)
        return merged
    except FileNotFoundError:
        return dict(DEFAULT_SETTINGS)
    except (json.JSONDecodeError, OSError) as exc:
        if default_on_error:
            return dict(DEFAULT_SETTINGS)
        raise StoreUnreadable(str(exc)) from exc


def save_settings(settings):
    _atomic_write_json(SETTINGS_FILE, settings)


def set_app_limit(process_name, minutes):
    """minutes=None clears the limit for that app."""
    settings = load_settings(default_on_error=False)
    limits = dict(settings.get("app_limits", {}))
    if minutes is None:
        limits.pop(process_name, None)
    else:
        limits[process_name] = minutes
    settings["app_limits"] = limits
    save_settings(settings)


def earliest_recorded_date():
    """The oldest date key in the store, or None if it's empty.

    Bounds the "previous" arrow: without it there is no natural floor, and a
    new user could arrow back into years of empty periods.
    """
    data = load_data()
    if not data:
        return None
    return min(data)


def month_bounds(anchor):
    """(first, last) day of the calendar month containing `anchor`."""
    first = anchor.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def days_between(first, last):
    """One entry per calendar day from `first` to `last` inclusive, oldest
    first — always the full range, never truncated at today.

    Truncating the current month at today once meant August showed 31 cells
    and September 19, with nothing to explain why the grid changed shape. A
    real calendar shows the whole range and leaves the future blank; isFuture
    marks those days so the UI never has to work out "today" itself.
    """
    data = load_data()
    today = date.fromisoformat(today_str())
    days = []
    d = first
    while d <= last:
        key = str(d)
        seconds = _normalize_day(data.get(key, {}))["total"]
        days.append(
            {
                "date": key,
                "dayNum": d.day,
                "weekday": d.strftime("%a"),
                "weekdayIndex": d.weekday(),  # 0 = Monday
                "seconds": seconds,
                "label": format_hms(seconds),
                "isToday": d == today,
                "isFuture": d > today,
            }
        )
        d += timedelta(days=1)
    return days


def apps_between(first, last):
    """process_name -> seconds, summed over every recorded day in range."""
    lo, hi = str(first), str(last)
    totals = {}
    for key, day in load_data().items():
        if lo <= key <= hi:  # ISO dates sort the same as the dates they name
            for name, secs in _normalize_day(day)["apps"].items():
                totals[name] = totals.get(name, 0) + secs
    return totals


def recorded_days():
    """(date_str, total_seconds) for every day with anything recorded,
    oldest first."""
    rows = ((key, _normalize_day(day)["total"]) for key, day in load_data().items())
    return sorted(row for row in rows if row[1] > 0)
