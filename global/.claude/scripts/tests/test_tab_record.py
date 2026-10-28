"""tab_record.py: the pure helpers (names, job keys, blur boxes, the video window, frame timing, clock labels, the concat list).

Nothing here starts a browser, ffmpeg or node: those are proved by run_tab_record_smoke.sh against a live tab.
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tab_record as tr  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402


class Names(unittest.TestCase):
    def test_safe_name_keeps_letters_and_drops_the_rest(self):
        self.assertEqual(tr.safe_name("Audio Eye / job #1"), "Audio-Eye-job-1")

    def test_safe_name_never_empty_and_never_long(self):
        self.assertEqual(tr.safe_name(""), "tab")
        self.assertEqual(tr.safe_name("///"), "tab")
        self.assertEqual(len(tr.safe_name("a" * 200)), 60)

    def test_job_key_from_steps_file(self):
        self.assertEqual(tr.job_key_from_steps("aeye-p1-steps.json"), "aeye")
        self.assertEqual(tr.job_key_from_steps("/x/d6/rcat/rcat-p2-steps.json"), "rcat")
        self.assertEqual(tr.job_key_from_steps("graf-steps.json"), "graf")
        self.assertEqual(tr.job_key_from_steps(""), "")

    def test_job_key_from_flag_name(self):
        self.assertEqual(tr.job_key_from_flag("bitw-submitted"), "bitw")
        self.assertEqual(tr.job_key_from_flag("graf"), "graf")
        self.assertEqual(tr.job_key_from_flag("subs-submitted.flag"), "subs")

    def test_flag_prefixes_dedupe_and_skip_blanks(self):
        self.assertEqual(tr.flag_prefixes("graf", ["graf", " ", "x"]), ["graf", "x"])


class BlurBoxes(unittest.TestCase):
    def test_box_is_padded_scaled_and_clamped(self):
        (box,) = tr.merge_rects([{"x": 2, "y": 2, "w": 10, "h": 10}], 1.0, 100, 100, pad=6)
        self.assertEqual(box, (0, 0, 18, 18))

    def test_overlapping_boxes_become_one(self):
        rects = [{"x": 10, "y": 10, "w": 50, "h": 10}, {"x": 55, "y": 12, "w": 50, "h": 10}]
        self.assertEqual(len(tr.merge_rects(rects, 1.0, 400, 400)), 1)

    def test_far_apart_boxes_stay_two(self):
        rects = [{"x": 10, "y": 10, "w": 20, "h": 10}, {"x": 200, "y": 300, "w": 20, "h": 10}]
        self.assertEqual(len(tr.merge_rects(rects, 1.0, 400, 400)), 2)

    def test_box_outside_the_frame_is_dropped(self):
        self.assertEqual(tr.merge_rects([{"x": 500, "y": 500, "w": 10, "h": 10}], 1.0, 100, 100), [])

    def test_scale_applies(self):
        (box,) = tr.merge_rects([{"x": 100, "y": 100, "w": 100, "h": 50}], 0.5, 1000, 1000, pad=0)
        self.assertEqual(box, (50, 50, 100, 75))


class BlurScale(unittest.TestCase):
    def test_one_to_one_screen(self):
        self.assertEqual(tr.blur_scale(1280, 1280, 1), 1.0)

    def test_wide_screenshot_is_shrunk(self):
        self.assertEqual(tr.blur_scale(1280, 2560, 2), 1.0)  # 1280 CSS px at 2x: a CSS pixel is one video pixel

    def test_retina_screenshot_of_a_narrow_page(self):
        self.assertEqual(tr.blur_scale(1280, 2000, 2), 1.28)  # 1000 CSS px at 2x shown in 1280 px: 1.28 video px per CSS px

    def test_huge_unreadable_marker_covers_the_whole_frame(self):
        (box,) = tr.merge_rects([{"x": 0, "y": 0, "w": 100000, "h": 100000}], 1.0, 1280, 900)
        self.assertEqual(box, (0, 0, 1280, 900))


class Window(unittest.TestCase):
    def test_short_page_is_not_scrolled(self):
        self.assertEqual(tr.window_top(0, (0, 0, 10, 10), 700), 0)

    def test_change_inside_the_window_keeps_it_put(self):
        self.assertEqual(tr.window_top(100, (0, 200, 10, 300), 3000), 100)

    def test_change_outside_recentres_on_it(self):
        top = tr.window_top(0, (0, 1500, 10, 1600), 3000)
        self.assertEqual(top, 1550 - tr.H // 2)

    def test_never_runs_past_the_bottom(self):
        self.assertEqual(tr.window_top(0, (0, 2900, 10, 3000), 3000), 3000 - tr.H)

    def test_no_change_keeps_previous_top_within_bounds(self):
        self.assertEqual(tr.window_top(5000, None, 3000), 3000 - tr.H)


class Timing(unittest.TestCase):
    def test_gap_is_real_but_capped_and_last_frame_is_held(self):
        d = tr.durations([0, 1000, 20000])
        self.assertEqual(d, [1.0, tr.MAX_GAP_S, tr.TAIL_S])

    def test_a_very_short_gap_is_not_zero(self):
        self.assertEqual(tr.durations([0, 10, 20])[0], 0.1)

    def test_timeline_adds_up(self):
        rows = tr.timeline([1000, 2000, 5000], [1.0, 3.0, 5.0])
        self.assertEqual(rows, [(1000, 0.0), (2000, 1.0), (5000, 4.0)])

    def test_video_offset_is_the_frame_at_or_before_the_moment(self):
        rows = [(1000, 0.0), (2000, 1.0), (5000, 4.0)]
        self.assertEqual(tr.video_offset(rows, 500), 0.0)
        self.assertEqual(tr.video_offset(rows, 2500), 1.0)
        self.assertEqual(tr.video_offset(rows, 9000), 4.0)

    def test_wall_ms_reads_local_time_on_the_date(self):
        ms = tr.wall_ms("2026-10-28", "14:03:07")
        self.assertEqual(dt.datetime.fromtimestamp(ms / 1000), dt.datetime(2026, 10, 28, 14, 3, 7))
        self.assertEqual(dt.datetime.fromtimestamp(tr.wall_ms("2026-10-28", "14:03") / 1000).second, 0)

    def test_stamp_label_is_twelve_hour(self):
        ms = int(dt.datetime(2026, 10, 28, 14, 3, 7).timestamp() * 1000)
        self.assertEqual(tr.stamp_label(ms, "graf"), "Wed 28 Oct 2:03:07 PM  graf")


class ConcatList(unittest.TestCase):
    def test_last_file_repeats_and_quotes_are_escaped(self):
        txt = tr.concat_text(["/a/f1.png", "/a/it's.png"], [1.0, 2.5])
        self.assertEqual(
            txt.splitlines(),
            ["file '/a/f1.png'", "duration 1.000", "file '/a/it'\\''s.png'", "duration 2.500", "file '/a/it'\\''s.png'"],
        )

    def test_empty_list_is_blank(self):
        self.assertEqual(tr.concat_text([], []).strip(), "")


class Home(unittest.TestCase):
    def test_env_overrides_the_recordings_folder(self):
        d = fresh_dir(self)
        old = os.environ.get("TAB_RECORD_HOME")
        os.environ["TAB_RECORD_HOME"] = d
        try:
            self.assertEqual(str(tr.home()), d)
        finally:
            if old is None:
                del os.environ["TAB_RECORD_HOME"]
            else:
                os.environ["TAB_RECORD_HOME"] = old

    def test_pid_alive(self):
        self.assertTrue(tr.pid_alive(os.getpid()))
        self.assertFalse(tr.pid_alive("not-a-pid"))
        self.assertFalse(tr.pid_alive(None))


class Clips(unittest.TestCase):
    def _clip(self, blurred=True, approve=True, size=1000):
        d = fresh_dir(self)
        clip = os.path.join(d, "c.mp4")
        with open(clip, "wb") as f:
            f.write(b"x" * size)
        if blurred is not None:
            with open(clip + ".json", "w") as f:
                f.write('{"blurred": %s}' % ("true" if blurred else "false"))
        if approve:
            with open(clip + ".approved", "w") as f:
                f.write("{}")
            now = os.path.getmtime(clip) + 5
            os.utime(clip + ".approved", (now, now))
        return clip

    def test_bitrate_fits_the_limit(self):
        kb = tr.bitrate_kbps(9.5, 60)
        self.assertLess(kb * 1000 / 8 * 60, 9.5 * 1024 * 1024)
        self.assertGreater(kb, 1000)

    def test_bitrate_has_a_floor_and_survives_zero_length(self):
        self.assertEqual(tr.bitrate_kbps(1, 100000), 50)
        self.assertGreater(tr.bitrate_kbps(9.5, 0), 0)

    def test_covering_picks_recordings_that_span_the_moment(self):
        rows = [("a", 100, 200), ("b", 150, 400), ("c", 500, 600)]
        self.assertEqual([r[0] for r in tr.recordings_covering(rows, 160)], ["b", "a"])
        self.assertEqual(tr.recordings_covering(rows, 450), [])

    def test_an_approved_blurred_small_clip_may_leave(self):
        self.assertEqual(tr.clip_ok(self._clip()), (True, ""))

    def test_no_sidecar_means_not_made_by_cut(self):
        ok, why = tr.clip_ok(self._clip(blurred=None))
        self.assertFalse(ok)
        self.assertIn("sidecar", why)

    def test_a_sidecar_that_is_not_blurred_is_refused(self):
        self.assertFalse(tr.clip_ok(self._clip(blurred=False))[0])

    def test_unapproved_is_refused(self):
        ok, why = tr.clip_ok(self._clip(approve=False))
        self.assertFalse(ok)
        self.assertIn("not approved", why)

    def test_a_clip_changed_after_approval_is_refused(self):
        clip = self._clip()
        later = os.path.getmtime(clip + ".approved") + 10
        os.utime(clip, (later, later))
        ok, why = tr.clip_ok(clip)
        self.assertFalse(ok)
        self.assertIn("changed", why)

    def test_over_the_limit_is_refused_and_a_larger_limit_allows_it(self):
        clip = self._clip(size=2 * 1024 * 1024)
        self.assertFalse(tr.clip_ok(clip, 1.0)[0])
        self.assertTrue(tr.clip_ok(clip, 3.0)[0])

    def test_only_mp4(self):
        clip = self._clip()
        os.rename(clip, clip[:-4] + ".webm")
        self.assertFalse(tr.clip_ok(clip[:-4] + ".webm")[0])
        self.assertFalse(tr.clip_ok(clip + "-missing.mp4")[0])


if __name__ == "__main__":
    unittest.main()
