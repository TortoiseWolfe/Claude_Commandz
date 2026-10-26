"""hours.py: minute-level time log. Every fixture is invented (clients acme and globex, example.test addresses, a fake HOME);
nothing here reads a real transcript, mailbox or calendar."""

import datetime as dt
import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import hours as H  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

ET = ZoneInfo("America/New_York")
OWNER = "owner@example.test"


def at(day, hm, secs=0):
    """Eastern wall time -> epoch. at('2026-10-20', '16:05')."""
    h, m = hm.split(":")
    d = dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=int(h), minute=int(m), second=secs, tzinfo=ET)
    return d.timestamp()


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def user_line(ts, cwd, text, **kw):
    o = {"type": "user", "timestamp": iso(ts), "cwd": cwd, "entrypoint": "cli", "message": {"role": "user", "content": text}}
    o.update(kw)
    return json.dumps(o, separators=(",", ":"))


def asst_line(ts, cwd, text):
    return json.dumps({"type": "assistant", "timestamp": iso(ts), "cwd": cwd,
                       "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}, separators=(",", ":"))


def cfg_for(**over):
    cfg = {"clients": {
        "acme": {"label": "Acme", "repos": ["acme-*"], "words": ["acme"], "mail_to": ["acme.example"],
                 "git_repos": ["~/repos/acme-site"], "calls": [{"dir": "~/meetings/acme", "slot": "Tue 16:00-16:30", "project": "general"}],
                 "projects": {"Site": {"repos": ["acme-site"], "words": []}}, "log_name": "acme"},
        "globex": {"label": "Globex", "repos": ["globex-*"], "words": ["globex"]}},
        "overrides": {}, "relay_patterns": [], "weekly": ["acme"], "owner_emails": [OWNER]}
    cfg.update(over)
    return cfg


class Unit(unittest.TestCase):
    def test_stream_iv_bridges_within_gap_and_floors_a_lone_point(self):
        t = 1_000_000.0
        self.assertEqual(H.stream_iv([t]), [(t, t + 300)])
        # two prompts 15 minutes apart bridge into one run; 15 min + 1 s apart do not
        self.assertEqual(H.stream_iv([t, t + 900]), [(t, t + 1200)])
        self.assertEqual(len(H.stream_iv([t, t + 901])), 2)
        self.assertEqual(H.secs(H.stream_iv([t, t + 301, t + 602])), 602 + 300)

    def test_merge_and_secs_count_overlap_once(self):
        self.assertEqual(H.merge_iv([(0, 10), (5, 20), (30, 40)]), [(0, 20), (30, 40)])
        self.assertEqual(H.secs(H.merge_iv([(0, 10), (0, 10), (10, 15)])), 15)

    def test_attribute_planted_overlap_counts_each_minute_once(self):
        # call 0-10, typed 5-15, mail 8-13, git 12-17 (minutes): the union is 17 minutes, not 10+10+5+5
        m = 60
        raws = [H.raw(0, 10 * m, "call", "p", "call", "c"), H.raw(5 * m, 15 * m, "typed", "p", "typed", "t"),
                H.raw(8 * m, 13 * m, "mail", "p", "mail", "m"), H.raw(12 * m, 17 * m, "git", "p", "commit", "g")]
        segs = H.attribute(raws)
        self.assertEqual(sum(g["e"] - g["s"] for g in segs), 17 * m)
        for a, b in zip(segs, segs[1:]):
            self.assertLessEqual(a["e"], b["s"])           # disjoint
        by = {}
        for g in segs:
            by[g["src"]] = by.get(g["src"], 0) + g["e"] - g["s"]
        self.assertEqual(by, {"call": 10 * m, "typed": 5 * m, "git": 2 * m})  # the highest priority names each minute
        self.assertEqual(H.attribute([]), [])

    def test_sittings_join_by_gap_and_report_covered_minutes(self):
        segs = [dict(H.raw(0, 300, "typed", "p", "a", "x"), covered=["typed"]), dict(H.raw(1000, 1300, "typed", "p", "b", "y"), covered=["typed"]),
                dict(H.raw(9000, 9300, "typed", "p", "c", "z"), covered=["typed"])]
        st = H.sittings(segs, 1800)
        self.assertEqual(len(st), 2)
        self.assertEqual(H.sit_row(st[0])["minutes"], 10)       # 5 + 5 covered, not 21 end-minus-start

    def test_repo_names_folds_worktrees_scratch_and_nested_folders(self):
        home = fresh_dir(self)
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old) if old else os.environ.pop("HOME", None))
        r = home + "/repos/"
        self.assertEqual(H.repo_names(r + "acme-site"), ["acme-site"])
        self.assertEqual(H.repo_names(r + "acme-site/src/x"), ["acme-site"])
        self.assertEqual(H.repo_names(r + ".worktrees/acme-site--feat"), ["acme-site"])
        self.assertEqual(H.repo_names(r + "acme-site-wf-ab12"), ["acme-site"])
        self.assertEqual(H.repo_names(r + "CD/cd-thing/x"), ["cd-thing", "CD"])
        self.assertEqual(H.repo_names("/tmp/claude-1000/-home-x-repos-acme-site/0123abcd-0123-0123-0123-0123456789ab/scratch"), ["acme-site"])
        self.assertEqual(H.repo_names("/elsewhere/acme-site"), [])
        self.assertEqual(H.repo_names(None), [])

    def rec(self, text, cwd="/h/repos/hub-thing", prev="", ts=None, sid="abcdef123", **kw):
        return dict({"cwd": cwd, "text": text, "q": "", "prev": prev, "ts": ts or at("2026-10-20", "10:00"), "sid": sid}, **kw)

    def test_label_event_order_override_card_folder_words_short_reply(self):
        cfg = cfg_for(ask_patterns=[["globex", ["blue card"]], ["unclear", ["grey card"]]])
        home = fresh_dir(self)
        os.environ["HOME"], old = home, os.environ.get("HOME")
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old) if old else os.environ.pop("HOME", None))
        cwd = home + "/repos/acme-site"
        # folder
        self.assertEqual(H.label_event(cfg, self.rec("do a thing", cwd=cwd))["clients"], ["acme"])
        self.assertEqual(H.label_event(cfg, self.rec("do a thing", cwd=cwd))["project"], {"acme": "Site"})
        # words, in a neutral folder
        lab = H.label_event(cfg, self.rec("ask acme about the invoice"))
        self.assertEqual((lab["clients"], lab["why"]), (["acme"], "words in the prompt"))
        self.assertEqual(lab["project"], {"acme": "general"})
        self.assertEqual(H.label_event(cfg, self.rec("acme and globex"))["why"], "words name several clients")
        # a short reply is weak only: listed as unclear, never counted
        lab = H.label_event(cfg, self.rec("yes", prev="shall I email acme?"))
        self.assertEqual((lab["clients"], lab["weak"]), ([], ["acme"]))
        self.assertEqual(H.label_event(cfg, self.rec("yes please go ahead and do all of that and then tell me " + "x" * 60, prev="acme?"))["weak"], [])
        # a hand label beats the folder; "other" counts for nobody; "unclear" counts for nobody
        for ov, want in (("globex", ["globex"]), ("other", []), ("unclear", []), ("acme+globex", ["acme", "globex"])):
            r = self.rec("do a thing", cwd=cwd, sid="abcdef999", ts=at("2026-10-20", "10:00", 30))
            cfg["overrides"] = {"abcdef|Tue 20 10:00:30 AM": ov}
            self.assertEqual(H.label_event(cfg, r)["clients"], want, ov)
        # a card: ask_patterns beat the folder, and an "unclear" pattern stops the search with nobody counted
        cfg["overrides"] = {}
        blue = self.rec("ok", cwd=cwd, q="the Blue Card says", k="ask")
        self.assertEqual(H.label_event(cfg, blue, ask=True)["clients"], ["globex"])
        grey = self.rec("ok", cwd=cwd, q="grey card, about acme", k="ask")
        lab = H.label_event(cfg, grey, ask=True)
        self.assertEqual((lab["clients"], lab["why"]), ([], "question card text (unclear)"))

    def test_srt_length_reads_last_cue(self):
        d = fresh_dir(self)
        p = os.path.join(d, "t.srt")
        open(p, "w").write("1\n00:00:01,000 --> 00:00:04,500\nhi\n\n2\n00:21:30,250 --> 00:21:31,750\nbye\n")
        self.assertAlmostEqual(H.srt_length(p), 21 * 60 + 31.75)
        self.assertIsNone(H.srt_length(os.path.join(d, "missing.srt")))
        open(p, "w").write("no cues here\n")
        self.assertIsNone(H.srt_length(p))

    def test_due_only_monday_after_six_and_once_a_day(self):
        home = fresh_dir(self)
        os.environ["HOME"], old = home, os.environ.get("HOME")
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old) if old else os.environ.pop("HOME", None))
        mon7, mon5, tue7 = at("2026-10-26", "07:00"), at("2026-10-26", "05:00"), at("2026-10-27", "07:00")
        self.assertTrue(H.due(mon7))
        self.assertFalse(H.due(mon5))
        self.assertFalse(H.due(tue7))
        os.makedirs(H.state_dir())
        open(os.path.join(H.state_dir(), "hours-week.stamp"), "w").write("2026-10-26\n")
        self.assertFalse(H.due(mon7))
        self.assertTrue(H.due(at("2026-11-02", "07:00")))

    def test_hh_week_start_and_split_days(self):
        self.assertEqual(H.hh(5400), "1.5")
        self.assertEqual(H.fday(H.week_start_ts(at("2026-10-22", "12:00"))), "2026-10-19")
        d = H.split_days([(at("2026-10-20", "23:00"), at("2026-10-21", "01:00"))])
        self.assertEqual({k: round(v) for k, v in d.items()}, {"2026-10-20": 3600, "2026-10-21": 3600})


