"""frontdesk-7: a lane is found by any name it has (exact name, ask id, 8-hex or date suffix, repo), an ambiguous word is refused with the
candidates listed, the start line names the lane, "stop" and "close" stop a running job, and the "lanes" marker answers from the state
file, the index and tmux, never from memory. The PC and OpenClaw are fakes."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
import openclaw_pickup as pk  # noqa: E402
from test_openclaw_dispatch import NOW, SHA  # noqa: E402
from test_openclaw_pass import ASK, PassBase  # noqa: E402

LANE = "oc-workspace-40816152"  # 16 Oct 22:28: an all-digit ask tail, the lane the receptionist could not close


class Lanes(PassBase):
    def put(self, name, repo="own-app", status="started", ask=None, engine="claude", started=NOW, report_text=None, task="do the thing", **extra):
        """A lane in the dispatcher's state file, with a prompt.md the way TEMPLATE writes it."""
        hand = self.tmp / "lanes" / ("2026-10-16-%s" % name)
        hand.mkdir(parents=True, exist_ok=True)
        report = hand / "report.md"
        (hand / "prompt.md").write_text(dp.TEMPLATE.format(repo=repo, dir="/x", task=dp.json.dumps(task), report=report, work_rule="w", extra_rules=""))
        if report_text is not None:
            report.write_text(report_text)
        state = self.state()
        lane = {"repo": repo, "engine": engine, "model": "sonnet", "started": started, "status": status, "report": str(report)}
        if ask:
            lane["ask"] = ask
        lane.update(extra)
        state["lanes"][name] = lane
        dp.save_state(self.ctx.state_path, state)
        return name

    def find(self, word):
        return dp.find_lanes(word, self.state())

    def chat(self):
        return self.host.chats[-1]


