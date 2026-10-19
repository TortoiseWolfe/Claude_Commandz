"""19 Oct: an ask that involves email starts a lane with Gmail on, in the repo it concerns, and never a connectors-off question lane.
Replays the two asks of 2:32 PM and 2:33 PM (the Colson email on the Tahoe, then "the Hub knows more about it"), which the 15-minute fallback
sent to workspace lanes started with --no-connectors. Also: a git worktree of a listed repo is a repo to the waiting room and the pass, with
no hub/repos.tsv refresh (3:03 PM). The PC and OpenClaw are fakes; git is real, in a temp folder."""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
from test_openclaw_dispatch import NOW, Base, FakeHost  # noqa: E402

COLSON = "need to proof read and edit the email back to lawyer  Colson on the Tahoe"
FOLLOW = "I didn't ask you to do it driectly, the Hub know more about it and it can see his former messages"
FIRST_TS, SECOND_TS = NOW - 20 * 60, NOW - 20 * 60 + 60


class MailHost(FakeHost):
    """Records the argv lane.sh would get: the real LaneHost.start_lane over a fake _run."""
    def __init__(self):
        super().__init__()
        self.argvs = []

    def _run(self, argv, timeout, cwd=None, stdin=None, env=None, timeout_result=False):
        self.argvs.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def start_lane(self, name, dirpath, engine, model, prompt_path, ask=None):
        FakeHost.start_lane(self, name, dirpath, engine, model, prompt_path, ask)
        return dp.LaneHost.start_lane(self, name, dirpath, engine, model, prompt_path, ask)


class MailBase(Base):
    def setUp(self):
        super().setUp()
        (self.root / "tahoe-case" / ".git").mkdir(parents=True)
        (self.root / "hub/repos.tsv").write_text((self.root / "hub/repos.tsv").read_text() +
                                                 "tahoe-case\tNO-REMOTE\tmain\t2026-10-19\trepo\tmain\n")
        (self.root / ".git").mkdir()
        self.ctx.repos = dp.load_repos(self.root / "hub/repos.tsv", self.root)
        self.host = MailHost()

    def history(self, last):
        return [{"who": "hub", "text": "### harmonicegg-mail\nreport written", "ts": FIRST_TS - 3000},
                {"who": "Jonathan", "text": COLSON, "ts": FIRST_TS},
                {"who": "Receptionist", "text": "This is a personal email draft about the Tahoe.", "ts": FIRST_TS + 2},
                {"who": "hub", "text": "On the hub's list: the hub has your message.", "ts": FIRST_TS + 46}] + last

    def defer(self, ask_id, words, context):
        state = self.state()
        state.setdefault("asks", {})[ask_id] = {
            "kind": "question", "task": words, "created": NOW - 1800, "deadline": NOW - 1500, "status": "deferred",
            "deferred_at": NOW - 16 * 60, "lanes": []}
        dp.save_state(self.ctx.state_path, state)
        dp.save_context(self.ctx, ask_id, context)

    def fallback(self):
        out = dp.fallback_due(self.ctx, self.host, NOW)
        self.assertEqual(len(self.host.started), 1, out)
        argv = self.host.argvs[-1]
        return self.host.started[0], argv, Path(self.host.started[0][4]).read_text()


class TheColsonAsksOf19Oct(MailBase):
    def check_gmail_lane(self, started, argv, prompt):
        _lane, directory, engine, _model, _prompt = started
        self.assertEqual((directory, engine), (str(self.root / "tahoe-case"), "claude"))  # the repo it concerns, not the workspace
        self.assertIn("--connectors", argv)
        self.assertNotIn("--no-connectors", argv)
        self.assertIn("Gmail is on for this lane", prompt)
        self.assertNotIn("Do not use connectors", prompt)
        self.assertIn(COLSON, prompt)

    def test_the_2_32_pm_ask_gets_a_gmail_lane_in_tahoe_case(self):
        self.defer("oc-c0559501", COLSON, {"his_ts": FIRST_TS, "history": self.history([])[:3]})
        self.check_gmail_lane(*self.fallback())

    def test_the_2_33_pm_follow_up_with_no_mail_words_gets_the_same(self):
        self.assertFalse(dp.MAIL_ASK_RE.search(FOLLOW))
        hist = self.history([{"who": "Jonathan", "text": FOLLOW, "ts": SECOND_TS}])
        self.defer("oc-54871b1c", FOLLOW, {"his_ts": SECOND_TS, "history": hist})
        started, argv, prompt = self.fallback()
        self.check_gmail_lane(started, argv, prompt)
        self.assertIn(FOLLOW, prompt)

    def test_a_mail_ask_with_no_repo_named_gets_gmail_in_the_workspace(self):
        self.defer("oc-aaaaaaa1", "reply to the email from the plumber", {"his_ts": NOW - 1800, "history": []})
        started, argv, _prompt = self.fallback()
        self.assertEqual(started[1], str(self.root))
        self.assertIn("--connectors", argv)

    def test_the_pass_start_and_the_script_start_turn_gmail_on_for_a_mail_task(self):
        self.ctx.hand_off_first = False
        kind_line = dp.do_start(self.ctx, self.host, "tahoe-case", "proofread the email to the lawyer", NOW)
        self.assertIn(" | started ", kind_line[0])
        self.assertIn("--connectors", self.host.argvs[-1])
        self.assertEqual(self.host.started[-1][2], "claude")
        self.assertFalse(self.host.force_connectors_on)  # put back after the start

    def test_the_prompt_the_pass_reads_carries_his_rule_and_the_mail_rule(self):
        self.assertIn("I don't have tokens to waste on you sending things to the wrong lane", dp.PASS_PROMPT)
        self.assertIn("Gmail is on for this lane", dp.MAIL_RULES)


