"""Tests for panel_scoreboard.py. The scoreboard goes to a temp file (PANEL_SCOREBOARD); the panel is a stub script, never a real API."""
import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import panel_scoreboard as sb  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

LEGACY = [
    {"date": "2026-10-01", "repo": "runit", "item": "a", "opus_first_review": "pass", "panel_majority": "pass",
     "experts": {"groq": "pass", "gemini": "error"}, "landed": "x"},
    {"date": "2026-10-01", "repo": "runit", "item": "b", "opus_first_review": "pass", "panel_majority": "pass"},
    {"date": "2026-10-01", "repo": "runit", "item": "c", "opus_first_review": "pass", "panel_majority": "pass", "notes": "n"},
]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "planted"


def panel(verdicts, gate="send", majority=None):
    d = {"class": "public", "gate": gate, "experts": {n: {"verdict": v, "blocking": []} for n, v in verdicts.items()}}
    if majority:
        d["majority"] = majority
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(fresh_dir(self))
        self.board = self.dir / "board.jsonl"
        p = mock.patch.dict(os.environ, {"PANEL_SCOREBOARD": str(self.board)})
        p.start()
        self.addCleanup(p.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = sb.main(list(argv))
        return rc, out.getvalue()

    def pj(self, verdicts, raw=False, **kw):
        d = panel(verdicts, **kw)
        f = self.dir / f"p{len(list(self.dir.iterdir()))}.json"
        f.write_text(("noise\n__PANEL_JSON=" + json.dumps(d) + "__\n") if raw else json.dumps(d))
        return str(f)

    def rows(self):
        return [json.loads(x) for x in self.board.read_text().splitlines()]

    def seed(self, n, panel_v="pass", claude="pass", extra=None):
        with open(self.board, "a") as f:
            for i in range(n):
                f.write(json.dumps({"panel_majority": panel_v, "claude_verdict": claude, "agree": panel_v == claude,
                                    "false_pass": panel_v == "pass" and claude == "revise", **(extra or {})}) + "\n")

    def planted(self, caught):
        with open(self.board, "a") as f:
            f.write(json.dumps({"kind": "planted", "caught": caught}) + "\n")

    def add(self, verdicts, claude, blocking=None, raw=False):
        args = ["add", "--repo", "r", "--ref", "abc", "--class", "own", "--panel-json", self.pj(verdicts, raw=raw),
                "--claude-verdict", claude]
        if blocking is not None:
            args += ["--claude-blocking", str(blocking)]
        return self.run_cli(*args)


class AddTests(Base):
    def test_row_shape_and_agreement(self):
        self.add({"a": "pass", "b": "pass", "c": "revise", "d": "error"}, "pass")
        r = self.rows()[0]
        for k in ("ts", "repo", "ref", "class", "panel_majority", "pass_votes", "revise_votes", "members",
                  "claude_verdict", "agree", "false_pass"):
            self.assertIn(k, r)
        self.assertEqual((r["panel_majority"], r["pass_votes"], r["revise_votes"], r["agree"], r["false_pass"]),
                         ("pass", 2, 1, True, False))
        self.assertEqual(r["members"]["d"], "error")

    def test_reads_raw_panel_output_with_sentinel(self):
        self.add({"a": "revise", "b": "revise"}, "revise", raw=True)
        self.assertEqual(self.rows()[0]["panel_majority"], "revise")

    def test_false_pass_needs_blocking_findings(self):
        self.add({"a": "pass", "b": "pass"}, "revise", blocking=2)
        self.add({"a": "pass", "b": "pass"}, "revise", blocking=0)
        self.add({"a": "pass", "b": "pass"}, "revise")   # unstated: a revise counts as blocking
        self.assertEqual([r["false_pass"] for r in self.rows()], [True, False, True])

    def test_note_and_unreadable_json(self):
        bad = self.dir / "bad.json"
        bad.write_text("not json")
        rc, _ = self.run_cli("add", "--repo", "r", "--ref", "x", "--class", "own", "--panel-json", str(bad),
                             "--claude-verdict", "pass")
        self.assertEqual(rc, 2)
        self.assertFalse(self.board.exists())

    def test_client_class_refused(self):
        with self.assertRaises(SystemExit):
            self.run_cli("add", "--repo", "r", "--ref", "x", "--class", "client", "--panel-json", "f", "--claude-verdict", "pass")


class StatusTests(Base):
    def test_reads_three_legacy_rows(self):
        self.board.write_text("".join(json.dumps(r) + "\n" for r in LEGACY) + "garbage line\n")
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 1)
        self.assertIn("scored changes: 3", out)
        self.assertIn("agreement: 100%", out)
        self.assertIn("NOT READY", out)

    def test_missing_file_is_not_ready(self):
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 1)
        self.assertIn("scored changes: 0", out)

    def test_ready_when_bar_met(self):
        self.seed(15)
        self.planted(True)
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertTrue(out.strip().endswith("READY") and "NOT READY" not in out)

    def test_each_bar_blocks(self):
        self.seed(14)
        self.planted(True)
        self.assertIn("need 15", self.run_cli("status")[1])
        self.seed(1, "pass", "revise")   # 15 changes, one false pass and agreement 93%
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 1)
        self.assertIn("1 false pass", out)

    def test_low_agreement_blocks(self):
        self.seed(13)
        self.seed(2, "revise", "pass")
        self.planted(True)
        out = self.run_cli("status")[1]
        self.assertIn("agreement 87%", out)

    def test_planted_missed_or_absent_blocks_and_latest_wins(self):
        self.seed(15)
        self.assertIn("no planted-fault", self.run_cli("status")[1])
        self.planted(False)
        self.assertIn("missed", self.run_cli("status")[1])
        self.planted(True)
        self.assertEqual(self.run_cli("status")[0], 0)

    def test_planted_rows_are_not_scored_changes(self):
        self.planted(True)
        self.assertIn("scored changes: 0", self.run_cli("status")[1])


