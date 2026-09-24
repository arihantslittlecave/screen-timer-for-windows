import functools
import os
import tempfile
import threading
import traceback
from datetime import date, datetime, timedelta

import win32gui

import autostart
import icons
import runtime
import storage
from paths import user_data_path

MIN_APP_SECONDS = 60
MAX_APPS = 30
SNOOZE_OPTIONS = (5, 10, 15, 30)
PERIOD_KINDS = ("day", "week", "month", "all")

_log_lock = threading.Lock()


def _compare(current, previous, against):
    """How `current` stacks up against `previous`, or None when there is
    nothing to compare with (a first day, a first week)."""
    if not previous:
        return None
    difference = current - previous
    if abs(difference) < 60:
        return {"direction": "same", "label": "", "against": against}
    return {
        "direction": "up" if difference > 0 else "down",
        "label": storage.format_hms(abs(difference)),
        "against": against,
    }


def _short_date(d, today):
    """"Tue 16 Sep", with the year only when it isn't this year."""
    text = f"{d:%a} {d.day} {d:%b}"
    return text if d.year == today.year else f"{text} {d.year}"


def _period_bounds(kind, anchor):
    """(first, last) dates of the day/week/month containing `anchor`.
    Weeks run Monday to Sunday."""
    if kind == "week":
        first = anchor - timedelta(days=anchor.weekday())
        return first, first + timedelta(days=6)
    if kind == "month":
        return storage.month_bounds(anchor)
    return anchor, anchor


def _period_title(kind, first, last, today):
    if kind == "day":
        if first == today:
            return "Today"
        if first == today - timedelta(days=1):
            return "Yesterday"
        return _short_date(first, today)
    if kind == "week":
        this_monday = today - timedelta(days=today.weekday())
        if first == this_monday:
            return "This week"
        if first == this_monday - timedelta(days=7):
            return "Last week"
        if first.month == last.month:
            text = f"{first.day} – {last.day} {last:%b}"
        else:
            text = f"{first.day} {first:%b} – {last.day} {last:%b}"
        return text if last.year == today.year else f"{text} {last.year}"
    return f"{first:%B %Y}"


def _against(kind, is_current, prev_first, today):
    if kind == "day":
        # A named date, not "the day before": looking at 16 Sep, "the day
        # before" reads as though it means the day before today.
        return "yesterday" if is_current else _short_date(prev_first, today)
    return {
        "week": "last week" if is_current else "the week before",
        "month": "last month" if is_current else "the month before",
    }[kind]


def _window_visible():
    """Whether anyone can see the window right now. Unknown counts as
    visible, so a lookup failure can only ever cost a little extra work,
    never leave the UI frozen on stale numbers."""
    hwnd = runtime.window_hwnd
    if not hwnd:
        return True
    try:
        return bool(win32gui.IsWindowVisible(hwnd)) and not win32gui.IsIconic(hwnd)
    except Exception:
        return True


def log_info(label, text=""):
    """Normal-operation breadcrumb (startup, tray, first bridge call)."""
    log_exception(label, text)


def log_exception(label, text):
    """Appends to %APPDATA%\\ScreenTimer\\screen-timer.log.

    A packaged build has no console, so an exception crossing the JS bridge
    otherwise vanishes: the UI's refresh() catches it and the window just sits
    there blank. A file on disk is the only way a user can report what broke.

    Named for the app rather than for errors because it also carries ordinary
    startup breadcrumbs — a file called error.log sitting in a healthy install
    reads as something being wrong.
    """
    # The tray thread, the tracking thread and the UI bridge all log. Writing
    # the header and body as two calls let concurrent writers interleave
    # mid-line and shred each other's entries, which was actually observed.
    # One write, under one lock.
    entry = f"--- {datetime.now().isoformat(timespec='seconds')} {label}\n{text.rstrip()}\n"
    with _log_lock:
        if _append(user_data_path("screen-timer.log"), entry):
            return
        # The primary log lives in the same folder as the data files, so
        # whatever stops one write tends to stop them all: a folder locked by
        # antivirus at boot silences the log and freezes tracking together,
        # and the silence then hides its own cause. This second location is
        # somewhere else entirely, so a lock on the app's own folder cannot
        # suppress the record of that lock.
        _append(_fallback_log_path(), f"[primary log unwritable]\n{entry}")


def _append(path, text):
    """Returns True if the line landed. Never raises: logging must not be the
    thing that breaks the app."""
    try:
        with open(path, "a", encoding="utf8") as f:
            f.write(text)
        return True
    except OSError:
        return False


def _fallback_log_path():
    return os.path.join(tempfile.gettempdir(), "ScreenTimer-fallback.log")