class World(unittest.TestCase):
    """A fake HOME with transcripts, a recording, a calendar cache, a fake Gmail tool and a git repo, all on Tue 20 Oct 2026."""

    DAY = "2026-10-20"

    def setUp(self):
        self.home = fresh_dir(self)
        old = os.environ.get("HOME"), os.environ.get("HOURS_GMAIL")
        os.environ["HOME"] = self.home
        self.addCleanup(self.restore, old)
        h = self.home
        for d in (".claude/projects/-x-acme", ".claude/projects/-x-globex", ".claude/projects/-x-hub", "meetings/acme/2026-10-20-sync",
                  "repos/acme-site", "out", "bin"):
            os.makedirs(os.path.join(h, d))
        acme, globex, hub = h + "/repos/acme-site", h + "/repos/globex-app", h + "/repos/hub-thing"
        T = lambda hm, s=0: at(self.DAY, hm, s)  # noqa: E731
        w = lambda name, lines: open(os.path.join(h, ".claude/projects", name), "w").write("\n".join(lines) + "\n")  # noqa: E731
        # typed: 4:05 and 4:10 PM in the acme folder (one run, 4:05-4:15), the globex folder at 4:06 PM, a neutral folder naming acme at 6:00 PM
        w("-x-acme/aaaaaa11.jsonl", [user_line(T("16:05"), acme, "start on the page"), user_line(T("16:10"), acme, "and the footer")])
        w("-x-globex/bbbbbb22.jsonl", [user_line(T("16:06"), globex, "fix the globex thing")])
        w("-x-hub/cccccc33.jsonl", [user_line(T("18:00"), hub, "ask acme about the invoice"), asst_line(T("18:01"), hub, "I will email acme"),
                                    user_line(T("18:02"), hub, "yes")])
        # a lane window: the hub typed these, so none of it is the owner's time
        w("-x-acme/dddddd44.jsonl", [user_line(T("17:00"), acme, "You are a lane (not the hub). Do the job."),
                                    user_line(T("17:01"), acme, "Hub: your context was cleared"),
                                    user_line(T("17:02"), acme, "keep going")])
        # a call recording of 10 minutes and a calendar cache with nothing else that day
        open(h + "/meetings/acme/2026-10-20-sync/transcript.srt", "w").write("1\n00:00:01,000 --> 00:10:00,000\nhello\n")
        os.makedirs(h + "/out", exist_ok=True)
        open(h + "/out/calendar-events.tsv", "w").write("start\tend\ttitle\tclient\n")
        # the fake Gmail tool prints the real format: one mail sent at 4:12 PM, one received (no SENT label)
        sent = "Tue, 20 Oct 2026 16:12:00 -0400"
        tool = h + "/bin/fakegmail"
        open(tool, "w").write("#!/bin/sh\ncat <<'EOF'\n"
                              "MSG aaaa1111bbbb | %s | Turtle <%s> | Re: the page | %s | SENT | t1\n"
                              "MSG cccc2222dddd | someone@acme.example | Someone <someone@acme.example> | hello | %s | INBOX | t2\n"
                              "EOF\n" % (OWNER, OWNER, sent, sent))
        os.chmod(tool, os.stat(tool).st_mode | stat.S_IEXEC)
        os.environ["HOURS_GMAIL"] = tool
        # git: a by-hand commit at 4:08 PM, an AI-assisted one at 4:30 PM (a transcript covers it, so it is not hand time) and an older
        # AI-assisted one before any transcript existed (counted, labelled git only)
        g = ["git", "-C", acme]
        subprocess.run(g + ["init", "-q"], check=True)
        for when, msg, who in ((T("16:08"), "hand commit", OWNER), (T("16:30"), "ai commit\n\nCo-Authored-By: Claude <x@example.test>", OWNER),
                               (at("2026-10-01", "12:00"), "old ai commit\n\nCo-Authored-By: Claude <x@example.test>", OWNER),
                               (T("16:09"), "someone else's commit", "other@example.test")):
            env = dict(os.environ, GIT_AUTHOR_NAME="n", GIT_AUTHOR_EMAIL=who, GIT_COMMITTER_NAME="n", GIT_COMMITTER_EMAIL=who,
                       GIT_AUTHOR_DATE=str(int(when)), GIT_COMMITTER_DATE=str(int(when)))
            subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", msg], check=True, env=env)
        self.cfg = cfg_for(out_dir=h + "/out")
        self.lo, self.hi = H.window(self.DAY, self.DAY)

    @staticmethod
    def restore(old):
        for k, v in zip(("HOME", "HOURS_GMAIL"), old):
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def run_analyze(self, **kw):
        return H.analyze(self.cfg, ["acme", "globex"], self.lo, self.hi, machine="NX-01", **kw)

    def test_typed_only_matches_the_ledger_rule_and_ignores_lane_windows(self):
        res = self.run_analyze(sources=False)
        a, g = H.ksum(res["clients"]["acme"]), H.ksum(res["clients"]["globex"])
        # acme: 4:05-4:15 run (10 min) + 6:00 PM alone (5 min); the lane window's three prompts add nothing. "yes" is only weak.
        self.assertEqual(round(a["total"] / 60), 15)
        self.assertEqual(len(res["clients"]["acme"]["weak"]), 1)
        self.assertEqual(round(g["total"] / 60), 5)
        self.assertEqual(res["cls"]["dddddd44"], "lane")
        # every session in the acme folders is counted or has a reason
        reasons = {c[1][:6]: c[4] for c in res["clients"]["acme"]["coverage"]}
        self.assertEqual(reasons["aaaaaa"], "counted")
        self.assertTrue(reasons["dddddd"].startswith("machine-only: a lane window"))

    def test_all_sources_overlap_counts_each_minute_once(self):
        res = self.run_analyze()
        r = res["clients"]["acme"]
        by = H.ksum(r)["src"]
        # each source on its own: call 4:00-4:10 (10), typed 4:05-4:15 + 6:00 (15), mail 4:07-4:12 (5), git 4:08-4:13 (5)
        self.assertEqual({s: round(v / 60) for s, v in by.items()}, {"call": 10, "typed": 15, "mail": 5, "git": 5})
        # merged: 4:00-4:15 is 15 minutes, plus 6:00-6:05; 35 minutes of raw time is 20 minutes of life
        self.assertEqual(round(H.ksum(r)["total"] / 60), 20)
        segs = r["segs"]
        self.assertEqual(sum(g["e"] - g["s"] for g in segs), 20 * 60)
        for a, b in zip(segs, segs[1:]):
            self.assertLessEqual(a["e"], b["s"])
        # the AI-assisted 4:30 commit is not hand time; the other person's commit is not the owner's
        self.assertFalse(any("ai commit" == g["what"][len("commit: "):] for g in segs))
        self.assertFalse(any("someone else" in g["what"] for g in segs))
        # a mail the owner received is not mail the owner sent
        self.assertEqual(len(H.sent_mail(self.cfg, "acme", self.lo, self.hi)), 1)
        # the call took its length from the recording and its start from the weekly slot (a Tuesday)
        calls = [x for x in r["raws"] if x["src"] == "call"]
        self.assertEqual((calls[0]["s"], round((calls[0]["e"] - calls[0]["s"]) / 60)), (at(self.DAY, "16:00"), 10))
        self.assertIn("weekly Tue slot", calls[0]["evid"])

    def test_a_calendar_event_sets_the_call_start_and_one_without_a_recording_counts_alone(self):
        open(self.home + "/out/calendar-events.tsv", "a").write(
            "%s\t%s\tsync\tacme\n%s\t%s\tall-hands\tacme\n" % (iso(at(self.DAY, "15:30")), iso(at(self.DAY, "16:00")),
                                                              iso(at(self.DAY, "20:00")), iso(at(self.DAY, "20:45"))))
        calls = sorted((x for x in H.call_raws(self.cfg, "acme", self.lo, self.hi)), key=lambda x: x["s"])
        self.assertEqual([(x["s"], round((x["e"] - x["s"]) / 60)) for x in calls], [(at(self.DAY, "15:30"), 10), (at(self.DAY, "20:00"), 45)])
        self.assertIn("calendar event", calls[0]["evid"])

    def test_git_ai_commits_count_only_before_the_first_transcript(self):
        first = at(self.DAY, "16:05")
        raws = H.git_raws(self.cfg, "acme", 0.0, self.hi, first)
        text = " ".join(x["what"] + " " + x["evid"] for x in raws)
        self.assertIn("hand commit", text)
        self.assertIn("old ai commit", text)
        self.assertIn("git only", text)
        self.assertNotIn("ai commit\n", text)
        self.assertNotIn("someone else", text)
        self.assertNotIn("ai commit", " ".join(x["what"] for x in raws if "old" not in x["what"]))

    def test_rows_round_trip_between_machines(self):
        res = self.run_analyze(sources=False)
        p = os.path.join(self.home, "rows.tsv")
        H.rows_file(res, ["acme"], "NCC-74656", p)
        lines = open(p).read().splitlines()
        self.assertEqual(lines[0].split("\t")[:4], ["machine", "client", "project", "src"])
        self.assertEqual(len(lines) - 1, len(res["clients"]["acme"]["raws"]))
        # the other machine's rows come back as raws, tagged with where they came from, and merge without double counting
        back = H.read_rows_in([p])
        self.assertEqual(len(back["acme"]), len(lines) - 1)
        self.assertTrue(back["acme"][0]["evid"].startswith("NCC-74656: "))
        both = H.analyze(self.cfg, ["acme"], self.lo, self.hi, machine="NX-01", sources=False, rows_in=[p])
        self.assertEqual(H.ksum(both["clients"]["acme"])["total"], H.ksum(res["clients"]["acme"])["total"])

    def test_log_files_tsv_adds_up_to_the_report(self):
        res = H.analyze(self.cfg, ["acme"], 0.0, self.hi, machine="NX-01")
        out = os.path.join(self.home, "log")
        md, tsv, rows = H.log_files(self.cfg, res, "acme", 0.0, self.hi, "NX-01", out)
        self.assertTrue(md.endswith("acme-time-log.md") and tsv.endswith("acme-time-log.tsv"))
        lines = open(tsv).read().splitlines()
        self.assertEqual(lines[0].split("\t"), H.TSV_COLS)
        minutes = sum(int(l.split("\t")[3]) for l in lines[1:])
        total = H.ksum(res["clients"]["acme"])["total"] / 60
        self.assertLessEqual(abs(minutes - total), len(lines))   # each sitting rounds to a whole minute
        text = open(md).read()
        for head in ("## Totals per week", "## Totals per project", "## The sittings", "## Unclear", "## Every session on disk"):
            self.assertIn(head, text)

    def test_main_modes_run_end_to_end(self):
        os.makedirs(os.path.join(self.home, ".config/ledger"))
        json.dump({"hours": {k: v for k, v in self.cfg.items() if k != "owner_emails"}, "author_emails": [OWNER]},
                  open(os.path.join(self.home, ".config/ledger/clients.json"), "w"))
        out = os.path.join(self.home, "log2")
        for mode, args in (("report", ["--client", "acme", "--since", self.DAY, "--until", self.DAY]),
                           ("log", ["--client", "acme", "--out", out]),
                           ("unclear", ["--client", "acme"]),
                           ("rows", ["--client", "acme", "--out", os.path.join(self.home, "r.tsv")])):
            self.assertEqual(H.main([mode] + args), 0, mode)
        self.assertTrue(os.path.exists(os.path.join(out, "acme-time-log.tsv")))
        with self.assertRaises(SystemExit):
            H.main(["report", "--client", "nobody"])

    def test_run_week_writes_the_line_and_the_stamp_and_runs_once(self):
        mon = at("2026-10-26", "07:00")
        with mock.patch.object(H, "refresh_calendar") as refresh:     # the real one would look for a real calendar token
            line = H.run_week(self.cfg, mon, "NX-01", self.home + "/wk", if_due=True)
        refresh.assert_called_once()                              # the Monday run starts from a fresh calendar
        self.assertIn("Acme", line)
        self.assertIn("0.3 h", line)                          # 20 minutes = 0.33 h of Tue 20 Oct
        self.assertEqual(open(os.path.join(H.state_dir(), "hours-week.txt")).read().strip(), line)
        self.assertTrue(os.path.exists(self.home + "/wk/weekly/2026-10-19.md"))
        self.assertTrue(os.path.exists(self.home + "/wk/acme-time-log.tsv"))   # a client with a log_name refreshes its full log
        self.assertIsNone(H.run_week(self.cfg, mon, "NX-01", self.home + "/wk", if_due=True))     # the stamp says it ran today
        self.assertIsNone(H.run_week(self.cfg, at("2026-10-27", "07:00"), "NX-01", self.home + "/wk", if_due=True))   # not a Monday


