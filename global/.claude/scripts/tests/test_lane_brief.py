"""lane_brief.py answers "where is this lane and does it need me?" in one call (29 Oct 2026: the hub spent about a quarter of its turns on
lane.sh --status, tmux capture-pane, report.md and lane.sh --recycle-point, and capture-pane cut long lines at the screen width).

Everything here runs against a fake HOME that holds the index, the lane folders, the transcripts and the dispatcher's state, with a
stub tmux first on PATH: nothing touches a live lane or the live ~/.claude/state."""

import json
import os
import re
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))
from tmpdirs import fresh_dir  # noqa: E402
import lane_brief  # noqa: E402

TMUX_STUB = r"""#!/usr/bin/env bash
# stub tmux: every call is logged; display answers the pane's command, capture-pane answers the plain, joined (-J) or coloured (-e) screen
# of the window named in -t. A window with no file answers like tmux for a missing window: nothing, exit 1.
d="$FAKE_TMUX"
echo "$*" >>"$d/calls.log"
cmd="$1"; shift
target=""; mode=plain
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    -t) target="${args[$((i + 1))]}" ;;
    -e) mode=esc ;;
    -J) mode=joined ;;
  esac
done
w="${target#*:}"
case "$cmd" in
  display) [ -f "$d/$w.cmd" ] && cat "$d/$w.cmd" || exit 1 ;;
  capture-pane) [ -f "$d/$w.$mode" ] && cat "$d/$w.$mode" || exit 1 ;;
  *) exit 1 ;;
esac
"""
LANE_SH_STUB = '#!/usr/bin/env bash\n[ "$1" = --recycle-point ] && echo "${FAKE_RECYCLE:-128}"\n'

RULE = "─" * 60
LAUNCH = "$ AFA_LANE=1 AFA_LANE_NAME={n} claude --model sonnet --add-dir /x --effort high --settings {{\"autoCompactWindow\":200000}} \"$(cat /x/prompt.md)\""
LONG = "Long line: " + " ".join(f"word{i}" for i in range(60))  # about 400 characters, longer than any pane is wide