def _logged(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            log_exception(fn.__name__, traceback.format_exc())
            raise

    return wrapper


class Api:
    _logged_first_call = False

    def log_error(self, message):
        """Called from the UI's global error handler."""
        log_exception("javascript", str(message))
        return True

    def _apps_payload(self, seconds_by_name, app_limits=None):
        """{"apps": top rows, "appCount": how many there are in total}, so the
        UI can say "top 30" rather than "all 30" when the list is capped."""
        rows, count = self._app_rows(seconds_by_name, app_limits)
        return {"apps": rows, "appCount": count}

    def _app_rows(self, seconds_by_name, app_limits=None):
        """One ranked app list for every tab — same filtering (short and
        system entries dropped), same sort, same shape, so the tabs cannot
        drift into disagreeing about what is conceptually the same list.

        Icons are not included: they are ~3KB each and never change, so the
        UI asks for them once via get_icons() instead of receiving the same
        images again on every refresh.

        app_limits are daily limits, so they are only passed for a single
        day; set against a week's total they would read as broken.
        """
        per_process = app_limits is not None
        app_limits = app_limits or {}
        significant = {
            n: s
            for n, s in seconds_by_name.items()
            if s >= MIN_APP_SECONDS and not storage.is_ignored_process(n)
        }
        # Two processes of one app (python.exe and pythonw.exe, an app that
        # renamed its .exe in an update) would otherwise be two rows with the
        # same name. Merged, except on a single day, where limits are set per
        # process and a merged row couldn't say which one it meant.
        merged = {}
        for process_name, seconds in sorted(significant.items(), key=lambda kv: kv[1], reverse=True):
            name = storage.friendly_app_name(process_name)
            key = process_name if per_process else name
            if key in merged:
                merged[key]["seconds"] += seconds
            else:
                merged[key] = {"name": name, "processName": process_name, "seconds": seconds}
        ranked = sorted(merged.values(), key=lambda row: row["seconds"], reverse=True)
        rows = [
            {
                **row,
                "label": storage.format_hms(row["seconds"]),
                "limitMinutes": app_limits.get(row["processName"]),
                "limitExceeded": bool(app_limits.get(row["processName"]))
                and row["seconds"] >= app_limits[row["processName"]] * 60,
            }
            for row in ranked[:MAX_APPS]
        ]
        return rows, len(ranked)

    @_logged
    def is_visible(self):
        """The only call the UI makes while the window is hidden in the tray,
        so that time costs next to nothing."""
        return _window_visible()

    @_logged
    def get_state(self):
        """The live, settings-shaped bits that aren't tied to whichever
        period is on screen: break countdown, limit, start-on-login."""
        # One breadcrumb on the first successful bridge call. Without it there
        # is no way to tell "the UI never asked" apart from "the UI asked and
        # the answer was wrong" — they look identical from a blank window.
        if not Api._logged_first_call:
            Api._logged_first_call = True
            log_info("bridge-ok", "first get_state call reached Python")
        settings = storage.load_settings()
        break_interval_seconds = settings["break_interval_minutes"] * 60
        break_in = runtime.seconds_until_break(break_interval_seconds)

        return {
            "visible": _window_visible(),
            "goalHours": settings["daily_goal_hours"],
            "goalSeconds": int(settings["daily_goal_hours"] * 3600),
            "startOnLogin": autostart.is_enabled(),
            "breakMinutes": settings["break_interval_minutes"],
            "breakInLabel": storage.format_hms(break_in) if break_in >= 60 else "under a minute",
            "snoozeMinutes": settings["snooze_minutes"],
        }

    @_logged
    def get_period(self, kind="day", anchor=None):
        """Everything one tab shows. `kind` is day, week, month or all;
        `anchor` is any ISO date inside the wanted period (None = today).
        prevAnchor/nextAnchor are ready-made anchors for the arrows, None
        where there is nothing to go to."""
        if kind not in PERIOD_KINDS:
            kind = "day"
        today = date.fromisoformat(storage.today_str())
        anchor = min(date.fromisoformat(anchor), today) if anchor else today
        earliest_str = storage.earliest_recorded_date()
        earliest = date.fromisoformat(earliest_str) if earliest_str else today
        settings = storage.load_settings()

        if kind == "all":
            return self._all_time(today, earliest)

        first, last = _period_bounds(kind, anchor)
        is_current = first <= today <= last
        days = storage.days_between(first, last)
        total = sum(d["seconds"] for d in days)
        active_days = sum(1 for d in days if d["seconds"] > 0)
        avg = total // active_days if active_days else 0

        prev_anchor = first - timedelta(days=1)
        has_prev = prev_anchor >= earliest
        compare = None
        previous_label = None
        if has_prev:
            prev_days = storage.days_between(*_period_bounds(kind, prev_anchor))
            prev_total = sum(d["seconds"] for d in prev_days)
            if kind == "day" and is_current:
                # Today is still running, so "3h less than yesterday" would be
                # true every morning and mean nothing. Yesterday's total is
                # given as plain context instead.
                previous_label = storage.format_hms(prev_total) if prev_total else None
            elif kind == "day":
                compare = _compare(total, prev_total, _against(kind, is_current, prev_anchor, today))
            else:
                # Averages, not totals: a week that started on Monday would
                # otherwise always look far lighter than last week's seven
                # full days.
                prev_active = sum(1 for d in prev_days if d["seconds"] > 0)
                prev_avg = prev_total // prev_active if prev_active else 0
                prev_first = _period_bounds(kind, prev_anchor)[0]
                compare = _compare(avg, prev_avg, _against(kind, is_current, prev_first, today))

        apps = storage.apps_between(first, last)
        if kind == "day":
            # The Day tab shows the week around the day, so there's always
            # context for "is this a lot?" and a one-tap way to the next day.
            week_first, week_last = _period_bounds("week", anchor)
            days = storage.days_between(week_first, week_last)
            week_title = _period_title("week", week_first, week_last, today)
        return {
            "kind": kind,
            "anchor": str(anchor),
            "title": _period_title(kind, first, last, today),
            "isCurrent": is_current,
            "prevAnchor": str(prev_anchor) if has_prev else None,
            "nextAnchor": str(last + timedelta(days=1)) if last < today else None,
            "totalSeconds": total,
            "totalLabel": storage.format_hms(total),
            "avgLabel": storage.format_hms(avg),
            "activeDays": active_days,
            "compare": compare,
            "previousLabel": previous_label,
            "days": days,
            "weekTitle": week_title if kind == "day" else None,
            **self._apps_payload(apps, settings.get("app_limits", {}) if kind == "day" else None),
        }

    def _all_time(self, today, earliest):
        recorded = storage.recorded_days()
        total = sum(seconds for _, seconds in recorded)
        avg = total // len(recorded) if recorded else 0

        busiest = None
        if recorded:
            busiest_day, busiest_seconds = max(recorded, key=lambda row: row[1])
            busiest = {
                "date": busiest_day,
                "title": _short_date(date.fromisoformat(busiest_day), today),
                "label": storage.format_hms(busiest_seconds),
            }

        by_month = {}
        for day_str, seconds in recorded:
            by_month[day_str[:7]] = by_month.get(day_str[:7], 0) + seconds
        months = [
            {
                "anchor": f"{key}-01",
                "title": f"{date.fromisoformat(key + '-01'):%b %Y}",
                "seconds": seconds,
                "label": storage.format_hms(seconds),
            }
            for key, seconds in sorted(by_month.items(), reverse=True)
        ]

        return {
            "kind": "all",
            "anchor": str(today),
            # Not "All time": the tab right above already says that.
            "title": f"Since {earliest.day} {earliest:%b %Y}" if recorded else "All time",
            "isCurrent": True,
            "prevAnchor": None,
            "nextAnchor": None,
            "totalSeconds": total,
            "totalLabel": storage.format_hms(total),
            "avgLabel": storage.format_hms(avg),
            "activeDays": len(recorded),
            "busiest": busiest,
            "months": months,
            "compare": None,
            "days": [],
            **self._apps_payload(storage.apps_between(earliest, today)),
        }

    @_logged
    def get_icons(self, process_names):
        """processName -> PNG data URI (or None). Asked once per app per
        session; see _app_rows for why icons travel separately."""
        app_paths = storage.load_app_paths()
        return {
            name: icons.get_icon_data_uri(app_paths[name]) if name in app_paths else None
            for name in process_names or []
        }

    @_logged
    def save_settings(self, break_minutes, goal_hours):
        # Merge onto the existing settings — overwriting wholesale would
        # silently drop app_limits every time break/goal are changed.
        # Strict read: if the file can't be read right now, fail the save
        # rather than merge onto defaults and write the user's real goal,
        # break interval and app limits away.
        settings = storage.load_settings(default_on_error=False)
        settings["break_interval_minutes"] = int(break_minutes)
        settings["daily_goal_hours"] = float(goal_hours)
        storage.save_settings(settings)
        return True

    @_logged
    def set_snooze_minutes(self, minutes):
        minutes = int(minutes)
        if minutes not in SNOOZE_OPTIONS:
            return False
        settings = storage.load_settings(default_on_error=False)
        settings["snooze_minutes"] = minutes
        storage.save_settings(settings)
        return True

    @_logged
    def set_app_limit(self, process_name, minutes):
        minutes = int(minutes) if minutes not in (None, "", 0, "0") else None
        storage.set_app_limit(process_name, minutes)
        return True

    @_logged
    def set_start_on_login(self, enabled):
        # Returns the achieved state, not the requested one — the UI reflects
        # what the registry actually holds.
        return autostart.set_enabled(bool(enabled))

    @_logged
    def snooze_break(self):
        settings = storage.load_settings()
        runtime.snooze(settings["snooze_minutes"] * 60, settings["break_interval_minutes"] * 60)
        return True
