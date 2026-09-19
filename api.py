import functools
import os
import tempfile
import threading
import traceback
from datetime import date, datetime

import autostart
import icons
import runtime
import storage
from paths import user_data_path

MIN_APP_SECONDS = 60
MAX_TOP_APPS = 8
SNOOZE_MINUTES = 5

_log_lock = threading.Lock()


def _format_delta(current, previous):
    if not previous:
        return None
    difference = current - previous
    if abs(difference) < 60:
        return {"direction": "same", "label": "about the same"}
    return {
        "direction": "up" if difference > 0 else "down",
        "label": storage.format_hms(abs(difference)),
    }


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

    def _top_apps(self, seconds_by_name, app_limits=None):
        """Shared by the daily, monthly and selected-day views — same
        filtering (ignore short/system entries), same sort, same shape, so
        the three call sites can't quietly drift into showing different
        things for what is conceptually the same list."""
        app_paths = storage.load_app_paths()
        app_limits = app_limits or {}
        significant = {
            n: s
            for n, s in seconds_by_name.items()
            if s >= MIN_APP_SECONDS and not storage.is_ignored_process(n)
        }
        ranked = sorted(significant.items(), key=lambda kv: kv[1], reverse=True)[:MAX_TOP_APPS]
        return [
            {
                "name": storage.friendly_app_name(name),
                "processName": name,
                "seconds": seconds,
                "label": storage.format_hms(seconds),
                "icon": icons.get_icon_data_uri(app_paths[name]) if name in app_paths else None,
                "limitMinutes": app_limits.get(name),
                "limitExceeded": bool(app_limits.get(name)) and seconds >= app_limits[name] * 60,
            }
            for name, seconds in ranked
        ]

    @_logged
    def get_state(self):
        """Today only: the live numbers that only mean something right now —
        current total, break countdown, snooze. Historical browsing lives in
        get_month_state, which has no notion of "next break" because a day
        that already happened has no next anything.
        """
        # One breadcrumb on the first successful bridge call. Without it there
        # is no way to tell "the UI never asked" apart from "the UI asked and
        # the answer was wrong" — they look identical from a blank window.
        if not Api._logged_first_call:
            Api._logged_first_call = True
            log_info("bridge-ok", "first get_state call reached Python")
        settings = storage.load_settings()
        today = storage.today_str()

        total = storage.get_today_total_seconds()
        apps = storage.get_today_apps()
        app_limits = settings.get("app_limits", {})

        goal_seconds = int(settings["daily_goal_hours"] * 3600)
        break_interval_seconds = settings["break_interval_minutes"] * 60
        break_in = runtime.seconds_until_break(break_interval_seconds)

        year, month = int(today[:4]), int(today[5:7])
        active_days = sum(1 for d in storage.get_month_days(year, month) if d["seconds"] > 0)
        month_total = sum(storage.get_month_apps(year, month).values())
        avg_seconds = month_total // active_days if active_days else 0

        return {
            "dayLabel": storage.friendly_day_label(today),
            "totalSeconds": total,
            "totalLabel": storage.format_hms(total),
            "goalHours": settings["daily_goal_hours"],
            "goalSeconds": goal_seconds,
            "goalExceeded": goal_seconds > 0 and total > goal_seconds,
            "delta": _format_delta(total, storage.get_day_total_seconds(storage.previous_day_str(today))),
            "avgSeconds": avg_seconds,
            "avgLabel": storage.format_hms(avg_seconds),
            "startOnLogin": autostart.is_enabled(),
            "breakMinutes": settings["break_interval_minutes"],
            "breakInSeconds": break_in,
            "breakInLabel": storage.format_hms(break_in) if break_in >= 60 else "under a minute",
            "snoozeMinutes": SNOOZE_MINUTES,
            "topApps": self._top_apps(apps, app_limits),
        }

    @_logged
    def get_month_state(self, year=None, month=None, selected_day=None):
        """The History section: a full calendar month, real dates, navigable
        by arrow rather than a fixed last-N-days window — the fix for not
        being able to see anything before whatever the window happened to
        cover."""
        today_date = date.today()
        year = int(year) if year else today_date.year
        month = int(month) if month else today_date.month

        days = storage.get_month_days(year, month)
        total = sum(d["seconds"] for d in days)
        active = [d for d in days if d["seconds"] > 0]
        avg_seconds = total // len(active) if active else 0
        busiest = max(active, key=lambda d: d["seconds"]) if active else None

        settings = storage.load_settings()
        app_limits = settings.get("app_limits", {})
        month_apps = storage.get_month_apps(year, month)

        selected = None
        if selected_day:
            selected = {
                "date": selected_day,
                "label": storage.friendly_day_label(selected_day),
                "totalSeconds": storage.get_day_total_seconds(selected_day),
                "totalLabel": storage.format_hms(storage.get_day_total_seconds(selected_day)),
                "topApps": self._top_apps(storage.get_day_apps(selected_day), app_limits),
            }

        return {
            "year": year,
            "month": month,
            "monthLabel": storage.month_label(year, month),
            "canGoPrev": storage.can_go_prev_month(year, month),
            "canGoNext": storage.can_go_next_month(year, month),
            "totalSeconds": total,
            "totalLabel": storage.format_hms(total),
            "avgSeconds": avg_seconds,
            "avgLabel": storage.format_hms(avg_seconds),
            "activeDays": len(active),
            "busiestDay": (
                {
                    "date": busiest["date"],
                    "dayNum": busiest["dayNum"],
                    "seconds": busiest["seconds"],
                    "label": busiest["label"],
                }
                if busiest
                else None
            ),
            "days": days,
            "selectedDay": selected_day,
            "selected": selected,
            "topApps": self._top_apps(month_apps),
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
        break_interval_seconds = storage.load_settings()["break_interval_minutes"] * 60
        runtime.snooze(SNOOZE_MINUTES * 60, break_interval_seconds)
        return True
