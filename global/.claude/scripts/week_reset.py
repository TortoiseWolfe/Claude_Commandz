#!/usr/bin/env python3
"""week_reset.py: when the weekly token reset is, and when the week began (one small module, no other script needed).
Use when: a script needs the weekly reset or the start of the token week without importing the 4,000-line dispatcher.

The real reset is one fixed instant, Wednesday 16:00 UTC (12:00 PM Eastern in summer time, 11:00 AM in winter). When the
quality report names its own reset, that one wins. tests/test_week_reset.py also checks that these figures still equal the
dispatcher's, which imports this module (issue 32: one owner for the weekly reset).

  week_reset.py [--now EPOCH] [--reset EPOCH]      prints the week's start and reset as Eastern 12-hour times
"""
import argparse
import datetime
import sys
from zoneinfo import ZoneInfo

WEEK_TZ = "America/New_York"
WEEK_RESET_UTC = (2, 16, 0)  # weekday (Mon=0), hour, minute in UTC


def eastern(ts):
    return datetime.datetime.fromtimestamp(ts, ZoneInfo(WEEK_TZ))


def next_reset(t):
    """The next Wednesday 16:00 UTC after the aware datetime `t`, in `t`'s own zone."""
    u = t.astimezone(datetime.timezone.utc)
    d = u.date() + datetime.timedelta(days=(WEEK_RESET_UTC[0] - u.weekday()) % 7)
    r = datetime.datetime(d.year, d.month, d.day, WEEK_RESET_UTC[1], WEEK_RESET_UTC[2], tzinfo=datetime.timezone.utc)
    if r <= u:
        r += datetime.timedelta(days=7)
    return r.astimezone(t.tzinfo)


def week_bounds(now, reset=None):
    """(start, reset) as Eastern datetimes of the week `now` (epoch seconds) is in. `reset` (epoch) is the quality report's own
    reset and is used only when it is still ahead; otherwise the fixed rule applies."""
    t = eastern(now)
    if isinstance(reset, (int, float)) and reset > now:
        r = eastern(reset)
        return r - datetime.timedelta(days=7), r  # wall-clock: a change of clocks in the week moves nothing
    r = next_reset(t)
    return (r.astimezone(datetime.timezone.utc) - datetime.timedelta(days=7)).astimezone(r.tzinfo), r  # the same UTC instant a week earlier


def week_start_ts(now, reset=None):
    """When the token week began, as epoch seconds."""
    return week_bounds(now, reset)[0].timestamp()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", type=float, default=None)
    ap.add_argument("--reset", type=float, default=None)
    a = ap.parse_args(argv)
    now = a.now if a.now is not None else datetime.datetime.now().timestamp()
    s, r = week_bounds(now, a.reset)
    print("Week began %s; resets %s" % (s.strftime("%a %-I:%M %p"), r.strftime("%a %-I:%M %p")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
