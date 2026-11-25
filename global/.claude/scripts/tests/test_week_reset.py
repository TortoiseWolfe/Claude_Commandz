"""week_reset.py: the weekly reset and the week's start, held equal to the dispatcher's own copy."""
import datetime
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import week_reset as wr  # noqa: E402

ET = ZoneInfo("America/New_York")


def ts(y, mo, d, h, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=ET).timestamp()


class WeekReset(unittest.TestCase):
    def test_rule_reset_is_wednesday_noon_in_summer_and_eleven_in_winter(self):
        s, r = wr.week_bounds(ts(2026, 10, 29, 8))
        self.assertEqual(s.strftime("%a %Y-%m-%d %H:%M"), "Wed 2026-10-28 12:00")
        self.assertEqual(r.strftime("%a %Y-%m-%d %H:%M"), "Wed 2026-11-04 11:00")
        # the week began at the same UTC instant seven days before, so a clock change between the two moves the wall clock
        self.assertEqual((r.timestamp() - s.timestamp()), 7 * 86400)

    def test_a_named_reset_still_ahead_wins(self):
        now = ts(2026, 10, 29, 8)
        reset = ts(2026, 11, 4, 12)
        s, r = wr.week_bounds(now, reset)
        self.assertEqual(r.timestamp(), reset)
        self.assertEqual(s.strftime("%a %H:%M"), "Wed 12:00")
        self.assertEqual(wr.week_start_ts(now, reset), s.timestamp())

    def test_a_named_reset_already_past_is_ignored(self):
        now = ts(2026, 11, 5, 9)
        self.assertEqual(wr.week_start_ts(now, ts(2026, 10, 28, 12)), ts(2026, 11, 4, 11))

    def test_the_start_never_passes_now_and_is_within_seven_days(self):
        for day in range(1, 29):
            for hour in (0, 11, 12, 17, 23):
                now = ts(2026, 11, day, hour)
                start = wr.week_start_ts(now)
                self.assertLessEqual(start, now)
                self.assertGreater(start + 7 * 86400, now)

    def test_equal_to_the_dispatchers_copy(self):
        import openclaw_dispatch as d
        self.assertEqual((wr.WEEK_TZ, wr.WEEK_RESET_UTC), (d.WEEK_TZ, d.WEEK_RESET_UTC))
        for day in range(1, 29):
            for hour in (0, 11, 12, 17, 23):
                now = ts(2026, 11, day, hour)
                for reset in (None, ts(2026, 11, day, hour) + 3 * 86400, now - 86400):
                    _, _, _, r = d._week_edges(now, reset)
                    s, r2 = wr.week_bounds(now, reset)
                    self.assertEqual(r2.timestamp(), r.timestamp(), (now, reset))
                    self.assertEqual(s.timestamp(), (r - datetime.timedelta(days=7)).timestamp() if reset and reset > now
                                     else (r.astimezone(datetime.timezone.utc) - datetime.timedelta(days=7)).timestamp(), (now, reset))


if __name__ == "__main__":
    unittest.main()
