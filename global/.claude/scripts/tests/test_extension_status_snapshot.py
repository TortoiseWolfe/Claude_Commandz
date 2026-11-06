"""/extension-status snapshots and the "what changed since last run" view (issue 2).
The command gathers the API answers into one JSON object and pipes it to extension_status_snapshot.py, which saves it under
<state>/<repo-slug>/<YYYY-MM-DD-HHMM>.json, picks the older snapshot to compare with, prints the deltas, and prunes old files.
Every fixture is invented and lives in a throwaway state folder; nothing here touches the live ~/.claude/state."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "extension_status_snapshot.py"
sys.path.insert(0, str(HERE.parent))
import extension_status_snapshot as snap  # noqa: E402

REPO = "TortoiseWolfe/spec-kit-extension-wireframe"
SLUG = "TortoiseWolfe-spec-kit-extension-wireframe"
NOW = datetime(2026, 11, 6, 9, 0)


def data(stars=10, clones=(20, 5), views=(100, 30), issues=(), prs=(), mentions=(), forks=1, watchers=2):
    return {
        "repo": REPO,
        "stars": stars, "forks": forks, "watchers": watchers,
        "clones": {"count": clones[0], "uniques": clones[1]},
        "views": {"count": views[0], "uniques": views[1]},
        "issues": [{"number": n, "title": f"issue {n}"} for n in issues],
        "prs": [{"number": n, "title": f"pr {n}"} for n in prs],
        "mentions": [{"url": u, "title": f"mention {u[-1]}"} for u in mentions],
    }


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.state = Path(self._t.name)
        self.dir = self.state / SLUG

    def tearDown(self):
        self._t.cleanup()

    def put(self, when, d):
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / (when.strftime("%Y-%m-%d-%H%M") + ".json")
        p.write_text(json.dumps(d))
        return p

    def run_cli(self, d, *args, now=NOW):
        env = dict(os.environ, EXTENSION_STATUS_STATE=str(self.state), EXTENSION_STATUS_NOW=now.strftime("%Y-%m-%d %H:%M"))
        return subprocess.run([sys.executable, str(SCRIPT), "--repo", REPO, *args], input=json.dumps(d),
                              capture_output=True, text=True, env=env)


class Saving(Base):
    def test_every_run_writes_a_snapshot_named_by_date_and_time(self):
        r = self.run_cli(data())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["2026-11-06-0900.json"])
        self.assertEqual(json.loads((self.dir / "2026-11-06-0900.json").read_text())["stars"], 10)

    def test_a_snapshot_older_than_90_days_is_pruned_and_a_newer_one_kept(self):
        old = self.put(NOW - timedelta(days=91), data())
        keep = self.put(NOW - timedelta(days=89), data())
        self.run_cli(data())
        self.assertFalse(old.exists())
        self.assertTrue(keep.exists())

    def test_other_repos_are_not_pruned_or_mixed_in(self):
        other = self.state / "someone-else"
        other.mkdir()
        f = other / "2020-01-01-0000.json"
        f.write_text("{}")
        self.run_cli(data())
        self.assertTrue(f.exists())

    def test_input_that_is_not_json_fails_and_writes_nothing(self):
        env = dict(os.environ, EXTENSION_STATUS_STATE=str(self.state))
        r = subprocess.run([sys.executable, str(SCRIPT), "--repo", REPO], input="not json", capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.dir.exists())


class Diff(Base):
    def test_no_prior_snapshot_falls_back_to_absolute_mode_with_a_note(self):
        r = self.run_cli(data())
        self.assertIn("no prior snapshot", r.stdout)
        self.assertIn("absolute", r.stdout)

    def test_deltas_against_the_newest_earlier_snapshot(self):
        self.put(NOW - timedelta(days=1), data(stars=8, clones=(6, 2), issues=[1], prs=[3]))
        r = self.run_cli(data(stars=10, clones=(20, 5), issues=[1, 2], prs=[3], mentions=["https://x/1"]))
        out = r.stdout
        self.assertIn("+2 stars", out)
        self.assertIn("+14 clones", out)
        self.assertIn("1 new issue", out)
        self.assertIn("#2", out)
        self.assertIn("1 new external mention", out)
        self.assertNotIn("new PR", out)

    def test_decreases_and_closed_items_are_shown(self):
        self.put(NOW - timedelta(days=1), data(stars=10, issues=[1, 2], prs=[3]))
        out = self.run_cli(data(stars=9, issues=[1], prs=[])).stdout
        self.assertIn("-1 stars", out)
        self.assertIn("1 issue closed or gone", out)
        self.assertIn("1 PR closed or gone", out)

    def test_nothing_changed_says_so(self):
        self.put(NOW - timedelta(days=1), data())
        self.assertIn("nothing changed", self.run_cli(data()).stdout)

    def test_the_baseline_is_the_newest_snapshot_before_this_run(self):
        self.put(NOW - timedelta(days=3), data(stars=1))
        self.put(NOW - timedelta(days=1), data(stars=9))
        self.assertIn("+1 stars", self.run_cli(data(stars=10)).stdout)

    def test_since_compares_against_the_newest_snapshot_older_than_the_duration(self):
        self.put(NOW - timedelta(days=10), data(stars=2))
        self.put(NOW - timedelta(days=8), data(stars=4))
        self.put(NOW - timedelta(days=1), data(stars=9))
        out = self.run_cli(data(stars=10), "--since", "7d").stdout
        self.assertIn("+6 stars", out)

    def test_since_with_nothing_old_enough_falls_back_to_absolute(self):
        self.put(NOW - timedelta(days=1), data(stars=9))
        out = self.run_cli(data(stars=10), "--since", "7d").stdout
        self.assertIn("no prior snapshot", out)

    def test_no_diff_forces_absolute_mode_but_still_saves(self):
        self.put(NOW - timedelta(days=1), data(stars=1))
        r = self.run_cli(data(stars=10), "--no-diff")
        self.assertIn("absolute", r.stdout)
        self.assertNotIn("+9 stars", r.stdout)
        self.assertTrue((self.dir / "2026-11-06-0900.json").exists())

    def test_a_bad_since_value_is_refused_and_nothing_is_written(self):
        r = self.run_cli(data(), "--since", "soon")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.dir.exists())


class MissingParts(Base):
    def test_a_part_one_side_could_not_fetch_is_not_shown_as_a_drop(self):
        self.put(NOW - timedelta(days=1), data(stars=10, clones=(20, 5)))
        d = data(stars=10)
        del d["clones"]  # traffic gave a 403 this time
        out = self.run_cli(d).stdout
        self.assertNotIn("clones", out)
        self.assertIn("nothing changed", out)


class CommandText(unittest.TestCase):
    """The command is a prompt, so its text must tell the model to use the helper and describe both flags."""

    text = (HERE.parents[1] / "commands" / "extension-status.md").read_text()

    def test_usage_lists_both_flags(self):
        self.assertIn("--since", self.text)
        self.assertIn("--no-diff", self.text)

    def test_it_calls_the_helper_with_the_repo(self):
        self.assertIn("extension_status_snapshot.py --repo", self.text)

    def test_only_a_full_run_is_saved(self):
        self.assertRegex(self.text, r"(?i)only (a )?full (report|run)")

    def test_the_90_day_prune_is_documented(self):
        self.assertIn("90 days", self.text)


class Helpers(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(snap.parse_duration("7d"), timedelta(days=7))
        self.assertEqual(snap.parse_duration("12h"), timedelta(hours=12))
        self.assertEqual(snap.parse_duration("2w"), timedelta(weeks=2))
        with self.assertRaises(ValueError):
            snap.parse_duration("7")

    def test_slug(self):
        self.assertEqual(snap.slug("a/b.c"), "a-b.c")


if __name__ == "__main__":
    unittest.main()
