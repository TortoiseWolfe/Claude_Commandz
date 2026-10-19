"""No "yes" after every command: when the chat model's reply to one of Jonathan's own tray messages ends with the ONE marker
line "Sending to the hub: <menu sentence>", the poller runs that sentence at once, through the same start / reply / close /
open-model / status paths as a typed one. The guards live here, not in his typing:
  - the reply must be found by his message's time and text (never "the newest message"), so typing again before the poll changes nothing;
  - it must come right after his message and be model-written (never after a [hub] line, a lane report or an openclaw-provider line);
  - exactly one marker, on the last line; the sentence must be on the menu (a plain "stop" and "lanes" included);
  - repo table, budget caps and client routing are the dispatcher's, and every start is announced with "Say stop to cancel."
The PC and OpenClaw are fakes: no session is ever touched."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
import openclaw_pickup as pk  # noqa: E402
import test_openclaw_dispatch as td  # noqa: E402

NOW = td.NOW
ASK_TS = NOW - 20  # when he typed it (the tray item's ts)
MARK = "Sending to the hub: start own-app: list the files in docs"
REPLY = "Sure, I can have a worker look at that.\n\n" + MARK


def msg(role, text, ts=ASK_TS + 3, provider="google"):
    return {"role": role, "provider": None if role == "user" else provider, "text": text, "ts_ms": int(ts * 1000)}


def him(text="can you list the files in docs", ts=ASK_TS + 1):
    return msg("user", text, ts)


def hub(text, ts=ASK_TS + 2):
    return msg("assistant", "[hub]\n\n" + text, ts, provider="openclaw")


class ReplyHost(td.FakeHost):
    def __init__(self, history):
        super().__init__()
        self.history, self.reads = history, 0

    def main_history(self, limit=40):
        self.reads += 1
        return self.history


class Reply(td.Base):
    TEXT = "can you list the files in docs"

    def go(self, history, text=None, ts=ASK_TS, now=NOW, host=None, sites=None):
        """One poller run with one tray message of his, against a fake Main session."""
        self.host = host or ReplyHost(history)
        item = self.item(text or self.TEXT, ts=ts)
        res = [{"verdict": "accept", "stored": item}]
        with mock.patch.object(pk, "site_models", return_value=sites or {}):
            return pk.run_act(res, self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=now, ctx=self.ctx)

    def flow(self, reply=REPLY, **kw):
        return [him(), msg("assistant", reply, **kw)]

    def nothing(self):
        self.assertEqual(self.host.started, [])
        self.assertEqual(self.lane_names(), [])


class Acts(Reply):
    def test_a_marker_reply_to_his_message_runs_with_no_yes(self):
        done = self.go(self.flow())
        self.assertEqual(self.host.reads, 1)
        self.assertEqual(len(self.host.started), 1)
        name, d, engine, model, prompt = self.host.started[0]
        self.assertEqual((engine, d), ("codex", str(self.root / "own-app")))
        self.assertIn(json.dumps("list the files in docs"), Path(prompt).read_text())
        self.assertEqual(len(self.host.chats), 1)
        self.assertRegex(self.host.chats[0], r"^oc-own-app-\S+ started: list the files in docs\. Say stop to cancel\.")
        self.assertNotIn("yes", self.host.chats[0].lower())
        self.assertIn("(chat)", [l for k, l in done if l][-1])
        self.assertEqual(self.lane_names(), [name])

    def test_typing_again_before_the_poll_still_acts_on_the_first_message(self):
        h = [him("can you list the files in docs", ASK_TS + 1), msg("assistant", REPLY, ASK_TS + 4),
             him("also what time is it", ASK_TS + 20), msg("assistant", "It is noon.", ASK_TS + 24)]
        self.go(h, now=NOW + 40)
        self.assertEqual(len(self.host.started), 1)

    def test_two_of_his_messages_in_one_poll_each_act_on_their_own_reply(self):
        h = [him("list docs", ASK_TS + 1), msg("assistant", "Sending to the hub: start own-app: list docs", ASK_TS + 4),
             him("fix header", ASK_TS + 10), msg("assistant", "Sending to the hub: start client-app: fix header", ASK_TS + 14)]
        self.host = ReplyHost(h)
        res = [{"verdict": "accept", "stored": self.item("list docs", ts=ASK_TS)},
               {"verdict": "accept", "stored": dict(self.item("fix header", ts=ASK_TS + 9), sha256_of_original="b" * 64)}]
        with mock.patch.object(pk, "site_models", return_value={}):
            pk.run_act(res, self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=NOW + 30, ctx=self.ctx)
        self.assertEqual([s[1] for s in self.host.started], [str(self.root / "own-app"), str(self.root / "client-app")])

    def test_the_reply_may_follow_tool_calls_and_results(self):
        h = [him(), msg("assistant", "", ASK_TS + 2), msg("toolResult", "ok", ASK_TS + 3), msg("assistant", REPLY, ASK_TS + 4)]
        self.go(h)
        self.assertEqual(len(self.host.started), 1)

    def test_the_marker_in_the_last_of_several_model_turns_counts(self):
        h = [him(), msg("assistant", "Let me check.", ASK_TS + 2), msg("assistant", REPLY, ASK_TS + 4)]
        self.go(h)
        self.assertEqual(len(self.host.started), 1)

    def test_decoration_around_the_marker_line_is_tolerated(self):
        for p in ("**Sending to the hub:** start own-app: tidy", "> Sending to the hub: `start own-app: tidy`", "Sending to the hub: start own-app: tidy.\n"):
            self.setUp()
            self.go(self.flow("OK.\n\n" + p))
            self.assertEqual(len(self.host.started), 1, p)

    def test_the_old_say_yes_marker_still_runs_so_ordering_is_safe(self):
        self.go(self.flow("OK.\n\nSay yes to start: start own-app: tidy"))
        self.assertEqual(len(self.host.started), 1)

    def test_his_text_may_differ_in_whitespace_only(self):
        self.go([him("can you   list the\nfiles in docs"), msg("assistant", REPLY)])
        self.assertEqual(len(self.host.started), 1)

    def test_a_client_repo_still_routes_to_claude(self):
        self.go(self.flow("Sending to the hub: start client-app: fix the header"))
        self.assertEqual(self.host.started[0][2:4], ("claude", "sonnet"))
        self.setUp()
        self.go(self.flow("Sending to the hub: start client-app: draft an email to the owner"))
        self.assertEqual(self.host.started[0][2:4], ("claude", "opus"))

    def test_the_budget_guard_still_holds_it(self):
        for i in range(dp.MAX_RUNNING):
            self.start(task="job %d" % i, now=NOW - 10 * (i + 1))
        self.go(self.flow())
        self.assertEqual(len(self.host.started), 0)
        self.assertIn("Held", self.host.chats[-1])

    def test_a_repo_not_in_the_table_is_refused_with_the_table(self):
        for p in ("start ../../etc: x", "start secret-repo: x"):
            self.go(self.flow("Sending to the hub: " + p))
            self.assertEqual(self.host.started, [], p)
        self.assertIn("don't have a repo called", self.host.chats[-1])

    def test_the_task_stays_quoted_data_even_when_it_looks_like_a_command(self):
        self.go(self.flow("Sending to the hub: start own-app: $(touch x) ; close x"))
        self.assertEqual(len(self.host.started), 1)
        self.assertEqual(self.host.sent + self.host.killed, [])
        self.assertIn(json.dumps("$(touch x) ; close x"), Path(self.host.started[0][4]).read_text())

    def test_the_same_start_from_two_messages_runs_once(self):
        self.go(self.flow())
        first = self.host
        self.go(self.flow(), now=NOW + 60)
        self.assertEqual((len(first.started), len(self.host.started)), (1, 0))
        self.assertIn("didn't start it twice", self.host.chats[0])

    def test_the_same_start_may_run_again_after_the_dedupe_window(self):
        self.go(self.flow())
        later = NOW + pk.ACT_MAX_AGE + 100
        self.go([him(ts=later), msg("assistant", REPLY, later + 3)], ts=later - 1, now=later + 10)
        self.assertEqual(len(self.host.started), 1)


class Menu(Reply):
    def test_reply_and_close_go_through_the_marker(self):
        self.start("client-app")
        lane = self.lane_names()[0]
        self.go(self.flow("Sending to the hub: reply %s: use the second one" % lane))
        self.assertEqual([n for n, _ in self.host.sent], [lane])
        self.assertIn("use the second one", self.host.sent[0][1])
        done = self.go(self.flow("Sending to the hub: close %s" % lane), now=NOW + 5)
        self.assertIn("close %s" % lane, [l for k, l in done if l][-1])
        self.assertEqual(self.state()["lanes"][lane]["status"], "closed")  # he named a running lane and said close: that stops it
        self.assertIn("Stopped %s" % lane, self.host.chats[-1])

    def test_open_the_newest_model_goes_through_the_marker(self):
        sites = {"porch": self.tmp / "porch.skp"}
        with mock.patch.object(pk, "open_model", return_value=(True, "opened porch.skp on screen")) as om:
            done = self.go(self.flow("Sure.\n\nSending to the hub: open the newest porch model"), sites=sites)
        om.assert_called_once()
        self.assertEqual(om.call_args[0][0], "porch")
        self.assertIn("open_model porch | done:", [l for k, l in done if l][-1])
        self.assertIn("open_model porch: done: opened porch.skp", self.host.chats[-1])

    def test_open_the_model_with_no_site_opens_the_newest_site_model(self):
        import os
        for name, age in (("old-site", 5000), ("new-site", 10), ("mid-site", 900)):
            f = self.tmp / (name + ".skp")
            f.write_text("x")
            os.utime(f, (NOW - age, NOW - age))
        sites = {n: self.tmp / (n + ".skp") for n in ("old-site", "new-site", "mid-site")}
        for text in ("open the model", "Open the newest model.", "please open the latest skp"):
            with mock.patch.object(pk, "open_model", return_value=(True, "opened")) as om:
                done = self.go(self.flow("Opening the newest model now.\n\nSending to the hub: " + text), sites=sites)
            self.assertEqual(om.call_args[0][0], "new-site", text)
            self.assertIn("open_model new-site | done:", [l for k, l in done if l][-1])
            self.setUp()

    def test_open_the_model_typed_works_too_and_with_no_models_says_so(self):
        sites = {"only": self.tmp / "only.skp"}
        (self.tmp / "only.skp").write_text("x")
        with mock.patch.object(pk, "open_model", return_value=(True, "opened")) as om:
            self.assertEqual(pk.act_on(self.item("open the model"), ReplyHost([]), sites, {}, self.inbox, self.wake, NOW, self.ctx)[0], "action")
        self.assertEqual(om.call_args[0][0], "only")
        kind, line = pk.act_on(self.item("open the model"), ReplyHost([]), {}, {}, self.inbox, self.wake, NOW, self.ctx)
        self.assertEqual(kind, "action")
        self.assertIn("FAILED: no site model found", line)

    def test_a_named_site_still_wins_over_the_newest(self):
        sites = {"porch": self.tmp / "t.skp", "other": self.tmp / "o.skp"}
        (self.tmp / "t.skp").write_text("x")
        (self.tmp / "o.skp").write_text("x")
        with mock.patch.object(pk, "open_model", return_value=(True, "opened")) as om:
            self.go(self.flow("Sending to the hub: open the newest porch model"), sites=sites)
        self.assertEqual(om.call_args[0][0], "porch")

    def test_a_failed_open_is_said_as_failed(self):
        sites = {"porch": self.tmp / "porch.skp"}
        with mock.patch.object(pk, "open_model", return_value=(False, "launched but no window")):
            self.go(self.flow("Sending to the hub: open the newest porch model"), sites=sites)
        self.assertIn("FAILED", self.host.chats[-1])

    def test_status_goes_through_the_marker_and_rebuilds_the_digest(self):
        done = self.go(self.flow("Sending to the hub: status"))
        self.assertIn("| status |", [l for k, l in done if l][-1])
        self.assertGreaterEqual(self.host.digests, 1)

    def test_an_unknown_site_or_an_off_menu_sentence_runs_nothing_and_becomes_a_question(self):
        # frontdesk-9: a marker that is not a menu sentence is a question for the hub. It never starts, sends to or closes a lane;
        # with no dispatch pass (this ctx) it goes straight onto the hub's list
        for p in ("open the newest nowhere model", "format the disk", "start own-app", "email Greg"):
            self.setUp()
            self.go(self.flow("Sending to the hub: " + p))
            self.assertEqual((self.host.started, self.host.sent, self.host.killed), ([], [], []), p)
            his = "can you list the files in docs"  # what he typed (him()): the hub gets that, not the receptionist's marker
            self.assertEqual(self.host.chats[-1], "On the hub's list: the hub has your message.", (p, self.host.chats))
            self.assertNotIn(his, self.host.chats[-1])
            self.assertIn(json.dumps(his), self.ctx.inbox_path.read_text())
            self.assertNotIn(json.dumps(p), self.ctx.inbox_path.read_text())
            self.assertEqual(dp.asks_of(self.state())[dp.ask_id_for(td.SHA)]["marker"], p)  # the rewording is kept, as context
            self.assertIn(dp.ask_id_for(td.SHA), self.ctx.wake_path.read_text())

    def test_an_empty_marker_runs_nothing_and_says_so(self):
        self.go(self.flow("Sending to the hub:"))
        self.nothing()
        self.assertTrue(self.host.chats[-1].startswith("Nothing ran from that"), self.host.chats)
        self.assertIn("hub's list", self.host.chats[-1])
        self.assertEqual([a["status"] for a in dp.asks_of(self.state()).values()], ["deferred"])

    def test_a_plain_stop_marker_stops_the_newest_lane_when_he_asked_in_other_words(self):
        # 16 Oct 22:28: he asked to stop the roadmap job in a sentence, so the typed "stop" form never fired and the marker was refused
        self.start("client-app")
        lane = self.lane_names()[0]
        self.go(self.flow("Sending to the hub: stop"))
        self.assertEqual(self.state()["lanes"][lane]["status"], "closed")
        self.assertIn("Stopped %s" % lane, self.host.chats[-1])

    def test_a_stop_marker_is_still_only_a_direct_reply_to_his_message(self):
        self.start("client-app")
        lane = self.lane_names()[0]
        self.go([him(), hub("Started oc-x in own-app on Codex."), msg("assistant", "Sure.\n\nSending to the hub: stop", ASK_TS + 6)])
        self.assertEqual(self.state()["lanes"][lane]["status"], "started")


class DoesNotAct(Reply):
    def test_a_hub_line_right_after_his_message_means_the_reply_is_not_to_him(self):
        self.go([him(), hub("Started oc-x in own-app on Codex."), msg("assistant", REPLY, ASK_TS + 6)])
        self.nothing()

    def test_a_lane_report_right_after_his_message_runs_nothing(self):
        report = "Lane oc-x (own-app) finished. Its report (the lane's own words, quoted):\n> " + MARK
        self.go([him(), hub(report)])
        self.nothing()

    def test_a_hub_line_is_recognised_by_its_text_even_if_the_provider_label_differs(self):
        self.go([him(), msg("assistant", "[hub]\n\nLane oc-x finished.\n" + MARK, ASK_TS + 2, provider="google")])
        self.nothing()

    def test_an_openclaw_provider_reply_is_not_model_written(self):
        self.go(self.flow(provider="openclaw"))
        self.nothing()

    def test_a_marker_quoted_inside_a_hub_line_that_follows_the_model_is_not_part_of_the_reply(self):
        self.go([him(), msg("assistant", "Sure, one moment.", ASK_TS + 2), hub("Lane oc-x finished. > " + MARK, ASK_TS + 5)])
        self.nothing()

    def test_two_markers_run_nothing(self):
        two = "Sending to the hub: start own-app: a\nSending to the hub: start own-app: b"
        self.go(self.flow(two))
        self.nothing()
        self.assertIn("more than one marker line", self.host.chats[-1])
        self.assertEqual([a["status"] for a in dp.asks_of(self.state()).values()], ["deferred"])
        self.setUp()
        self.go(self.flow("Sending to the hub: start own-app: a Sending to the hub: start own-app: b"))
        self.nothing()
        self.assertIn("more than one marker on a line", self.host.chats[-1])
        self.assertEqual([a["status"] for a in dp.asks_of(self.state()).values()], ["deferred"])

    def test_a_marker_that_is_not_the_last_line_still_runs(self):
        # drop 2, 19 Oct 3:38-3:57 PM: three replies put words after the marker and nothing ran ("the marker was not the last line")
        self.go(self.flow(MARK + "\n\nThat should do it."))
        self.assertEqual(len(self.lane_names()), 1)
        self.assertNotIn("not the last line", " ".join(self.host.chats))

    def test_no_marker_runs_nothing_and_says_nothing(self):
        self.go(self.flow("I can look into that. Want me to?"))
        self.nothing()
        self.assertEqual(self.host.chats, [])

    def test_a_marker_in_his_own_message_is_not_a_marker(self):
        self.go([him(MARK), msg("assistant", "I see.")], text=MARK)
        self.nothing()

    def test_a_different_message_of_his_is_not_the_one_that_was_replied_to(self):
        self.go([him("what is the weather"), msg("assistant", REPLY)])
        self.nothing()

    def test_the_same_words_typed_long_ago_are_not_this_message(self):
        self.go([him(ts=ASK_TS - 2 * pk.MATCH_TOL), msg("assistant", REPLY, ASK_TS - 2 * pk.MATCH_TOL + 3)])
        self.nothing()

    def test_thinking_parts_never_count(self):
        h = pk.normalize_history({"messages": [
            {"role": "user", "timestamp": int((ASK_TS + 1) * 1000), "content": [{"type": "text", "text": self.TEXT}]},
            {"role": "assistant", "provider": "google", "timestamp": int((ASK_TS + 3) * 1000),
             "content": [{"type": "thinking", "thinking": MARK}, {"type": "text", "text": "Hello."}]}]})
        self.assertEqual(h[1]["text"], "Hello.")
        self.go(h)
        self.nothing()

    def test_exact_typed_commands_still_work_and_read_nothing(self):
        self.host = ReplyHost(self.flow())
        kind, line = self.act("start own-app: list files")
        self.assertEqual((kind, len(self.host.started), self.host.reads), ("dispatch", 1, 0))


class Waiting(Reply):
    def pending(self):
        return json.loads(self.ctx.pending_path.read_text())

    def test_no_reply_yet_waits(self):
        self.go([him()], now=NOW)
        self.nothing()
        self.assertEqual(len(self.pending()), 1)

    def test_the_waiting_message_is_settled_by_the_next_poll_even_with_no_new_items(self):
        self.go([him()], now=NOW)
        self.host = ReplyHost(self.flow())
        with mock.patch.object(pk, "site_models", return_value={}):
            pk.run_act([], self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=NOW + 60, ctx=self.ctx)
        self.assertEqual(len(self.host.started), 1)
        self.assertEqual(self.pending(), [])

    def test_he_typed_twice_before_the_model_answered_the_first_waits_instead_of_being_dropped(self):
        self.go([him(), him("and also this", ASK_TS + 5)], now=NOW)
        self.nothing()
        self.assertEqual(len(self.pending()), 1)

    def test_an_unreadable_session_keeps_it_for_the_next_poll(self):
        self.go(None)
        self.nothing()
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(self.host.chats, [])

    def test_an_entry_gives_up_after_the_wait_and_a_reply_that_never_comes_runs_nothing(self):
        self.go([him()], now=NOW)
        self.host = ReplyHost(self.flow())
        with mock.patch.object(pk, "site_models", return_value={}):
            pk.run_act([], self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=ASK_TS + pk.ACT_MAX_AGE + 5, ctx=self.ctx)
        self.nothing()
        self.assertEqual(self.pending(), [])

    def test_his_message_missing_from_the_session_waits_while_young_then_is_dropped(self):
        self.go([msg("assistant", "unrelated")], now=NOW)
        self.assertEqual(len(self.pending()), 1)
        self.host = ReplyHost([msg("assistant", "unrelated")])
        with mock.patch.object(pk, "site_models", return_value={}):
            pk.run_act([], self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=ASK_TS + pk.REPLY_WAIT + 30, ctx=self.ctx)
        self.assertEqual(self.pending(), [])
        self.nothing()

    def test_a_settled_entry_is_not_acted_on_twice(self):
        self.go(self.flow())
        self.host = ReplyHost(self.flow())
        with mock.patch.object(pk, "site_models", return_value={}):
            pk.run_act([], self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=NOW + 30, ctx=self.ctx)
        self.assertEqual((self.host.started, self.host.reads), ([], 0))

    def test_the_waiting_list_is_capped(self):
        many = [{"sha": "%064x" % i, "ts": ASK_TS, "text": "t%d" % i} for i in range(pk.MAX_PENDING + 20)]
        pk.save_pending(self.ctx.pending_path, many)
        self.assertEqual(len(pk.load_pending(self.ctx.pending_path)), pk.MAX_PENDING)

    def test_a_damaged_waiting_file_is_an_empty_list(self):
        self.ctx.pending_path.write_text("{not json")
        self.assertEqual(pk.load_pending(self.ctx.pending_path), [])
        self.ctx.pending_path.write_text(json.dumps([{"sha": 1}, "x", {"sha": "a", "ts": 5, "text": "t"}]))
        self.assertEqual(len(pk.load_pending(self.ctx.pending_path)), 1)

    def test_with_nothing_waiting_and_no_items_nothing_is_read(self):
        self.host = ReplyHost(self.flow())
        pk.run_act([], self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=NOW, ctx=self.ctx)
        self.assertEqual(self.host.reads, 0)


class Gates(Reply):
    def test_a_message_from_any_source_but_the_tray_is_ignored(self):
        for src in ("twitch", "discord", "youtube", "drop"):
            self.host = ReplyHost(self.flow())
            kind, line = self.act(self.TEXT, source=src)
            self.assertEqual((kind, line), ("ignored", None), src)
            self.assertEqual((self.host.started, self.host.reads, self.host.chats), ([], 0, []))

    def test_without_the_lane_menu_chat_is_plain_chat_and_nothing_is_read(self):
        self.host = ReplyHost(self.flow())
        res = [{"verdict": "accept", "stored": self.item(self.TEXT, ts=ASK_TS)}]
        with mock.patch.object(pk, "site_models", return_value={}):
            pk.run_act(res, self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=NOW, ctx=None)
        self.assertEqual((self.host.started, self.host.reads), ([], 0))

    def test_a_message_without_a_time_is_never_queued(self):
        item = self.item(self.TEXT)
        del item["ts"]
        self.host = ReplyHost(self.flow())
        with mock.patch.object(pk, "site_models", return_value={}):
            pk.run_act([{"verdict": "accept", "stored": item}], self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=NOW, ctx=self.ctx)
        self.assertEqual((self.host.started, self.host.reads), ([], 0))

    def test_hub_prefixed_messages_go_to_the_hubs_list_and_nothing_else(self):
        self.host = ReplyHost(self.flow())
        kind, line = self.act("hub: please look at the roadmap")
        self.assertEqual(kind, "inbox")
        self.assertEqual((self.host.started, self.host.reads), ([], 0))

    def test_stop_typed_closes_the_newest_lane(self):
        self.start("client-app")
        lane = self.lane_names()[0]
        kind, line = self.act("stop", now=NOW + 5)
        self.assertEqual((kind, self.host.killed), ("dispatch", [lane]))

    def test_the_session_is_read_through_chat_history_for_main_only(self):
        calls = []

        class Rec(pk.Host):
            def _run(self, argv, timeout, cwd=None, stdin=None):
                calls.append((argv, stdin))

                class R:
                    returncode = 0
                    stdout = json.dumps({"messages": [{"role": "assistant", "provider": "google", "timestamp": 1, "content": [{"type": "text", "text": "hi"}]}]})
                return R()

        h = Rec().main_history()
        self.assertEqual(h, [{"role": "assistant", "provider": "google", "text": "hi", "ts_ms": 1}])
        argv, stdin = calls[0]
        self.assertIn("chat.history", argv[-1])
        self.assertEqual(json.loads(stdin), {"sessionKey": "agent:main:main", "limit": pk.HISTORY_WINDOW})


if __name__ == "__main__":
    unittest.main()
