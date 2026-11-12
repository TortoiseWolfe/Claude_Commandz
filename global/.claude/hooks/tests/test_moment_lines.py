"""moment-lines.py: a table-driven reminder that speaks at the moment of need.

The hook is run as its own process with the event as JSON on stdin. The table, the state
folder and HOME are temporary, so nothing reads the real table or home folder and no network
is used. All names are invented.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "moment-lines.py"


def row(rid, when="prompt", who="all", where="-", pattern="widgets", unless="-", action="say", line="Check the widgets"):
    return "\t".join([rid, when, who, where, pattern, unless, action, line])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="moment-lines-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.state = self.tmp / "state"
        self.table = self.tmp / "table.tsv"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_table(self, *rows):
        self.table.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def run_hook(self, payload, env=None, args=(), table=True, stdin=None):
        full_env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "MOMENT_LINES_STATE": str(self.state)}
        if table:
            full_env["MOMENT_LINES_TABLE"] = str(self.table)
        full_env.update(env or {})
        data = stdin if stdin is not None else json.dumps(payload)
        r = subprocess.run([sys.executable, str(HOOK), *args], input=data, capture_output=True, text=True,
                           env=full_env)
        return r.returncode, r.stdout, r.stderr

    def prompt(self, text, session="s1", cwd="/work/acme", **kw):
        p = {"hook_event_name": "UserPromptSubmit", "prompt": text, "cwd": cwd}
        if session is not None:
            p["session_id"] = session
        return self.run_hook(p, **kw)

    def tool(self, name, tool_input, session="s1", cwd="/work/acme", **kw):
        p = {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": tool_input, "cwd": cwd}
        if session is not None:
            p["session_id"] = session
        return self.run_hook(p, **kw)

    def bash(self, command, **kw):
        return self.tool("Bash", {"command": command}, **kw)

    def context(self, out):
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def assertSilent(self, result):
        self.assertEqual(result, (0, "", ""))


class TestSay(Base):
    def test_prompt_say_prints_json_shape(self):
        self.write_table(row("widget-note"))
        code, out, err = self.prompt("fix the widgets please")
        self.assertEqual((code, err), (0, ""))
        data = json.loads(out)
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertEqual(data["hookSpecificOutput"]["additionalContext"],
                         "Moment of need:\n- Check the widgets (widget-note)")

    def test_no_match_is_silent(self):
        self.write_table(row("widget-note"))
        self.assertSilent(self.prompt("nothing relevant"))

    def test_header_and_comments_skipped(self):
        self.write_table("id\twhen\twho\twhere\tpattern\tunless\taction\tline", "# note", "", row("widget-note"))
        code, out, _ = self.prompt("widgets")
        self.assertIn("(widget-note)", self.context(out))

    def test_dash_means_empty(self):
        self.write_table(row("widget-note", where="-", unless="-"))
        self.assertEqual(self.prompt("widgets")[0], 0)
        self.assertIn("widget-note", self.prompt("widgets", session="s2")[1])

    def test_once_per_session_and_again_in_new_session(self):
        self.write_table(row("widget-note"))
        self.assertIn("widget-note", self.prompt("widgets")[1])
        self.assertSilent(self.prompt("widgets more"))
        self.assertIn("widget-note", self.prompt("widgets", session="s2")[1])

    def test_no_session_id_repeats(self):
        self.write_table(row("widget-note"))
        self.assertIn("widget-note", self.prompt("widgets", session=None)[1])
        self.assertIn("widget-note", self.prompt("widgets", session=None)[1])

    def test_session_id_is_sanitized(self):
        self.write_table(row("widget-note"))
        self.prompt("widgets", session="../../evil/x")
        names = [p.name for p in self.state.iterdir()]
        self.assertEqual(names, ["evilx.json"])

    def test_unreadable_state_repeats(self):
        self.write_table(row("widget-note"))
        self.state.write_text("a file, not a folder")
        self.assertIn("widget-note", self.prompt("widgets")[1])
        self.assertIn("widget-note", self.prompt("widgets")[1])

    def test_tool_row_does_not_fire_on_prompt_and_prompt_row_not_on_tool(self):
        self.write_table(row("tool-row", when="tool:Bash"), row("prompt-row"))
        code, out, _ = self.prompt("widgets")
        self.assertNotIn("tool-row", out)
        code, out, _ = self.bash("echo widgets")
        self.assertIn("tool-row", self.context(out))
        self.assertNotIn("prompt-row", out)

    def test_pretooluse_say_event_name(self):
        self.write_table(row("tool-row", when="tool:Bash"))
        code, out, _ = self.bash("ls widgets")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "PreToolUse")


class TestMatching(Base):
    def test_who_hub_and_lane(self):
        self.write_table(row("hub-row", who="hub"), row("lane-row", who="lane"), row("all-row", who="all"))
        _, out, _ = self.prompt("widgets", session="a")
        ctx = self.context(out)
        self.assertIn("hub-row", ctx)
        self.assertIn("all-row", ctx)
        self.assertNotIn("lane-row", ctx)
        _, out, _ = self.prompt("widgets", session="b", env={"AFA_LANE": "1"})
        ctx = self.context(out)
        self.assertIn("lane-row", ctx)
        self.assertIn("all-row", ctx)
        self.assertNotIn("hub-row", ctx)

    def test_where_matches_cwd(self):
        self.write_table(row("acme-only", where="acme"))
        self.assertIn("acme-only", self.prompt("widgets", cwd="/work/acme")[1])
        self.assertSilent(self.prompt("widgets", cwd="/work/other", session="s2"))

    def test_where_matches_command(self):
        self.write_table(row("deploy-row", when="tool:Bash", where="deploy", pattern="widgets"))
        self.assertSilent(self.bash("echo widgets", cwd="/work/other"))
        self.assertIn("deploy-row", self.bash("deploy widgets", cwd="/work/other", session="s2")[1])

    def test_case_is_ignored(self):
        self.write_table(row("widget-note", pattern="WIDGETS"))
        self.assertIn("widget-note", self.prompt("Widgets")[1])

    def test_unless_stops_row(self):
        self.write_table(row("widget-note", unless="gadgets"))
        self.assertSilent(self.prompt("widgets and gadgets"))
        self.assertIn("widget-note", self.prompt("widgets", session="s2")[1])

    def test_tool_regex_must_fully_match(self):
        self.write_table(row("edit-row", when="tool:Edit", pattern="widgets"))
        self.assertSilent(self.tool("MultiEdit", {"x": "widgets"}))
        self.assertSilent(self.tool("Edi", {"x": "widgets"}))
        self.assertIn("edit-row", self.tool("Edit", {"x": "widgets"})[1])

    def test_tool_regex_alternation(self):
        self.write_table(row("multi", when="tool:Edit|Write", pattern="widgets"))
        self.assertIn("multi", self.tool("Write", {"x": "widgets"})[1])

    def test_bash_uses_command_only(self):
        self.write_table(row("bash-row", when="tool:Bash", pattern="description"))
        self.assertSilent(self.tool("Bash", {"command": "ls", "description": "description"}))

    def test_other_tool_uses_json_input(self):
        self.write_table(row("write-row", when="tool:Write", pattern='"file_path": "/a/b widgets'))
        self.assertIn("write-row", self.tool("Write", {"file_path": "/a/b widgets.txt", "content": "x"})[1])

    def test_prompt_truncated(self):
        self.write_table(row("widget-note"))
        self.assertSilent(self.prompt("x" * 4000 + "widgets"))

    def test_three_line_cap_in_table_order(self):
        self.write_table(*[row("r%d" % i, line="line %d" % i) for i in range(1, 6)])
        _, out, _ = self.prompt("widgets")
        ctx = self.context(out)
        self.assertEqual(ctx.count("\n- "), 3)
        self.assertTrue(ctx.index("(r1)") < ctx.index("(r2)") < ctx.index("(r3)"))
        self.assertNotIn("(r4)", ctx)
        # the ones not printed are not marked shown, so they come next time
        _, out, _ = self.prompt("widgets")
        ctx = self.context(out)
        self.assertIn("(r4)", ctx)
        self.assertIn("(r5)", ctx)
        self.assertNotIn("(r1)", ctx)


class TestRefusals(Base):
    def test_once_refuses_first_then_passes(self):
        self.write_table(row("once-row", when="tool:Bash", action="once", line="Read the plan first"))
        code, out, err = self.bash("do widgets")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("BLOCKED by a moment line."))
        self.assertIn("one-time reminder", err)
        self.assertIn("- Read the plan first (once-row)", err)
        self.assertSilent(self.bash("do widgets"))
        self.assertEqual(self.bash("do widgets", session="s2")[0], 2)

    def test_once_without_session_only_says(self):
        self.write_table(row("once-row", when="tool:Bash", action="once"))
        code, out, err = self.bash("do widgets", session=None)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("once-row", self.context(out))

    def test_once_on_prompt_says(self):
        self.write_table(row("once-row", action="once"))
        code, out, _ = self.prompt("widgets")
        self.assertEqual(code, 0)
        self.assertIn("once-row", self.context(out))

    def test_refuse_every_time_and_unless_is_the_way_through(self):
        self.write_table(row("no-way", when="tool:Bash", action="refuse", unless="--approved", line="Not without approval"))
        for _ in range(2):
            code, out, err = self.bash("push widgets")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("- Not without approval (no-way)", err)
            self.assertNotIn("one-time", err)
        self.assertSilent(self.bash("push widgets --approved"))

    def test_refuse_on_prompt_says(self):
        self.write_table(row("no-way", action="refuse", unless="approved"))
        code, out, _ = self.prompt("widgets")
        self.assertEqual(code, 0)
        self.assertIn("no-way", self.context(out))

    def test_refuse_without_unless_is_downgraded_to_once(self):
        self.write_table(row("typo", when="tool:Bash", action="refuse"))
        self.assertEqual(self.bash("do widgets")[0], 2)
        self.assertSilent(self.bash("do widgets"))

    def test_refusal_lists_say_row_that_fired_with_it(self):
        self.write_table(row("a-say", when="tool:Bash", line="Say line"),
                         row("b-refuse", when="tool:Bash", action="refuse", unless="--ok", line="Refuse line"))
        code, _, err = self.bash("do widgets")
        self.assertEqual(code, 2)
        self.assertTrue(err.index("(b-refuse)") < err.index("(a-say)"))
        # the say row counted as shown
        code, _, err = self.bash("do widgets")
        self.assertEqual(code, 2)
        self.assertNotIn("a-say", err)

    def test_refusal_does_not_mark_refuse_row(self):
        self.write_table(row("b-refuse", when="tool:Bash", action="refuse", unless="--ok"))
        self.assertEqual(self.bash("do widgets")[0], 2)
        self.assertEqual(self.bash("do widgets")[0], 2)


class TestBadInput(Base):
    def test_bad_rows_skipped_good_rows_fire(self):
        self.write_table(
            "short\tprompt\tall",
            row("bad-regex", pattern="(widgets"),
            row("bad-action", action="shout"),
            row("bad-who", who="everyone"),
            row("bad-when", when="sometimes"),
            row("Bad_Id"),
            row("empty-pattern", pattern="-"),
            row("empty-line", line="-"),
            row("dup", line="first"),
            row("dup", line="second"),
            row("good", line="Good line"))
        _, out, _ = self.prompt("widgets")
        ctx = self.context(out)
        self.assertIn("(dup)", ctx)
        self.assertIn("first", ctx)
        self.assertNotIn("second", ctx)
        self.assertIn("(good)", ctx)
        for bad in ("bad-regex", "bad-action", "bad-who", "bad-when", "Bad_Id", "empty-pattern", "empty-line"):
            self.assertNotIn(bad, ctx)

    def test_bad_regex_in_where_or_unless_skips_row(self):
        self.write_table(row("bad-where", where="("), row("bad-unless", unless="["), row("good"))
        _, out, _ = self.prompt("widgets")
        ctx = self.context(out)
        self.assertNotIn("bad-where", ctx)
        self.assertNotIn("bad-unless", ctx)
        self.assertIn("(good)", ctx)

    def test_missing_table(self):
        self.assertSilent(self.prompt("widgets"))

    def test_default_table_under_home(self):
        folder = self.home / "repos" / "hub" / "rules"
        folder.mkdir(parents=True)
        (folder / "moment-lines.tsv").write_text(row("home-row") + "\n")
        code, out, _ = self.prompt("widgets", table=False)
        self.assertIn("home-row", out)

    def test_garbage_stdin(self):
        self.write_table(row("widget-note"))
        self.assertSilent(self.run_hook(None, stdin="not json"))
        self.assertSilent(self.run_hook(None, stdin=""))
        self.assertSilent(self.run_hook(None, stdin="[1, 2]"))

    def test_other_event_names(self):
        self.write_table(row("widget-note"))
        self.assertSilent(self.run_hook({"hook_event_name": "PostToolUse", "prompt": "widgets", "tool_name": "Bash",
                                         "tool_input": {"command": "widgets"}}))
        self.assertSilent(self.run_hook({"prompt": "widgets"}))

    def test_wrong_field_types(self):
        self.write_table(row("widget-note"), row("t", when="tool:Bash"))
        self.assertSilent(self.run_hook({"hook_event_name": "UserPromptSubmit", "prompt": 5}))
        self.assertSilent(self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": "x"}))

    def test_kill_switch(self):
        self.write_table(row("widget-note"), row("r", when="tool:Bash", action="once"))
        self.assertSilent(self.prompt("widgets", env={"MOMENT_LINES": "off"}))
        self.assertSilent(self.bash("widgets", env={"MOMENT_LINES": "off"}))


class TestState(Base):
    def test_old_state_files_pruned(self):
        self.write_table(row("widget-note"))
        self.state.mkdir()
        old = self.state / "old.json"
        fresh = self.state / "fresh.json"
        old.write_text("[]")
        fresh.write_text("[]")
        past = time.time() - 4 * 86400
        os.utime(old, (past, past))
        self.prompt("widgets", session="now")
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue((self.state / "now.json").exists())

    def test_prune_removes_at_most_fifty(self):
        self.write_table(row("widget-note"))
        self.state.mkdir()
        past = time.time() - 4 * 86400
        for i in range(60):
            p = self.state / ("old%d.json" % i)
            p.write_text("[]")
            os.utime(p, (past, past))
        self.prompt("widgets", session="now")
        left = [p for p in self.state.iterdir() if p.name.startswith("old")]
        self.assertEqual(len(left), 10)

    def test_state_holds_list_of_ids(self):
        self.write_table(row("widget-note"))
        self.prompt("widgets", session="abc")
        self.assertEqual(json.loads((self.state / "abc.json").read_text()), ["widget-note"])


class TestReply(Base):
    def transcript(self, reply, name="t.jsonl"):
        path = self.tmp / name
        entries = [
            {"type": "user", "message": {"role": "user", "content": "please look at the widgets"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}},
        ]
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
        return str(path)

    def stop(self, reply="All done with the widgets", session="s1", transcript=None, **extra):
        env = extra.pop("env", None)
        p = {"hook_event_name": "Stop", "stop_hook_active": False, "cwd": "/work/acme",
             "transcript_path": transcript or self.transcript(reply)}
        if session is not None:
            p["session_id"] = session
        p.update(extra)
        return self.run_hook(p, env=env)

    def test_blocks_once_with_json_shape(self):
        self.write_table(row("reply-row", when="reply", line="Say what was not checked"))
        code, out, err = self.stop()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {
            "decision": "block",
            "reason": "Moment of need, before this reply stands:\n- Say what was not checked (reply-row)\n"
                      "Rewrite the reply to cover these."})
        self.assertSilent(self.stop())
        self.assertIn('"block"', self.stop(session="s2")[1])

    def test_any_action_blocks_once(self):
        self.write_table(row("r-say", when="reply", action="say"), row("r-refuse", when="reply", action="refuse"),
                         row("r-once", when="reply", action="once"))
        code, out, _ = self.stop()
        for rid in ("r-say", "r-refuse", "r-once"):
            self.assertIn("(%s)" % rid, out)
        self.assertSilent(self.stop())

    def test_stop_hook_active_passes(self):
        self.write_table(row("reply-row", when="reply"))
        self.assertSilent(self.stop(stop_hook_active=True))

    def test_unattended_passes(self):
        self.write_table(row("reply-row", when="reply"))
        self.assertSilent(self.stop(env={"AFA_LANE_UNATTENDED": "1"}))

    def test_no_session_id_passes(self):
        self.write_table(row("reply-row", when="reply"))
        self.assertSilent(self.stop(session=None))

    def test_unwritable_memory_passes(self):
        self.write_table(row("reply-row", when="reply"))
        self.state.write_text("a file, not a folder")
        self.assertSilent(self.stop())
        self.assertSilent(self.stop())

    def test_missing_transcript_passes(self):
        self.write_table(row("reply-row", when="reply"))
        self.assertSilent(self.stop(transcript=str(self.tmp / "nope.jsonl")))
        self.assertSilent(self.run_hook({"hook_event_name": "Stop", "session_id": "s1"}))

    def test_fenced_and_indented_matches_do_not_fire(self):
        self.write_table(row("reply-row", when="reply", pattern="secretword"))
        self.assertSilent(self.stop("Intro\n```\nsecretword\n```\nend"))
        self.assertSilent(self.stop("Intro\n~~~sh\nsecretword\n~~~\nend"))
        self.assertSilent(self.stop("Intro\n    secretword\nend"))
        self.assertSilent(self.stop("Intro\n\tsecretword\nend"))
        self.assertIn("block", self.stop("Intro\n```\ncode\n```\nsecretword outside")[1])

    def test_caret_anchors_at_line_start(self):
        self.write_table(row("reply-row", when="reply", pattern="^Done:"))
        self.assertIn("block", self.stop("First line\nDone: yes")[1])
        self.assertSilent(self.stop("First line\nnot Done: here", session="s2"))

    def test_who_hub_and_lane(self):
        self.write_table(row("hub-row", when="reply", who="hub"), row("lane-row", when="reply", who="lane"))
        out = self.stop(session="a")[1]
        self.assertIn("hub-row", out)
        self.assertNotIn("lane-row", out)
        out = self.stop(session="b", env={"AFA_LANE": "1"})[1]
        self.assertIn("lane-row", out)
        self.assertNotIn("hub-row", out)

    def test_three_line_cap(self):
        self.write_table(*[row("r%d" % i, when="reply", pattern="widgets") for i in range(1, 6)])
        out = self.stop()[1]
        self.assertEqual(out.count("\\n- "), 3)
        self.assertNotIn("(r4)", out)
        out = self.stop()[1]
        self.assertIn("(r4)", out)
        self.assertIn("(r5)", out)
        self.assertNotIn("(r1)", out)

    def test_prompt_and_tool_rows_do_not_fire_on_stop(self):
        self.write_table(row("p-row"), row("t-row", when="tool:Bash"))
        self.assertSilent(self.stop())

    def test_reply_row_does_not_fire_on_prompt_or_tool(self):
        self.write_table(row("reply-row", when="reply"))
        self.assertSilent(self.prompt("widgets"))
        self.assertSilent(self.bash("widgets"))

    def test_uses_last_reply_of_the_turn(self):
        self.write_table(row("reply-row", when="reply", pattern="final"))
        self.assertIn("block", self.stop("the final word")[1])

    def test_check_accepts_reply(self):
        self.write_table(row("reply-row", when="reply"))
        code, out, _ = self.run_hook(None, args=("--check", str(self.table)), stdin="")
        self.assertEqual((code, out), (0, "1 rows loaded, 0 problems\n"))


class TestCheck(Base):
    def check(self, *args):
        code, out, err = self.run_hook(None, args=("--check",) + args, stdin="")
        return code, out

    def test_clean_table(self):
        self.write_table(row("a"), row("b", when="tool:Bash"))
        self.assertEqual(self.check(str(self.table)), (0, "2 rows loaded, 0 problems\n"))

    def test_problems_reported_with_line_numbers(self):
        self.write_table(
            "# comment",
            row("good"),
            row("bad-regex", pattern="(x"),
            row("typo", action="refuse"),
            row("good"),
            "too\tfew")
        code, out = self.check(str(self.table))
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines(), [
            "line 3: bad regex in pattern",
            "line 4: refuse without unless, treated as once",
            "line 5: duplicate id",
            "line 6: wrong column count",
            "2 rows loaded, 4 problems"])

    def test_default_path_from_env_free_home(self):
        folder = self.home / "repos" / "hub" / "rules"
        folder.mkdir(parents=True)
        (folder / "moment-lines.tsv").write_text(row("home-row") + "\n")
        code, out, _ = self.run_hook(None, args=("--check",), stdin="", table=False)
        self.assertEqual((code, out), (0, "1 rows loaded, 0 problems\n"))

    def test_missing_table_is_a_problem(self):
        code, out = self.check(str(self.tmp / "nope.tsv"))
        self.assertEqual(code, 1)
        self.assertIn("1 problems", out)

    def test_never_prints_line_text_or_pattern(self):
        self.write_table(row("secret-id", pattern="(secretpattern", line="Secret reminder text"),
                         row("typo", action="refuse", line="Another private sentence"))
        code, out = self.check(str(self.table))
        self.assertEqual(code, 1)
        for text in ("Secret reminder", "secretpattern", "private sentence"):
            self.assertNotIn(text, out)


if __name__ == "__main__":
    unittest.main()