class PlantedTests(Base):
    def stub(self, verdicts, majority=None):
        out = {"class": "public", "gate": "send", "experts": {n: {"verdict": v} for n, v in verdicts.items()}}
        if majority:
            out["majority"] = majority
        s = self.dir / "stub_panel.py"
        s.write_text("import sys, json\n"
                     f"open({str(self.dir / 'args.txt')!r}, 'w').write(' '.join(sys.argv[1:]))\n"
                     f"print('__PANEL_MAJORITY=x__')\nprint('__PANEL_JSON=' + {json.dumps(json.dumps(out))} + '__')\n")
        return f"{sys.executable} {s}"

    def test_fixture_files_exist_and_carry_the_bug(self):
        for n in ("spec.txt", "acceptance.txt", "planted.diff"):
            self.assertTrue((FIXTURE / n).is_file())
        d = (FIXTURE / "planted.diff").read_text()
        self.assertIn("len(items) - n + 1", d)
        self.assertIn('"echo report for " + path', d)

    def test_caught(self):
        with mock.patch.dict(os.environ, {"PANEL_REVIEW_CMD": self.stub({"a": "revise", "b": "revise", "c": "pass"})}):
            rc, out = self.run_cli("planted", "--repo-dir", str(FIXTURE))
        self.assertEqual(rc, 0)
        self.assertIn("caught", out)
        r = self.rows()[0]
        self.assertEqual((r["kind"], r["caught"]), ("planted", True))
        args = (self.dir / "args.txt").read_text()
        self.assertIn("--class public", args)
        self.assertIn("planted.diff", args)

    def test_missed(self):
        with mock.patch.dict(os.environ, {"PANEL_REVIEW_CMD": self.stub({"a": "pass", "b": "pass"})}):
            rc, _ = self.run_cli("planted", "--repo-dir", str(FIXTURE))
        self.assertEqual(rc, 1)
        self.assertFalse(self.rows()[0]["caught"])

    def test_no_panel_output_is_a_miss(self):
        with mock.patch.dict(os.environ, {"PANEL_REVIEW_CMD": f"{sys.executable} -c pass"}):
            self.run_cli("planted", "--repo-dir", str(FIXTURE))
        self.assertFalse(self.rows()[0]["caught"])

    def test_missing_fixture(self):
        rc, _ = self.run_cli("planted", "--repo-dir", str(self.dir))
        self.assertEqual(rc, 2)
        self.assertFalse(self.board.exists())


class SkipTests(Base):
    GOOD = {"a": "pass", "b": "pass", "c": "pass"}

    def skip(self, verdicts=None, cls="own", ref="r1", **kw):
        return self.run_cli("should-skip-claude", "--panel-json", self.pj(verdicts or self.GOOD, **kw), "--class", cls, "--ref", ref)[1].strip()

    def ready(self):
        self.seed(15)
        self.planted(True)

    def refs(self, outcome):
        return [r for r in (f"ref{i}" for i in range(200)) if self.skip(ref=r) == outcome]

    def test_not_ready_is_no(self):
        self.assertEqual(set(self.skip(ref=f"x{i}") for i in range(20)), {"no"})

    def test_client_is_always_no(self):
        self.ready()
        self.assertEqual(set(self.skip(cls="client", ref=f"x{i}") for i in range(20)), {"no"})

    def test_sampling_is_deterministic_and_about_one_in_four(self):
        self.ready()
        yes = self.refs("yes")
        self.assertEqual(self.refs("yes"), yes)
        self.assertTrue(120 <= len(yes) <= 180, len(yes))
        self.assertEqual(set(self.refs("no")) & set(yes), set())

    def test_conditions_each_force_no(self):
        self.ready()
        ref = self.refs("yes")[0]
        self.assertEqual(self.skip(ref=ref), "yes")
        self.assertEqual(self.skip(ref=ref, cls="public"), "yes")
        self.assertEqual(self.skip({"a": "pass", "b": "pass"}, ref=ref), "no")                  # only 2 pass votes
        self.assertEqual(self.skip({**self.GOOD, "d": "revise"}, ref=ref), "no")                # a revise vote
        self.assertEqual(self.skip(ref=ref, gate="local"), "no")                                # not sent
        self.assertEqual(self.skip({"a": "revise", "b": "revise", "c": "revise"}, ref=ref), "no")
        self.assertEqual(self.skip({**self.GOOD, "d": "error", "e": "unavailable"}, ref=ref), "yes")

    def test_unreadable_panel_json_is_no(self):
        self.ready()
        bad = self.dir / "bad.json"
        bad.write_text("x")
        self.assertEqual(self.run_cli("should-skip-claude", "--panel-json", str(bad), "--class", "own", "--ref", "r")[1].strip(), "no")


if __name__ == "__main__":
    unittest.main()
