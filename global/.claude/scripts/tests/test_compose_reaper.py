"""compose_reaper.py (#107, part 2): on 24 Oct 2026 a dead lane's dev server ran 16 hours in a worktree and grew to 12 GB.
A compose project whose working folder is a worktree nobody is in and nobody has touched for hours is stopped (never its volumes);
a running container with no memory limit over 4 GB is only reported. A dry run, the default, changes nothing."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import compose_reaper as cr  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

NOW = 2_000_000.0
HOUR = 3600


class Reaper(unittest.TestCase):
    def setUp(self):
        self.root = fresh_dir(self)
        self.wt = os.path.join(self.root, "repos", ".worktrees")
        os.makedirs(self.wt)
        self.downs = []

    def worktree(self, name, age_h):
        d = os.path.join(self.wt, name)
        os.makedirs(d)
        f = os.path.join(d, "app.ts")
        open(f, "w").write("x")
        os.utime(f, (NOW - age_h * HOUR, NOW - age_h * HOUR))
        os.utime(d, (NOW - age_h * HOUR, NOW - age_h * HOUR))
        return d

    def run_reaper(self, containers, panes=(), apply=False, stats=()):
        return cr.reap(containers=lambda: list(containers), panes=lambda: list(panes), stats=lambda: list(stats), now=NOW, worktrees_root=self.wt,
                       idle_hours=3, apply=apply, down=lambda project: self.downs.append(project) or True)

    def test_an_idle_worktree_with_no_pane_in_it_is_reaped(self):
        d = self.worktree("app--old", age_h=16)
        out = self.run_reaper([("c1", "app-old", d)], apply=True)
        self.assertEqual(self.downs, ["app-old"])
        self.assertIn("app-old", out["reaped"][0])

    def test_a_dry_run_names_it_and_stops_nothing(self):
        d = self.worktree("app--old", age_h=16)
        out = self.run_reaper([("c1", "app-old", d)])
        self.assertEqual(self.downs, [])
        self.assertEqual(len(out["would_reap"]), 1)
        self.assertEqual(out["reaped"], [])

    def test_a_pane_inside_the_worktree_keeps_it(self):
        d = self.worktree("app--live", age_h=16)
        out = self.run_reaper([("c1", "app-live", d)], panes=[os.path.join(d, "sub")], apply=True)
        self.assertEqual(self.downs, [])
        self.assertEqual(out["kept"][0][1], "a session is working in it")

    def test_a_recently_touched_worktree_keeps_it(self):
        d = self.worktree("app--busy", age_h=1)
        self.run_reaper([("c1", "app-busy", d)], apply=True)
        self.assertEqual(self.downs, [])

    def test_a_project_outside_the_worktrees_folder_is_never_touched(self):
        other = os.path.join(self.root, "repos", "main-app")
        os.makedirs(other)
        os.utime(other, (NOW - 99 * HOUR, NOW - 99 * HOUR))
        out = self.run_reaper([("c1", "main-app", other)], apply=True)
        self.assertEqual(self.downs, [])
        self.assertEqual(out["kept"][0][1], "not in a worktree")

    def test_a_path_that_climbs_out_of_the_worktrees_folder_is_never_touched(self):
        d = self.worktree("app--x", age_h=16)
        sneaky = os.path.join(d, "..", "..", "main-app")
        self.run_reaper([("c1", "main-app", sneaky)], apply=True)
        self.assertEqual(self.downs, [])

    def test_a_project_name_that_is_not_plain_is_refused(self):
        d = self.worktree("app--y", age_h=16)
        out = self.run_reaper([("c1", "--rm; x", d)], apply=True)
        self.assertEqual(self.downs, [])
        self.assertEqual(out["kept"][0][1], "odd project name")

    def test_two_containers_of_one_project_give_one_down(self):
        d = self.worktree("app--two", age_h=16)
        self.run_reaper([("c1", "app-two", d), ("c2", "app-two", d)], apply=True)
        self.assertEqual(self.downs, ["app-two"])

    def test_the_newest_file_decides_not_the_folder(self):
        d = self.worktree("app--deep", age_h=16)
        sub = os.path.join(d, "src")
        os.makedirs(sub)
        f = os.path.join(sub, "new.ts")
        open(f, "w").write("x")
        os.utime(f, (NOW - 1 * HOUR, NOW - 1 * HOUR))
        self.run_reaper([("c1", "app-deep", d)], apply=True)
        self.assertEqual(self.downs, [])

    def test_node_modules_and_next_do_not_count_as_activity(self):
        d = self.worktree("app--cache", age_h=16)
        nm = os.path.join(d, "node_modules", "p")
        os.makedirs(nm)
        f = os.path.join(nm, "i.js")
        open(f, "w").write("x")
        os.utime(f, (NOW - 10, NOW - 10))
        os.utime(d, (NOW - 16 * HOUR, NOW - 16 * HOUR))  # making node_modules touched the folder; put its time back
        self.run_reaper([("c1", "app-cache", d)], apply=True)
        self.assertEqual(self.downs, ["app-cache"])

    def test_a_big_container_with_no_memory_limit_is_flagged_and_never_stopped(self):
        out = self.run_reaper([], stats=[("c9", "next-dev", 12 * 1024**3, 0), ("c8", "small", 1 * 1024**3, 0), ("c7", "capped", 9 * 1024**3, 4 * 1024**3)],
                              apply=True)
        self.assertEqual([x[1] for x in out["unbounded"]], ["next-dev"])
        self.assertEqual(self.downs, [])

    def test_the_report_text_names_what_was_done(self):
        d = self.worktree("app--old", age_h=16)
        out = self.run_reaper([("c1", "app-old", d)], apply=True, stats=[("c9", "next-dev", 12 * 1000**3, 0)])
        text = cr.report_text(out, apply=True)
        self.assertIn("stopped compose project app-old", text)
        self.assertIn("next-dev", text)
        self.assertIn("12.0 GB", text)
        self.assertEqual(cr.report_text({"would_reap": [], "reaped": [], "kept": [], "unbounded": []}, apply=True), "")


if __name__ == "__main__":
    unittest.main()