def iso(ts):
    return datetime.fromtimestamp(ts, ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class LaneBrief(unittest.TestCase):
    def setUp(self):
        self.home = Path(fresh_dir(self))
        self.state = self.home / ".claude" / "state"
        self.lanes = self.state / "lanes"
        self.lanes.mkdir(parents=True)
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.tmx = self.home / "tmux"
        self.tmx.mkdir()
        self.bin = self.home / "bin"
        self.bin.mkdir()
        for name, text in (("tmux", TMUX_STUB), ("lane.sh", LANE_SH_STUB)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        self.rows = []
        self.report_ts = datetime.now().replace(hour=8, minute=1, second=0, microsecond=0).timestamp()  # 8:01 AM today

    # ---- fixture helpers ----

    def lane(self, name, billing="subscription", model="sonnet", sid="aaaa1111", recycle="128", report="PIPELINE-READY https://example.test/pull/9 | tests 3/3",
             handoff=None, out_log=None, start=None, transcript=None, ctx_k=80):
        folder = self.lanes / f"2026-10-29-{name}"
        folder.mkdir(exist_ok=True)
        if report is not None:
            (folder / "report.md").write_text(report + "\nsecond line\n")
            os.utime(folder / "report.md", (self.report_ts, self.report_ts))
        if handoff is not None:
            (folder / "handoff.md").write_text(handoff)
        if out_log is not None:
            (folder / "out.log").write_text(out_log)
        start = start if start is not None else int(self.report_ts) - 7200
        row = ["2026-10-29", name, str(self.repo), model, billing, str(folder / "prompt.md"), str(folder / "report.md"), sid, str(start), "high", "off", recycle, "-", "-", "-"]
        self.rows.append("\t".join(row))
        (self.lanes / "index.tsv").write_text("\n".join(self.rows) + "\n")
        if billing in ("subscription", "console") and sid != "-":
            self.transcript(sid, ctx_k, transcript)
        return folder

    def transcript(self, sid, ctx_k, extra=()):
        proj = self.home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.repo))
        proj.mkdir(parents=True, exist_ok=True)
        lines = [{"type": "user", "timestamp": iso(self.report_ts - 3600), "message": {"role": "user", "content": "start the work"}}]
        lines += list(extra or ())
        lines.append({"type": "assistant", "timestamp": iso(self.report_ts - 30),
                      "message": {"role": "assistant", "usage": {"input_tokens": 1000, "cache_read_input_tokens": ctx_k * 1000 - 1000, "cache_creation_input_tokens": 0}}})
        (proj / f"{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")

    def pane(self, name, cmd="claude", screen=None, joined=None, esc=None):
        """Set what the stub tmux answers for a window."""
        if cmd is not None:
            (self.tmx / f"{name}.cmd").write_text(cmd + "\n")
        for ext, text in (("plain", screen), ("joined", joined if joined is not None else screen), ("esc", esc if esc is not None else screen)):
            if text is not None:
                (self.tmx / f"{name}.{ext}").write_text(text)

    def run_brief(self, *args, lane_sh=True):
        env = {"HOME": str(self.home), "PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_TMUX": str(self.tmx), "AFA_ENGINES_AVAILABLE": "*"}
        if lane_sh:
            env["LANE_SH"] = str(self.bin / "lane.sh")
        return subprocess.run([sys.executable, "-I", str(SCRIPTS / "lane_brief.py"), *args], capture_output=True, text=True, env=env, timeout=60)

    def tmux_calls(self):
        p = self.tmx / "calls.log"
        return p.read_text().splitlines() if p.exists() else []

    # ---- the cases ----

    def test_live_claude_lane_with_a_report(self):
        self.lane("alpha", model="opus", ctx_k=80)
        screen = "\n".join(["  Tests pass.", "", "✻ Cooked for 2m 40s · done 7:55 AM", RULE, "❯ ", RULE, "  ⏵⏵ auto mode on (shift+tab to cycle)"]) + "\n"
        joined = "\n".join(["01:29 AM", "🐢 TurtleWolfe @ NX-01", LAUNCH.format(n="alpha"), RULE, "  first old line", LONG, "  Tests pass.", "", "✻ Cooked for 2m 40s · done 7:55 AM",
                            RULE, "❯ ", RULE, "  01:45 AM 🐢 TurtleWolfe 🐺 @ NX-01 | 59% free", "  ⏵⏵ auto mode on (shift+tab to cycle)"]) + "\n"
        self.pane("alpha", "claude", screen=screen, joined=joined)
        r = self.run_brief("alpha")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("lane alpha: idle, report written", out)
        self.assertIn("line 1: PIPELINE-READY https://example.test/pull/9 | tests 3/3", out)
        self.assertIn("file time 8:01 AM", out)
        self.assertIn("engine: claude (subscription)  model: opus", out)
        self.assertIn("context: 80K of recycle line 128K (62%)", out)
        self.assertIn("pane: running claude", out)
        self.assertIn("dialog: no", out)
        self.assertIn(LONG, out)  # joined with -J: not cut at the screen width
        for gone in ("AFA_LANE_NAME", RULE, "⏵⏵", "🐢"):
            self.assertNotIn(gone, out)
        self.assertIn(f"{self.lanes}/2026-10-29-alpha", out)

    def test_lines_option_limits_the_screen(self):
        self.lane("alpha")
        self.pane("alpha", screen="\n".join(f"line {i}" for i in range(1, 31)) + "\n")
        out = self.run_brief("alpha", "--lines", "3").stdout
        shown = out.split("screen, last 3 non-blank lines:\n")[1].splitlines()
        self.assertEqual([s.strip() for s in shown], ["line 28", "line 29", "line 30"])
        default = self.run_brief("alpha").stdout.split("screen, last 12 non-blank lines:\n")[1].splitlines()
        self.assertEqual(len(default), 12)

    def test_suggestion_in_the_idle_input_box_is_labelled_and_typed_text_is_not(self):
        self.lane("alpha")
        self.pane("alpha", screen="  done\n❯ send it\n", esc="  done\n\x1b[39m❯\x1b[0m \x1b[2msend it\x1b[0m\n")
        out = self.run_brief("alpha").stdout
        self.assertIn("❯ send it  (suggestion, not typed)", out)
        self.pane("alpha", screen="  done\n❯ send it\n", esc="  done\n\x1b[39m❯\x1b[0m send it\n")
        out = self.run_brief("alpha").stdout
        self.assertIn("❯ send it", out)
        self.assertNotIn("suggestion, not typed", out)
        # words typed and a grey completion after them: the input box holds typed text, so nothing is called a suggestion
        self.pane("alpha", screen="  done\n❯ send it now\n", esc="  done\n\x1b[39m❯\x1b[0m send\x1b[2m it now\x1b[0m\n")
        self.assertNotIn("suggestion, not typed", self.run_brief("alpha").stdout)

    def test_working_and_error_states_follow_lane_sh_status(self):
        self.lane("alpha")
        self.pane("alpha", screen="  Working\n✻ Thinking… (12s · 3.1k tokens · esc to interrupt)\n")
        self.assertIn("lane alpha: working, report written", self.run_brief("alpha").stdout)
        self.pane("alpha", screen="  API Error: 529 overloaded\n")
        self.assertIn("lane alpha: error", self.run_brief("alpha").stdout)

    def test_a_report_after_which_the_lane_got_input_is_stale(self):
        later = {"type": "user", "timestamp": iso(self.report_ts + 600), "message": {"role": "user", "content": "one more thing"}}
        self.lane("alpha", transcript=[later])
        self.pane("alpha", screen="  ok\n")
        out = self.run_brief("alpha").stdout
        self.assertIn("report: report stale (input), file time 8:01 AM", out)
        self.assertIn("the lane got input after report.md was written", out)
        self.assertIn("lane alpha: idle, report stale", out)

    def test_no_report_yet(self):
        self.lane("alpha", report=None)
        self.pane("alpha", screen="  ok\n")
        out = self.run_brief("alpha").stdout
        self.assertIn("report: not yet", out)
        self.assertIn("hand-off: none", out)
        self.assertIn("lane alpha: idle, no report yet", out)

    def test_handoff_time_and_summary_when_it_is_newer_than_the_report(self):
        folder = self.lane("alpha", handoff="## Done\n- built it\n## Next\n- open the PR\n")
        later = self.report_ts + 300
        os.utime(folder / "handoff.md", (later, later))
        self.pane("alpha", screen="  ok\n")
        out = self.run_brief("alpha").stdout
        self.assertIn("hand-off: handoff.md at 8:06 AM (newer than the report)", out)
        self.assertIn("Done: built it | Next: open the PR", out)

    def test_dead_pane_names_the_command_it_shows(self):
        self.lane("alpha")
        self.pane("alpha", cmd="bash", screen="$ \n")
        out = self.run_brief("alpha").stdout
        self.assertIn("pane: DEAD (pane shows bash)", out)
        self.assertIn("lane alpha: DEAD", out)
        self.pane("alpha", cmd="", screen="$ \n")
        self.assertIn("pane: DEAD (pane shows nothing", self.run_brief("alpha").stdout)

    def test_no_window_at_all_is_dead(self):
        self.lane("alpha")
        out = self.run_brief("alpha").stdout
        self.assertIn("pane: DEAD (pane shows nothing: no window named alpha", out)
        self.assertIn("(nothing readable", out)

    def test_claude_node_and_a_version_string_count_as_running(self):
        self.lane("alpha")
        for cmd in ("claude", "node", "2.1.119"):
            self.pane("alpha", cmd=cmd, screen="  ok\n")
            self.assertIn(f"pane: running {cmd}", self.run_brief("alpha").stdout)

    def test_a_held_dialog_prints_its_first_lines_with_the_whole_command(self):
        self.lane("alpha")
        command = "rm -rf " + "/very/long/path/" * 30 + "target"
        screen = "\n".join(["  earlier output", RULE, " Bash command", "", "   " + command[:60], " Do you want to proceed?", " ❯ 1. Yes", "   2. No (esc)", "", " Esc to cancel"]) + "\n"
        joined = "\n".join(["  earlier output", RULE, " Bash command", "", "   " + command, " Do you want to proceed?", " ❯ 1. Yes", "   2. No (esc)", "", " Esc to cancel"]) + "\n"
        self.pane("alpha", screen=screen, joined=joined)
        out = self.run_brief("alpha").stdout
        self.assertIn("NEEDS YOU: waiting on a dialog", out)
        self.assertIn("dialog: YES", out)
        dialog = out.split("dialog: YES")[1].split("screen, last")[0]
        self.assertIn("|  Bash command", dialog)
        self.assertIn(command, dialog)  # the full command, not what the screen width showed
        self.assertNotIn("earlier output", dialog)  # the dialog starts at its top border

    def test_dialog_rule_is_the_one_lane_watch_uses(self):
        watch = (SCRIPTS / "lane_watch.sh").read_text()
        m = re.search(r"dialog_waiting\(\) \{\s*tail -(\d+) \| grep -qE '([^']+)'", watch)
        self.assertIsNotNone(m, "lane_watch.sh no longer has dialog_waiting in the form lane_brief.py copies")
        self.assertEqual(int(m.group(1)), lane_brief.DIALOG_TAIL)
        self.assertEqual(m.group(2), lane_brief.DIALOG_WAITING)

    def test_old_dialog_text_outside_the_last_15_lines_is_not_a_held_dialog(self):
        self.lane("alpha")
        screen = "  Do you want to proceed?\n" + "\n".join(f"  later line {i}" for i in range(20)) + "\n"
        self.pane("alpha", screen=screen)
        self.assertIn("dialog: no", self.run_brief("alpha").stdout)

    def test_headless_engine_shows_out_log_and_when_the_engine_is_blocked_until(self):
        until = datetime(2030, 1, 15, 3, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
        (self.state / "openclaw-dispatch.json").write_text(json.dumps({"agy_blocked_until": until, "codex_blocked_until": 1577836800.0}))
        log = "\n".join(["starting", "", "\x1b[91mError: \x1b[0mquota used up", "agy: stopped", "exit 75"]) + "\n"
        self.lane("beta", billing="agy", sid="-", out_log=log)
        r = self.run_brief("beta", "--lines", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("quota: agy BLOCKED until 3:00 AM Tue Jan 15 EST (Eastern)", out)
        self.assertIn("lane beta: headless agy lane, exit75; agy is BLOCKED until 3:00 AM Tue Jan 15 EST", out)
        self.assertIn("engine: agy  model: sonnet", out)
        tail = out.split("non-blank lines (")[1].split("):\n")[1].splitlines()
        self.assertEqual([s.strip() for s in tail], ["Error: quota used up", "agy: stopped", "exit 75"])  # colour codes removed, blank line skipped
        self.assertEqual(self.tmux_calls(), [], "a headless lane has no pane: tmux is not asked")
        self.lane("gamma", billing="codex", sid="-", out_log="working on it\n")
        out = self.run_brief("gamma").stdout
        self.assertIn("quota: codex not blocked (last block ended", out)  # a block that has passed
        self.assertIn("state: working", out)

    def test_unknown_lane_exits_2(self):
        self.lane("alpha")
        r = self.run_brief("alhpa")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("unknown lane: alhpa", r.stderr)
        self.assertIn("did you mean: alpha", r.stderr)

    def test_json_is_one_object_with_the_same_facts(self):
        self.lane("alpha", model="opus", ctx_k=80)
        self.pane("alpha", screen="  ok\n❯ next\n", joined="  ok\n❯ next\n", esc="  ok\n❯ \x1b[2mnext\x1b[0m\n")
        r = self.run_brief("alpha", "--json", "--lines", "5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)
        d = json.loads(r.stdout)
        self.assertEqual(d["lane"], "alpha")
        self.assertEqual(d["report"]["state"], "written")
        self.assertEqual(d["report"]["line1"], "PIPELINE-READY https://example.test/pull/9 | tests 3/3")
        self.assertEqual(d["report"]["time12"], "8:01 AM")
        self.assertEqual((d["context_k"], d["recycle_k"]), (80, 128))
        self.assertEqual(d["pane"], {"command": "claude", "alive": True})
        self.assertFalse(d["dialog"]["held"])
        self.assertEqual(d["tail"]["suggestion"], "next")
        self.assertEqual(d["tail"]["lines"][-1], "❯ next  (suggestion, not typed)")

    def test_json_keeps_a_long_line_the_text_view_cuts(self):
        self.lane("alpha")
        huge = "x" * 3000
        self.pane("alpha", screen=huge + "\n")
        self.assertIn("more characters", self.run_brief("alpha").stdout)
        self.assertEqual(json.loads(self.run_brief("alpha", "--json").stdout)["tail"]["lines"], [huge])

    def test_it_never_writes_and_never_sends_keys(self):
        folder = self.lane("alpha", handoff="Done: a\nNext: b\n")
        self.pane("alpha", screen="  Do you want to proceed?\n")
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob("*") if p.is_file() and "tmux" not in p.parts}
        self.run_brief("alpha")
        self.run_brief("alpha", "--json")
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob("*") if p.is_file() and "tmux" not in p.parts}
        self.assertEqual(before, after)
        self.assertTrue(folder.exists())
        verbs = {c.split()[0] for c in self.tmux_calls()}
        self.assertEqual(verbs - {"display", "capture-pane"}, set(), "only display and capture-pane: never send-keys")

    def test_context_follows_the_session_the_lane_hook_recorded_without_writing_the_index(self):
        # a /clear started a new transcript and the index row still names the old one (22 Oct, lane.sh follow_session)
        self.lane("alpha", sid="aaaa1111", ctx_k=20)
        self.transcript("bbbb2222", 150)
        sess = self.lanes / "sessions"
        sess.mkdir()
        (sess / "alpha").write_text(f"bbbb2222\t{int(self.report_ts) - 60}\t-\n")
        self.pane("alpha", screen="  ok\n")
        index_before = (self.lanes / "index.tsv").read_bytes()
        out = self.run_brief("alpha").stdout
        self.assertIn("context: 150K of recycle line 128K (117%)  OVER THE LINE", out)
        self.assertIn("lane alpha: idle, past its recycle line (150K of 128K)", out)
        self.assertEqual((self.lanes / "index.tsv").read_bytes(), index_before)
        # a session another lane's row claims is never this lane's successor
        self.lane("other", sid="bbbb2222", ctx_k=33)
        self.assertIn("context: 20K of recycle line", self.run_brief("alpha").stdout)

    def test_the_recycle_line_comes_from_the_real_lane_sh(self):
        self.lane("alpha", recycle="137", ctx_k=50)
        self.pane("alpha", screen="  ok\n")
        r = self.run_brief("alpha", lane_sh=False)  # no LANE_SH: the lane.sh beside lane_brief.py answers
        self.assertIn("context: 50K of recycle line 137K", r.stdout)

    def test_a_headless_claude_lane_on_console_credits_is_headless(self):
        self.lane("alpha", billing="console", sid="-", out_log="all done\nexit 0\n")
        out = self.run_brief("alpha").stdout
        self.assertIn("lane alpha: headless claude lane, done", out)
        self.assertEqual(self.tmux_calls(), [])

    def test_text_output_uses_the_12_hour_clock(self):
        self.lane("alpha", handoff="Done: a\nNext: b\n")
        self.pane("alpha", screen="  ok\n")
        out = self.run_brief("alpha").stdout
        for t in re.findall(r"\b\d{1,2}:\d{2}\b(?: [AP]M)?", out.split("screen, last")[0]):
            self.assertRegex(t, r"^\d{1,2}:\d{2} [AP]M$")


if __name__ == "__main__":
    unittest.main()
