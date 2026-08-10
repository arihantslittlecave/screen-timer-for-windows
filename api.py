import functools
import traceback
from datetime import datetime

import autostart
import icons
import runtime
import storage
from paths import user_data_path

MIN_APP_SECONDS = 60
MAX_TOP_APPS = 8
ALLOWED_RANGES = (7, 30)
SNOOZE_MINUTES = 5


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
    log_exception(label, text)


def log_exception(label, text):
    """Appends to %APPDATA%\\ScreenTimer\\error.log.

    A packaged build has no console, so an exception crossing the JS bridge
    otherwise vanishes: the UI's refresh() catches it and the window just sits
    there blank. A file on disk is the only way a user can report what broke.
    """
    try:
        with open(user_data_path("error.log"), "a", encoding="utf8") as f:
            f.write(f"--- {datetime.now().isoformat(timespec='seconds')} {label}\n")
            f.write(text.rstrip() + "\n")
    except OSError:
        pass  # logging must never be the thing that breaks the app


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

    @_logged
    def get_state(self, selected_day=None, history_days=7):
        # One breadcrumb on the first successful bridge call. Without it there
        # is no way to tell "the UI never asked" apart from "the UI asked and
        # the answer was wrong" — they look identical from a blank window.
        if not Api._logged_first_call:
            Api._logged_first_call = True
            log_info("bridge-ok", "first get_state call reached Python")
        settings = storage.load_settings()
        today = storage.today_str()
        day = selected_day or today
        history_days = history_days if history_days in ALLOWED_RANGES else 7

        total = storage.get_day_total_seconds(day)
        apps = storage.get_day_apps(day)
        # Tracking skips these going forward, but days recorded before that
        # still hold them, so they're filtered here too.
        significant = {
            n: s
            for n, s in apps.items()
            if s >= MIN_APP_SECONDS and not storage.is_ignored_process(n)
        }
        top_apps = sorted(significant.items(), key=lambda kv: kv[1], reverse=True)[:MAX_TOP_APPS]
        app_paths = storage.load_app_paths()
        app_limits = settings.get("app_limits", {})

        goal_seconds = int(settings["daily_goal_hours"] * 3600)
        break_interval_seconds = settings["break_interval_minutes"] * 60
        break_in = runtime.seconds_until_break(break_interval_seconds)

        history = [
            {
                "date": d,
                "seconds": s,
                "label": storage.format_hms(s),
                "weekday": storage.weekday_label(d),
                "isToday": d == today,
                "isSelected": d == day,
            }
            for d, s in storage.get_last_n_days(history_days)
        ]

        return {
            "selectedDay": day,
            "isToday": day == today,
            "dayLabel": storage.friendly_day_label(day),
            "totalSeconds": total,
            "totalLabel": storage.format_hms(total),
            "goalHours": settings["daily_goal_hours"],
            "goalSeconds": goal_seconds,
            "goalExceeded": goal_seconds > 0 and total > goal_seconds,
            "delta": _format_delta(total, storage.get_day_total_seconds(storage.previous_day_str(day))),
            "startOnLogin": autostart.is_enabled(),
            "breakMinutes": settings["break_interval_minutes"],
            "breakInSeconds": break_in,
            "breakInLabel": storage.format_hms(break_in) if break_in >= 60 else "under a minute",
            "snoozeMinutes": SNOOZE_MINUTES,
            "historyDays": history_days,
            "history": history,
            "topApps": [
                {
                    "name": storage.friendly_app_name(name),
                    "processName": name,
                    "seconds": seconds,
                    "label": storage.format_hms(seconds),
                    "icon": icons.get_icon_data_uri(app_paths[name]) if name in app_paths else None,
                    "limitMinutes": app_limits.get(name),
                    "limitExceeded": bool(app_limits.get(name)) and seconds >= app_limits[name] * 60,
                }
                for name, seconds in top_apps
            ],
        }

    @_logged
    def save_settings(self, break_minutes, goal_hours):
        # Merge onto the existing settings — overwriting wholesale would
        # silently drop app_limits every time break/goal are changed.
        settings = storage.load_settings()
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