class FindingALane(Lanes):
    def test_an_exact_name_in_any_case(self):
        self.put(LANE)
        self.assertEqual(self.find("OC-Workspace-40816152"), [LANE])

    def test_an_ask_id_finds_the_lane_the_ask_produced(self):
        self.put(LANE)
        state = self.state()
        dp.asks_of(state)["oc-40816152"] = {"lanes": [LANE], "status": "claimed"}
        dp.save_state(self.ctx.state_path, state)
        self.assertEqual(self.find("oc-40816152"), [LANE])
        self.assertEqual(dp.resolve_lane("oc-40816152", self.state()), LANE)

    def test_an_ask_id_still_finds_its_lane_after_the_ask_record_is_gone(self):
        self.put(LANE, ask="oc-40816152")
        self.assertEqual(self.find("oc-40816152"), [LANE])

    def test_an_all_digit_eight_hex_suffix_finds_the_lane(self):
        self.put(LANE)
        self.assertEqual(self.find("40816152"), [LANE])

    def test_a_date_suffix_finds_the_lane(self):
        self.put("oc-runit-1016-2103")
        self.assertEqual(self.find("1016-2103"), ["oc-runit-1016-2103"])

    def test_a_suffix_on_two_lanes_is_ambiguous_and_lists_both(self):
        self.put("oc-runit-1016-2103")
        self.put("oc-docs-1016-2103")
        self.assertEqual(self.find("1016-2103"), ["oc-docs-1016-2103", "oc-runit-1016-2103"])
        self.assertIsNone(dp.resolve_lane("1016-2103", self.state()))
        self.act("close 1016-2103")
        self.assertIn("could mean 2 lanes: oc-docs-1016-2103, oc-runit-1016-2103", self.host.chats[-1])
        self.assertEqual(self.host.killed, [])
        self.assertEqual(self.state()["lanes"]["oc-docs-1016-2103"]["status"], "started")

    def test_a_repo_with_one_open_lane_is_found_and_with_two_is_refused_by_name(self):
        self.put("oc-own-app-1")
        self.assertEqual(self.find("own-app"), ["oc-own-app-1"])
        self.put("oc-own-app-2")
        self.assertEqual(self.find("own-app"), ["oc-own-app-1", "oc-own-app-2"])
        self.act("reply own-app: hi")
        self.assertEqual(self.host.sent, [])
        self.assertIn("could mean 2 lanes: oc-own-app-1, oc-own-app-2", self.host.chats[-1])

    def test_a_repo_word_finds_open_lanes_only_not_closed_ones(self):
        self.put("oc-own-app-1", status="closed")
        self.assertEqual(self.find("own-app"), [])
        self.put("oc-own-app-2")
        self.assertEqual(self.find("own-app"), ["oc-own-app-2"])

    def test_an_exact_name_beats_a_looser_rule(self):
        self.put("oc-own-app-1016-2103")
        self.put("1016-2103")  # a lane whose whole name is the date form
        self.assertEqual(self.find("1016-2103"), ["1016-2103"])

    def test_a_word_that_is_nothing_finds_nothing_and_the_open_lanes_are_listed(self):
        self.put(LANE)
        self.assertEqual(self.find("deadbeef"), [])
        self.act("close deadbeef")
        self.assertIn("I don't have a lane called \"deadbeef\"", self.chat())
        self.assertIn(LANE, self.chat())

    def test_a_suffix_cannot_match_the_middle_of_a_name(self):
        self.put("oc-runit-40816152-9")
        self.assertEqual(self.find("40816152"), [])

    def test_close_by_ask_id_stops_a_running_lane(self):
        self.put(LANE, ask="oc-40816152")
        self.host.wins.add(LANE)
        kind, line = self.act("close oc-40816152")
        self.assertEqual(kind, "dispatch")
        self.assertEqual(self.host.killed, [LANE])
        self.assertEqual(self.state()["lanes"][LANE]["status"], "closed")
        self.assertTrue(self.state()["lanes"][LANE]["failed"])  # stopped: the same task may be asked again
        self.assertEqual(self.chat(), "Stopped %s." % LANE)

    def test_close_by_short_id_closes_a_lane_that_has_a_report(self):
        self.put(LANE, report_text="Did it. Saw it.")
        self.host.wins.add(LANE)
        self.act("close 40816152")
        self.assertEqual(self.host.killed, [LANE])
        self.assertEqual(self.chat(), "Closed %s." % LANE)

    def test_reply_by_ask_id_reaches_the_lane(self):
        self.put(LANE, ask="oc-40816152")
        self.act("reply oc-40816152: go on")
        self.assertEqual([n for n, _ in self.host.sent], [LANE])

    def test_stop_stops_the_newest_open_lane(self):
        self.put("oc-old-1", started=NOW - 100)
        self.put(LANE, started=NOW)
        self.host.wins.update({"oc-old-1", LANE})
        self.act("stop")
        self.assertEqual(self.host.killed, [LANE])
        self.assertEqual(self.state()["lanes"]["oc-old-1"]["status"], "started")


class TheStartLineNamesTheLane(Lanes):
    def test_a_claim_with_a_host_posts_the_lane_name_and_how_to_stop_it(self):
        self.hand(task="count the open issues")
        self.index_row("oc-own-app-%s" % ASK[3:])
        ok, msg = dp.claim(self.ctx, ASK, "oc-own-app-%s" % ASK[3:], NOW + 5, self.host)
        self.assertTrue(ok, msg)
        self.assertEqual(self.host.chats, [])  # a start line is progress: the log, not his chat
        line = self.host.notes[-1]
        self.assertTrue(line.startswith("oc-own-app-%s started: count the open issues." % ASK[3:]), line)
        self.assertIn("Say stop to cancel.", line)

    def test_a_refused_claim_posts_nothing(self):
        self.hand()
        n = len(self.host.chats)
        self.assertFalse(dp.claim(self.ctx, ASK, "oc-ghost", NOW, self.host)[0])
        self.assertEqual(len(self.host.chats), n)

    def test_the_start_for_ask_path_announces_the_lane_once(self):
        self.hand(task="count the open issues")
        lane = "oc-own-app-aaa"
        dp.write_prompt(self.ctx, lane, "count the open issues\n", NOW)
        real_start = self.host.start_lane

        def start_lane(name, dirpath, engine, model, prompt_path, ask=None):
            ok = real_start(name, dirpath, engine, model, prompt_path, ask=ask)
            if ok[0]:  # what lane.sh does: one row in the lane index
                with open(self.index, "a") as f:
                    f.write("\t".join(["2026-10-16", name, str(dirpath), model, "subscription", "-", str(self.tmp / "lanes" / name / "report.md"), "s", "-"]) + "\n")
            return ok
        self.host.start_lane = start_lane
        prompt = self.ctx.lanes_dir / ("%s-%s" % (time.strftime("%F", time.localtime(NOW)), lane)) / "prompt.md"
        ok, msg = dp.start_for_ask(self.ctx, self.host, ask_id=ASK, lane_name=lane, dirpath=self.root / "own-app", model="sonnet", prompt_path=prompt, now=NOW + 5)
        self.assertTrue(ok, msg)
        started = [c for c in self.host.notes if c.startswith(lane + " started")]
        self.assertEqual(len(started), 1, self.host.notes)
        self.assertEqual(self.host.chats, [])
        self.assertIn("Say stop to cancel.", started[0])