class ANonMailAskStaysConnectorsOff(MailBase):
    def test_the_fallback_answer_lane_stays_in_the_workspace_with_connectors_off(self):
        self.defer("oc-eeeeeeee", "Which branch contains the sample fix?", {"his_ts": NOW - 1800, "history": []})
        started, argv, prompt = self.fallback()
        self.assertEqual(started[1], str(self.root))
        self.assertIn("--no-connectors", argv)
        self.assertNotIn("--connectors", argv)
        self.assertIn("Do not use connectors", prompt)

    def test_a_script_start_for_a_code_task_gets_no_connectors_flag(self):
        self.ctx.hand_off_first = False
        line, _chat = dp.do_start(self.ctx, self.host, "own-app", "count the open issues", NOW)
        self.assertIn(" | started ", line)
        self.assertFalse([a for a in self.host.argvs[-1] if "connectors" in a])
        self.assertNotIn("Gmail is on", Path(self.host.started[-1][4]).read_text())

    def test_code_tasks_that_only_mention_email_are_not_mail_asks(self):
        for task in ("count the open issues", "fix the email validation in the signup form", "check the email regex", "add an email field to the form",
                     "rename the email address column", "the email template renders wrong"):
            self.assertFalse(dp.mail_ask(task), task)

    def test_an_email_message_from_hours_ago_does_not_make_a_new_ask_mail(self):
        ctx = {"his_ts": NOW, "history": [{"who": "Jonathan", "text": COLSON, "ts": NOW - 3 * 3600},
                                          {"who": "Jonathan", "text": "count the open issues", "ts": NOW}]}
        self.assertFalse(dp.mail_ask("count the open issues", ctx))
        ctx["history"][0]["ts"] = NOW - 600
        self.assertTrue(dp.mail_ask("count the open issues", ctx))


class AWorktreeOfAListedRepoIsARepo(MailBase):
    def setUp(self):
        super().setUp()
        repo = self.root / "own-app"
        for argv in (["init", "-q", "-b", "main"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", "-C", str(repo)] + argv, check=True, capture_output=True)
        self.wt = self.root / ".worktrees" / "own-app--porch"
        self.wt.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "porch", str(self.wt)], check=True, capture_output=True)

    def test_vet_start_accepts_a_worktree_made_after_the_manifest_was_written(self):
        problem, hit = dp.vet_start(self.ctx, self.wt, "claude", "sonnet")
        self.assertIsNone(problem)
        self.assertEqual((hit[0], Path(hit[1]).resolve()), ("own-app", self.wt.resolve()))  # the parent's name, the worktree's folder

    def test_a_worktree_of_a_client_repo_keeps_the_clients_rules(self):
        client = self.root / "client-app"
        subprocess.run(["git", "-C", str(client), "init", "-q", "-b", "main"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(client), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"], check=True, capture_output=True)
        wt = self.root / ".worktrees" / "client-app--x"
        subprocess.run(["git", "-C", str(client), "worktree", "add", "-q", "-b", "x", str(wt)], check=True, capture_output=True)
        problem, _hit = dp.vet_start(self.ctx, wt, "copilot", "sonnet")
        self.assertIn("runs on Claude only", problem)

    def test_a_worktree_of_a_repo_not_on_the_list_and_a_plain_folder_are_still_refused(self):
        other = self.tmp / "unlisted"
        other.mkdir()
        subprocess.run(["git", "-C", str(other), "init", "-q", "-b", "main"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(other), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"], check=True, capture_output=True)
        wt = self.tmp / "unlisted-wt"
        subprocess.run(["git", "-C", str(other), "worktree", "add", "-q", "-b", "y", str(wt)], check=True, capture_output=True)
        for d in (wt, other, self.root / ".worktrees", self.wt / "sub", "/etc"):
            Path(d).mkdir(exist_ok=True) if str(d).startswith(str(self.tmp)) else None
            problem, hit = dp.vet_start(self.ctx, d, "claude", "sonnet")
            self.assertIn("not a repo on the list", problem or "", str(d))
            self.assertIsNone(hit)

    def test_the_waiting_room_starts_an_entry_whose_folder_is_a_new_worktree(self):
        room = self.ctx.room_dir
        self.host.proj = 50  # the week is on pace
        room.mkdir(parents=True, exist_ok=True)
        (room / "tigard-porch-finish.md").write_text("dir: %s\nmodel: sonnet\napproved: x\n\nfinish the porch\n" % self.wt)
        out = dp.room_due(self.ctx, self.host, NOW)
        self.assertEqual(len(self.host.started), 1, out)
        self.assertEqual(self.host.started[0][1], str(self.wt))


if __name__ == "__main__":
    unittest.main()