class CalendarRefresh(unittest.TestCase):
    """calendar-events.tsv is filled from Google Calendar (calendar_api.list_events, replaced here by a fake) for the clients' events only."""

    def setUp(self):
        self.home = fresh_dir(self)
        old = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.addCleanup(lambda: os.environ.pop("HOME", None) if old is None else os.environ.__setitem__("HOME", old))
        os.makedirs(self.home + "/out")
        self.tsv = self.home + "/out/calendar-events.tsv"
        self.cfg = cfg_for(out_dir=self.home + "/out")
        self.cfg["clients"]["acme"]["calendar"] = {"words": [r"Acme\b"]}
        self.now = at("2026-10-26", "18:00")

    @staticmethod
    def ev(start, end, title, attendees=(), declined=False):
        return {"start": start, "end": end, "title": title, "attendees": list(attendees), "self_declined": declined}

    def events(self):
        return [
            self.ev("2026-10-20T16:00:00-04:00", "2026-10-20T16:30:00-04:00", "Acme sync", ["boss@acme.example", OWNER]),
            self.ev("2026-10-22T10:00:00-04:00", "2026-10-22T11:00:00-04:00", "Planning", ["x@mail.acme.example"]),   # a subdomain of mail_to
            self.ev("2026-10-23T10:00:00-04:00", "2026-10-23T11:00:00-04:00", "Acme standup"),                          # title words only
            self.ev("2026-10-24T10:00:00-04:00", "2026-10-24T11:00:00-04:00", "Dentist", [OWNER]),                      # nobody's
            self.ev("2026-10-25T10:00:00-04:00", "2026-10-25T11:00:00-04:00", "Acme retro", declined=True),             # declined
            self.ev("2026-10-27T16:00:00-04:00", "2026-10-27T16:30:00-04:00", "Acme sync", ["boss@acme.example"]),     # still ahead
            self.ev("2026-10-21T10:00:00-04:00", "2026-10-21T11:00:00-04:00", "Lookalike", ["x@notacme.example"]),      # not a subdomain
        ]

    def test_rows_are_the_clients_events_only_sorted_and_tab_separated(self):
        ok, msg = H.refresh_calendar(self.cfg, self.now, fetch=lambda lo, hi: self.events())
        self.assertTrue(ok, msg)
        rows = [l.split("\t") for l in open(self.tsv).read().splitlines()]
        self.assertEqual(rows[0][:2], ["start_iso", "end_iso"])
        self.assertEqual([(r[2], r[3]) for r in rows[1:]],
                         [("Acme sync", "acme"), ("Planning", "acme"), ("Acme standup", "acme"), ("Acme sync", "acme")])
        self.assertEqual([r[0][:10] for r in rows[1:]], sorted(r[0][:10] for r in rows[1:]))

    def test_the_rows_feed_the_hours_the_same_way_the_old_file_did(self):
        H.refresh_calendar(self.cfg, self.now, fetch=lambda lo, hi: self.events())
        got = [(r[2], r[3]) for r in H.calendar_rows(self.cfg)]
        self.assertIn(("Acme sync", "acme"), got)
        self.assertEqual(len(got), 4)

    def test_the_status_line_names_the_newest_past_event_not_a_future_one(self):
        ok, msg = H.refresh_calendar(self.cfg, self.now, fetch=lambda lo, hi: self.events())
        self.assertIn("4 client events", msg)
        self.assertIn("newest past one Fri 23 Oct 10:00 AM Acme standup", msg)
        self.assertEqual(open(os.path.join(H.state_dir(), "hours-calendar.txt")).read().strip(), msg)

    def test_the_window_asked_for_runs_from_calendar_since_to_two_weeks_ahead(self):
        asked = []
        self.cfg["calendar_since"] = "2026-08-01"
        H.refresh_calendar(self.cfg, self.now, fetch=lambda lo, hi: asked.append((lo, hi)) or [])
        self.assertEqual(asked, [(at("2026-08-01", "00:00"), self.now + 14 * 86400)])

    def test_a_failure_keeps_the_old_file_and_says_so(self):
        open(self.tsv, "w").write("start\tend\ttitle\tclient\n2026-10-20T16:00:00-04:00\t2026-10-20T16:30:00-04:00\told call\tacme\n")
        before = open(self.tsv).read()

        def boom(lo, hi):
            raise RuntimeError("Calendar API 403: disabled")
        ok, msg = H.refresh_calendar(self.cfg, self.now, fetch=boom)
        self.assertFalse(ok)
        self.assertIn("NOT refreshed", msg)
        self.assertIn("403", msg)
        self.assertEqual(open(self.tsv).read(), before)
        self.assertFalse(os.path.exists(self.tsv + ".tmp"))
        self.assertIn("NOT refreshed", open(os.path.join(H.state_dir(), "hours-calendar.txt")).read())

    def test_the_first_refresh_keeps_the_old_hand_copy_once(self):
        open(self.tsv, "w").write("hand copy\n")
        H.refresh_calendar(self.cfg, self.now, fetch=lambda lo, hi: self.events())
        H.refresh_calendar(self.cfg, self.now, fetch=lambda lo, hi: [])
        self.assertEqual(open(self.tsv + ".pre-api").read(), "hand copy\n")      # the second run did not overwrite it

    def test_no_calendar_token_is_a_failure_line_not_a_crash(self):
        os.environ["AFA_GMAIL_DIR"] = self.home + "/nocreds"
        self.addCleanup(os.environ.pop, "AFA_GMAIL_DIR", None)
        ok, msg = H.refresh_calendar(self.cfg, self.now)
        self.assertFalse(ok)
        self.assertIn("NOT refreshed", msg)

    def test_calendar_mode_runs_from_the_command_line_and_exits_1_on_failure(self):
        env = dict(os.environ, HOME=self.home, AFA_GMAIL_DIR=self.home + "/nocreds")
        r = subprocess.run([sys.executable, str(HERE.parent / "hours.py"), "calendar"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("NOT refreshed", r.stdout)

    def test_the_timer_script_runs_the_calendar_refresh_on_every_run(self):
        sh = (HERE.parent / "openclaw_status_refresh.sh").read_text()
        line = next(x for x in sh.splitlines() if x.startswith("(") and "hours.py\" calendar" in x)
        self.assertNotIn("--if-due", line)                      # every run, not only Monday


if __name__ == "__main__":
    unittest.main()
