"""19 Oct 11:59 PM: he-lift-move watched a 35-minute download with Monitor. Each progress line started a 2-second turn and lane_watch.sh woke the
hub for every one. lane_turn_kind.py tells such a turn (a Monitor event, answered with no question) from a real end of turn. The entries below
have the shapes of that lane's transcript."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lane_turn_kind as ltk  # noqa: E402

MONITOR = '<task-notification>\n<task-id>bny3f7dpg</task-id>\n<summary>Monitor event: "LiFT backup zip download progress"</summary>\n<event>downloaded 10% (1642 MiB) 11:59PM</event>\n</task-notification>'
JOB_DONE = '<task-notification>\n<task-id>bxnrx3xfx</task-id>\n<status>completed</status>\n<summary>Background command "Wait for the backup download" completed</summary>\n</task-notification>'


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def result():
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}


def said(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def used(name):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": name, "input": {}}]}}


def kind(*entries):
    return ltk.turn_kind([json.dumps(e) for e in entries])


class TurnKind(unittest.TestCase):
    def test_monitor_tick_answered_with_a_status_line(self):
        self.assertEqual(kind(user("start"), said("ok"), user(MONITOR), said("Download is at 10% (1.6 GB). Still waiting.")), "monitor")

    def test_monitor_tick_that_ran_a_tool_and_asked_nothing(self):
        self.assertEqual(kind(user(MONITOR), used("Bash"), result(), said("Checked the file size; fine.")), "monitor")

    def test_monitor_tick_that_asks_a_question_is_real(self):
        self.assertEqual(kind(user(MONITOR), said("The download failed at 40%. Should I restart it?")), "real")

    def test_monitor_tick_that_opens_a_dialog_is_real(self):
        self.assertEqual(kind(user(MONITOR), used("AskUserQuestion")), "real")

    def test_finished_background_command_is_real(self):
        self.assertEqual(kind(user(JOB_DONE), said("The button stopped spinning without a file.")), "real")

    def test_a_typed_turn_is_real(self):
        self.assertEqual(kind(user("Merge it"), said("Merged.")), "real")

    def test_a_typed_turn_after_ticks_is_real(self):
        self.assertEqual(kind(user(MONITOR), said("10%"), user("how long left?"), said("About 20 minutes.")), "real")

    def test_content_list_form_of_the_notification(self):
        text = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": MONITOR}]}}
        self.assertEqual(kind(text, said("20%")), "monitor")

    def test_sidechain_entries_are_not_the_lane(self):
        side = {**said("Is this ok?"), "isSidechain": True}
        self.assertEqual(kind(user(MONITOR), side, said("30%")), "monitor")

    def test_no_input_at_all_is_unknown(self):
        self.assertEqual(kind(said("hello")), "?")
        self.assertEqual(ltk.turn_kind([]), "?")

    def test_garbage_lines_are_skipped(self):
        self.assertEqual(ltk.turn_kind(["not json", json.dumps(user(MONITOR)), "{", json.dumps(said("40%"))]), "monitor")

    def test_command_line(self):
        import subprocess, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "t.jsonl")
            Path(f).write_text("\n".join(json.dumps(e) for e in (user(MONITOR), said("50%"))) + "\n")
            out = subprocess.run([sys.executable, "-I", str(Path(ltk.__file__)), f], capture_output=True, text=True)
            self.assertEqual(out.stdout.strip(), "monitor")
            out = subprocess.run([sys.executable, "-I", str(Path(ltk.__file__)), os.path.join(d, "missing")], capture_output=True, text=True)
            self.assertEqual(out.stdout.strip(), "?")


if __name__ == "__main__":
    unittest.main()