class TheLanesMarker(Lanes):
    def test_the_two_words_parse_only_as_whole_short_messages(self):
        for t in ("lanes", "Lanes!", "list lanes", "open lanes."):
            self.assertEqual(dp.parse(t), ("lanes",), t)
        for t in ("lanes please tell me everything about them", "the lanes"):
            self.assertIsNone(dp.parse(t), t)

    def test_nothing_open_says_so(self):
        kind, line = self.act("lanes")
        self.assertEqual(kind, "dispatch")
        self.assertEqual(self.chat(), "No lanes are open right now.")

    def bullets(self):
        return [l for l in self.chat().splitlines() if l.startswith("- ")]

    def test_each_live_lane_is_a_bullet_and_dead_ones_are_one_line(self):
        self.put(LANE, repo="workspace", ask="oc-40816152", task="create or update the session-prime roadmap for each repo under ~/repos tracking what is next",
                 started=NOW - 600)
        self.put("oc-own-app-0115-0300", report_text="Did it. Saw it. Nothing left.", task="count the issues")
        self.put("oc-docs-1", repo="docs", report_text="I could not complete the scan. The command was denied.")
        self.put("oc-old-2", status="dead", detail="gone")
        self.put("oc-shut-3", status="closed")
        self.host.wins.update({LANE, "oc-own-app-0115-0300", "oc-docs-1"})
        self.act("lanes")
        text = self.chat()
        self.assertNotIn("|", text)
        self.assertNotIn("(trimmed)", text)
        self.assertIn("Open lanes (3)", text)
        by = {b.split("**")[1]: b for b in self.bullets()}
        self.assertEqual(sorted(by), sorted([LANE, "oc-own-app-0115-0300", "oc-docs-1"]))
        self.assertIn("workspace", by[LANE])
        self.assertIn("running", by[LANE])
        self.assertRegex(by[LANE], r"\b\d{1,2}:\d{2} (AM|PM)\b")
        self.assertIn("create or update the session-prime roadmap for each repo", by[LANE])
        self.assertIn("report written", by["oc-own-app-0115-0300"])
        self.assertIn("failed", by["oc-docs-1"])
        self.assertNotIn("oc-old-2", text)  # a dead lane is counted, never listed
        self.assertIn("1 older lane has ended.", text)
        self.assertNotIn("oc-shut-3", text)

    def test_many_dead_lanes_are_one_counted_line(self):
        for i in range(14):
            self.put("oc-gone-%d" % i)
        self.put(LANE)
        self.host.wins.add(LANE)
        self.act("lanes")
        self.assertIn("14 older lanes have ended.", self.chat())
        self.assertEqual(len(self.bullets()), 1)
        self.assertNotIn("oc-gone", self.chat())

    def test_a_long_task_is_cut_at_a_word_with_no_trimmed_tail(self):
        self.put(LANE, task="word " * 100)
        self.host.wins.add(LANE)
        self.act("lanes")
        bullet = self.bullets()[0]
        self.assertLess(len(bullet), 220)
        self.assertNotIn("(trimmed)", bullet)
        self.assertTrue(bullet.rstrip().endswith("\u2026") or bullet.rstrip().endswith("."), bullet)
        self.assertNotIn("wor\u2026", bullet)

    def test_a_lane_with_no_window_and_no_report_is_counted_dead_not_listed(self):
        self.put(LANE)
        self.act("lanes")
        self.assertEqual(self.bullets(), [])
        self.assertIn("No lanes are running right now.", self.chat())
        self.assertIn("1 older lane has ended.", self.chat())

    def test_a_hub_lane_in_tmux_comes_from_the_index_and_its_prompt(self):
        prompt = self.tmp / "hub-prompt.md"
        prompt.write_text("You are a lane (not the hub).\n\n# Lane frontdesk-7: the receptionist can name, list and stop its own jobs\n")
        report = self.tmp / "lanes/2026-10-16-frontdesk-7/report.md"
        with open(self.index, "w") as f:
            f.write("\t".join(["2026-10-16", "frontdesk-7", str(self.root / "own-app"), "sonnet", "subscription", str(prompt), str(report), "s",
                               str(int(NOW - 60)), "high", "off"]) + "\n")
        self.host.wins.add("frontdesk-7")
        self.act("lanes")
        line = self.bullets()[0]
        self.assertTrue(line.startswith("- **frontdesk-7**"), line)
        self.assertIn("own-app", line)
        self.assertIn("running", line)
        self.assertIn("the receptionist can name, list and stop its own jobs", line)
        self.assertNotIn("frontdesk-7:", line.split("**", 2)[2])

    def test_a_hub_lane_that_wrote_its_report_is_report_written(self):
        report = self.tmp / "lanes/2026-10-16-frontdesk-7/report.md"
        report.parent.mkdir(parents=True)
        report.write_text("All done.")
        with open(self.index, "w") as f:
            f.write("\t".join(["2026-10-16", "frontdesk-7", str(self.root / "own-app"), "sonnet", "subscription", "-", str(report), "s",
                               str(int(NOW - 60)), "high", "off"]) + "\n")
        self.host.wins.add("frontdesk-7")
        self.act("lanes")
        self.assertIn("**frontdesk-7**", self.chat())
        self.assertIn("report written", self.chat())

    def test_a_window_nobody_indexed_is_still_listed(self):
        self.host.wins.add("scratch")
        self.act("lanes")
        self.assertTrue(self.bullets()[0].startswith("- **scratch**"))
        self.assertIn("running", self.bullets()[0])

    def test_more_than_twenty_are_cut_with_a_count(self):
        for i in range(23):
            self.put("oc-many-%d" % i, started=NOW + i)
            self.host.wins.add("oc-many-%d" % i)
        _line, text = dp.do_lanes(self.ctx, self.host, NOW)
        self.assertIn("(23)", text)
        self.assertEqual(len([l for l in text.splitlines() if l.startswith("- ")]), 20)
        self.assertIn("and 3 more", text)

    def test_the_listing_changes_nothing(self):
        self.put(LANE)
        before = self.state()
        self.act("lanes")
        self.assertEqual(self.state(), before)
        self.assertEqual(self.host.killed, [])

    def test_only_a_tray_message_can_ask(self):
        kind, line = self.act("lanes", source="discord")
        self.assertEqual((kind, line), ("ignored", None))

    def test_the_cli_prints_the_same_list(self):
        self.put(LANE)
        self.host.wins.add(LANE)
        _line, chat = dp.do_lanes(self.ctx, self.host, NOW)
        self.assertIn(LANE, chat)


class TheWorkspaceRoadmapException(unittest.TestCase):
    def test_a_roadmap_post_is_the_one_write_a_workspace_lane_may_make(self):
        self.assertIn("session_prime_facts.py --post", dp.WORKSPACE_RULES)
        self.assertIn("nothing else that writes", dp.WORKSPACE_RULES)
        self.assertIn("any other change", dp.WORKSPACE_RULES)

    def test_the_rule_reaches_the_lane_prompt(self):
        self.assertIn("session_prime_facts.py --post", dp.TEMPLATE.format(repo="workspace", dir="/x", task="t", report="r", work_rule="w", extra_rules=dp.WORKSPACE_RULES))


if __name__ == "__main__":
    unittest.main()
