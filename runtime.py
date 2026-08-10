"""State shared between the tracking thread and the UI.

The tracking loop writes these; the API reads them to render the break
countdown. Kept in its own module so neither side has to import the other.
"""

active_since_break = 0


def add_active_seconds(seconds):
    global active_since_break
    active_since_break += seconds


def reset_break():
    global active_since_break
    active_since_break = 0


def snooze(seconds_from_now, break_interval_seconds):
    """Schedules the next break `seconds_from_now`, regardless of elapsed time."""
    global active_since_break
    active_since_break = break_interval_seconds - seconds_from_now


def seconds_until_break(break_interval_seconds):
    return max(break_interval_seconds - active_since_break, 0)
