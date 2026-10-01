"""Tests for usage_ledger.py. Synthetic transcripts in a temp HOME; no real data is read.

  python3 -m unittest discover -s ~/.claude/scripts/tests -p 'test_usage_ledger.py'
"""
import contextlib
import csv
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import usage_ledger as u  # noqa: E402

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="T", GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="dev@example.com",
               GIT_AUTHOR_EMAIL="dev@example.com")
D = "2026-09-10T"      # midday UTC: the same calendar day in any timezone within +-11h


def git(*args, cwd=None, env=None):
    r = subprocess.run(["git", *args], cwd=cwd, env=env or GIT_ENV, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def usage(i=10, o=20, w5=30, w1=0, r=40):
    return {"input_tokens": i, "output_tokens": o, "cache_creation_input_tokens": w5 + w1,
            "cache_read_input_tokens": r,
            "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}}


def asst(mid, ts, cwd, session="s1", model="claude-opus-5", side=False, uid=None, agentId=None, **kw):
    return {"type": "assistant", "uuid": uid or "u-" + mid + ts, "timestamp": D + ts + "Z", "sessionId": session,
            "cwd": cwd, "isSidechain": side, "agentId": agentId,
            "message": {"id": mid, "model": model, "role": "assistant", "content": [{"type": "text", "text": "SECRET-REPLY"}],
                        "usage": usage(**kw)}}


def user(ts, cwd, session="s1", content="SECRET-PROMPT", side=False, uid=None, **extra):
    o = {"type": "user", "uuid": uid or "uu-" + session + ts, "timestamp": D + ts + "Z", "sessionId": session,
         "cwd": cwd, "isSidechain": side, "message": {"role": "user", "content": content}}
    o.update(extra)
    return o


def tool_result(ts, cwd, session="s1"):
    return user(ts, cwd, session, content=[{"type": "tool_result", "tool_use_id": "t", "content": "x"}])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ledgertest")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = u.Env(os.path.join(self.tmp, "home"))
        os.makedirs(self.env.repos_root)
        os.makedirs(self.env.projects_dir)
        self.slug = u.slugify(self.env.repos_root)

    def repo(self, name, commits=1, email="dev@example.com"):
        p = os.path.join(self.env.repos_root, name)
        os.makedirs(p)
        git("init", "-q", "-b", "main", p)
        env = dict(GIT_ENV, GIT_AUTHOR_EMAIL=email)
        for n in range(commits):
            with open(os.path.join(p, "f%d" % n), "w") as fh:
                fh.write(str(n))
            git("add", ".", cwd=p)
            git("commit", "-q", "-m", "c%d" % n, cwd=p, env=env)
        return p

    def write(self, rel, lines, mode="w", raw_tail=""):
        p = os.path.join(self.env.projects_dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, mode) as fh:
            for o in lines:
                fh.write(json.dumps(o) + "\n")
            fh.write(raw_tail)
        return p

    def clients(self, data):
        os.makedirs(os.path.dirname(self.env.clients_path), exist_ok=True)
        with open(self.env.clients_path, "w") as fh:
            json.dump(data, fh)

    def ingest(self, **kw):
        return u.ingest(self.env, workers=1, **kw)

    def q(self, sql, *a):
        c = sqlite3.connect(self.env.db_path)
        try:
            return c.execute(sql, a).fetchall()
        finally:
            c.close()

    def sess(self, name="s1"):
        return "%s-repos/%s.jsonl" % (self.slug, name)


class TestDedupeAndFilters(Base):
    def test_dedupe_by_message_id_across_lines_and_files(self):
        # one reply written as three content-block lines, plus a resumed-session copy in another file
        block = [asst("m1", "12:00:00", self.env.repos_root, uid="a%d" % n) for n in range(3)]
        self.write(self.sess("s1"), block + [asst("m2", "12:01:00", self.env.repos_root)])
        self.write(self.sess("s2"), [asst("m1", "12:00:00", self.env.repos_root, session="s2", uid="b0")])
        s = self.ingest()
        self.assertEqual(s["token_rows"], 2)
        self.assertEqual(self.q("SELECT count(*) FROM tokens WHERE message_id='m1'")[0][0], 1)
        self.assertEqual(self.q("SELECT sum(input) FROM tokens")[0][0], 20)       # 10 + 10, not 10*4
        self.assertEqual(self.ingest()["token_rows"], 0)

    def test_synthetic_model_skipped(self):
        self.write(self.sess(), [asst("m1", "12:00:00", self.env.repos_root, model="<synthetic>"),
                                 asst("m2", "12:00:05", self.env.repos_root, model="claude-haiku-4-5-20251001")])
        self.ingest()
        self.assertEqual(self.q("SELECT message_id, model FROM tokens"), [("m2", "claude-haiku-4-5-20251001")])

    def test_cache_breakdown_missing_falls_back_to_5m(self):
        o = asst("m1", "12:00:00", self.env.repos_root)
        del o["message"]["usage"]["cache_creation"]
        self.write(self.sess(), [o])
        self.ingest()
        self.assertEqual(self.q("SELECT cache_5m, cache_1h FROM tokens"), [(30, 0)])

    def test_no_message_text_is_stored(self):
        self.write(self.sess(), [user("12:00:00", self.env.repos_root), asst("m1", "12:00:05", self.env.repos_root)])
        self.ingest()
        blob = b""
        for f in os.listdir(self.env.ledger_dir):
            if f.startswith("ledger.db"):
                with open(os.path.join(self.env.ledger_dir, f), "rb") as fh:
                    blob += fh.read()
        self.assertNotIn(b"SECRET-PROMPT", blob)
        self.assertNotIn(b"SECRET-REPLY", blob)

    def test_db_dir_mode_700(self):
        self.ingest()
        self.assertEqual(os.stat(self.env.ledger_dir).st_mode & 0o777, 0o700)

    def test_prompt_classification(self):
        cwd = self.env.repos_root
        cases = {
            "typed": (user("12:00:00", cwd, origin={"kind": "human"}, promptSource="typed", entrypoint="cli"), "human"),
            "plain": (user("12:00:01", cwd), "human"),
            "image": (user("12:00:02", cwd, content=[{"type": "text", "text": "hi"}, {"type": "image"}]), "human"),
            "tool": (tool_result("12:00:03", cwd), "tool"),
            "meta": (user("12:00:04", cwd, isMeta=True), "machine"),
            "sdk": (user("12:00:05", cwd, entrypoint="sdk-py", promptSource="sdk"), "machine"),
            "notify": (user("12:00:06", cwd, origin={"kind": "task-notification"}), "machine"),
            "tag": (user("12:00:07", cwd, content="<task-notification>x</task-notification>"), "machine"),
            "compact": (user("12:00:08", cwd, isCompactSummary=True), "machine"),
            "sidechain": (user("12:00:09", cwd, side=True), "machine"),
            "slash": (user("12:00:10", cwd, content="<command-name>/commit</command-name>"), "human"),
        }
        for name, (o, kind) in cases.items():
            self.assertEqual(u.classify_user(o), kind, name)


class TestAttribution(Base):
    def test_hub_session_that_cds_into_two_repos(self):
        a, b = self.repo("alpha"), self.repo("beta")
        os.makedirs(os.path.join(b, "src", "deep"))
        self.write(self.sess(), [
            asst("m1", "12:00:00", self.env.repos_root),
            asst("m2", "12:01:00", a),
            asst("m3", "12:02:00", os.path.join(b, "src", "deep")),
            asst("m4", "12:03:00", a)])
        self.ingest()
        got = dict(self.q("SELECT message_id, repo FROM tokens"))
        self.assertEqual(got, {"m1": "repos (hub)", "m2": "alpha", "m3": "beta", "m4": "alpha"})
        self.assertEqual(sorted(r[0] for r in self.q("SELECT DISTINCT repo FROM activity")), ["alpha", "beta", "repos (hub)"])

    def test_worktree_folds_to_parent_repo(self):
        main = self.repo("proj")
        wt = os.path.join(self.env.repos_root, "proj-feature-wt")
        git("worktree", "add", "-q", "-b", "feat", wt, cwd=main)
        res = u.Resolver(self.env)
        self.assertEqual(res.for_cwd(wt)[0], "proj")
        self.assertEqual(os.path.realpath(res.for_cwd(wt)[1]), os.path.realpath(main))
        self.assertEqual(res.for_cwd(os.path.join(wt, "x"))[0], "proj")       # subdir of the worktree too
        self.write(self.sess(), [asst("m1", "12:00:00", wt)])
        self.ingest()
        self.assertEqual(self.q("SELECT repo FROM tokens"), [("proj",)])

    def test_plain_folder_inside_hub_repo_is_not_folded_into_hub(self):
        git("init", "-q", "-b", "main", self.env.repos_root)           # ~/repos is itself a repo
        os.makedirs(os.path.join(self.env.repos_root, "scratchdir"))
        self.assertEqual(u.Resolver(self.env).for_cwd(os.path.join(self.env.repos_root, "scratchdir"))[0], "scratchdir")

    def test_fallback_name_rules_when_dir_is_gone(self):
        res = u.Resolver(self.env)
        for suffix in ("-wf-abc123", "-director", "-integrate", "-graphify", "-seo", "-tmux-crew", "-pin-city",
                       "-sitecheck", "-blog-kitchen", "-owned-parts", "-ai-kitchen", "-director-wf-x"):
            cwd = os.path.join(self.env.repos_root, "foo" + suffix, "sub")
            self.assertEqual(res.for_cwd(cwd)[0], "foo", suffix)
        self.assertEqual(res.for_cwd(os.path.join(self.env.repos_root, "other-thing"))[0], "other-thing")
        self.assertEqual(u.Resolver(self.env, extra_fold=[r"-scratch"]).for_cwd(
            os.path.join(self.env.repos_root, "foo-scratch"))[0], "foo")

    def test_no_cwd_decodes_project_dir_slug(self):
        self.repo("good_prompt_bad_prompt")
        o = asst("m1", "12:00:00", None)
        del o["cwd"]
        o2 = asst("m2", "12:00:01", None)
        del o2["cwd"]
        o3 = asst("m3", "12:00:02", None)
        del o3["cwd"]
        self.write("%s-good-prompt-bad-prompt-director/s1.jsonl" % self.slug, [o])
        self.write("%s-acme-studio--claude-worktrees-wf-4463c03b-2b9-1/s2.jsonl" % self.slug, [o2])
        self.write("%s/s3.jsonl" % self.slug, [o3])
        self.ingest()
        got = dict(self.q("SELECT message_id, repo FROM tokens"))
        self.assertEqual(got, {"m1": "good_prompt_bad_prompt", "m2": "acme-studio", "m3": "repos (hub)"})

    def test_client_globs_first_match_wins_and_default_is_own(self):
        cfg = {"rules": [("A", ["foo*"]), ("B", ["foo-bar*", "baz"])], "notes": {}, "fold": []}
        self.assertEqual(u.client_for(cfg, "foo-bar"), "A")                 # first match wins
        self.assertEqual(u.client_for(cfg, "baz"), "B")
        self.assertEqual(u.client_for(cfg, "zzz"), "own:zzz")
        self.assertEqual(u.client_for(cfg, "zzz", "sess1", {"sess1": "Tagged"}), "Tagged")   # tag beats the map
        self.clients({"clients": [{"client": "A", "repos": ["alpha*"], "note": "n"}], "fold": ["-x"]})
        loaded = u.load_clients(self.env.clients_path)
        self.assertEqual(loaded["rules"], [("A", ["alpha*"])])
        self.assertEqual(loaded["notes"], {"A": "n"})

    def test_tag_override_moves_a_session_between_clients(self):
        self.repo("asbuilt")
        self.clients({"clients": [{"client": "ada client", "repos": ["ada-*"]}]})
        self.write(self.sess("sess-ada-0001"), [asst("m1", "12:00:00", os.path.join(self.env.repos_root, "asbuilt"), session="sess-ada-0001")])
        self.write(self.sess("sess-other-02"), [asst("m2", "12:00:00", os.path.join(self.env.repos_root, "asbuilt"), session="sess-other-02")])
        self.ingest()
        by = lambda: {r["group"]: r["opus_equiv_tokens"] for r in u.build_report(self.env, "2026-09-01", "2026-09-30")["rows"]}
        self.assertEqual(set(by()), {"own:asbuilt"})
        out = io.StringIO()
        self.assertEqual(u.main(["tag", "sess-ada", "--client", "ada client"], env=self.env, out=out), 0)   # prefix works
        self.assertEqual(set(by()), {"own:asbuilt", "ada client"})
        self.assertEqual(u.main(["untag", "sess-ada-0001"], env=self.env, out=out), 0)
        self.assertEqual(set(by()), {"own:asbuilt"})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(u.main(["untag", "sess-ada-0001"], env=self.env, out=io.StringIO()), 1)   # nothing left to remove


class TestIncrementalIngest(Base):
    def test_append_only_adds_new_rows_and_untouched_files_are_skipped(self):
        cwd = self.env.repos_root
        path = self.write(self.sess(), [asst("m1", "12:00:00", cwd), user("12:00:10", cwd)])
        self.write(self.sess("other"), [asst("m9", "12:00:00", cwd, session="other")])
        first = self.ingest()
        self.assertEqual((first["token_rows"], first["activity_rows"], first["files_read"]), (2, 3, 2))
        with open(path, "a") as fh:
            fh.write(json.dumps(asst("m2", "12:01:00", cwd)) + "\n")
        second = self.ingest()
        self.assertEqual((second["token_rows"], second["activity_rows"], second["files_read"]), (1, 1, 1))
        third = self.ingest()
        self.assertEqual((third["token_rows"], third["activity_rows"], third["files_read"]), (0, 0, 0))
        stored = self.q("SELECT size, offset FROM files WHERE path=?", path)[0]
        self.assertEqual(stored, (os.path.getsize(path), os.path.getsize(path)))

    def test_half_written_tail_is_left_for_the_next_run(self):
        cwd = self.env.repos_root
        whole = json.dumps(asst("m2", "12:01:00", cwd))
        path = self.write(self.sess(), [asst("m1", "12:00:00", cwd)], raw_tail=whole[:40])
        self.assertEqual(self.ingest()["token_rows"], 1)
        with open(path, "a") as fh:
            fh.write(whole[40:] + "\n")
        self.assertEqual(self.ingest()["token_rows"], 1)
        self.assertEqual(self.q("SELECT count(*) FROM tokens")[0][0], 2)

    def test_final_line_without_newline_is_consumed_once(self):
        cwd = self.env.repos_root
        path = self.write(self.sess(), [], raw_tail=json.dumps(asst("m1", "12:00:00", cwd)))
        self.assertEqual(self.ingest()["token_rows"], 1)
        with open(path, "a") as fh:
            fh.write("\n" + json.dumps(asst("m2", "12:01:00", cwd)) + "\n")
        self.assertEqual(self.ingest()["token_rows"], 1)

    def test_shrunk_file_is_reread_without_duplicates(self):
        cwd = self.env.repos_root
        path = self.write(self.sess(), [asst("m1", "12:00:00", cwd), asst("m2", "12:01:00", cwd), asst("m3", "12:02:00", cwd)])
        self.assertEqual(self.ingest()["token_rows"], 3)
        self.write(self.sess(), [asst("m1", "12:00:00", cwd), asst("m4", "12:03:00", cwd)])    # rewritten, smaller
        self.assertLess(os.path.getsize(path), self.q("SELECT size FROM files WHERE path=?", path)[0][0])
        s = self.ingest()
        self.assertEqual((s["files_read"], s["token_rows"]), (1, 1))         # only m4 is new; m1 not duplicated
        self.assertEqual(self.q("SELECT count(*) FROM tokens")[0][0], 4)

    def test_full_rereads_everything_idempotently(self):
        self.write(self.sess(), [asst("m1", "12:00:00", self.env.repos_root)])
        self.ingest()
        s = self.ingest(full=True)
        self.assertEqual((s["files_read"], s["token_rows"], s["activity_rows"]), (1, 0, 0))

    def test_subagent_and_workflow_files_are_found_and_journal_is_not(self):
        cwd = self.env.repos_root
        self.write("%s/s1/subagents/agent-aaa.jsonl" % self.slug, [asst("sub1", "12:00:00", cwd, side=True, agentId="aaa")])
        self.write("%s/s1/subagents/workflows/wf_x-1/agent-bbb.jsonl" % self.slug, [asst("wf1", "12:00:00", cwd, side=True)])
        self.write("%s/s1/subagents/workflows/wf_x-1/journal.jsonl" % self.slug, [asst("jr1", "12:00:00", cwd)])
        self.ingest()
        self.assertEqual(sorted(r[0] for r in self.q("SELECT message_id FROM tokens")), ["sub1", "wf1"])
        self.assertEqual(self.q("SELECT DISTINCT sidechain FROM activity"), [(1,)])


class TestTimeMath(Base):
    M = 60

    def test_lone_prompt_gets_five_minute_floor(self):
        self.assertEqual(u.stream_intervals([1000], u.HUMAN_GAP, u.HUMAN_FLOOR), [(1000, 1300)])

    def test_prompts_within_gap_merge_and_far_ones_do_not(self):
        t = 10_000
        iv = u.stream_intervals([t, t + 10 * self.M], u.HUMAN_GAP, u.HUMAN_FLOOR)
        self.assertEqual(u.total_seconds(iv), 15 * self.M)                  # bridged: last - first + floor
        iv = u.stream_intervals([t, t + 15 * self.M], u.HUMAN_GAP, u.HUMAN_FLOOR)
        self.assertEqual(u.total_seconds(iv), 20 * self.M)                  # exactly 15 min still bridges
        iv = u.stream_intervals([t, t + 16 * self.M], u.HUMAN_GAP, u.HUMAN_FLOOR)
        self.assertEqual(iv, [(t, t + 5 * self.M), (t + 16 * self.M, t + 21 * self.M)])     # 16 min: two islands
        iv = u.stream_intervals([t, t + 2 * self.M], u.HUMAN_GAP, u.HUMAN_FLOOR)
        self.assertEqual(u.total_seconds(iv), 7 * self.M)                   # floor overlaps the next prompt

    def test_agent_gap_is_ten_minutes_without_floor(self):
        t = 10_000
        iv = u.stream_intervals([t, t + 9 * self.M, t + 25 * self.M], u.AGENT_GAP, 0)
        self.assertEqual(u.total_seconds(iv), 9 * self.M)
        self.assertEqual(u.stream_intervals([t, t + 10 * self.M], u.AGENT_GAP, 0), [(t, t + 10 * self.M)])

    def test_union_across_parallel_sessions_does_not_double_count(self):
        t = 10_000
        rows = [("A", "r", "human", 0, t + n * 5 * self.M) for n in range(3)]            # A: 0, 5, 10 min -> 15 min
        rows += [("B", "r", "human", 0, t + 5 * self.M + n * 5 * self.M) for n in range(2)]   # B: 5, 10 min -> 10 min
        got = u.hours_by_group(rows, lambda s, r: "c", {"human"}, u.HUMAN_GAP, u.HUMAN_FLOOR, False)
        self.assertEqual(u.total_seconds(got["c"]), 15 * self.M)            # union, not 25
        two = u.hours_by_group(rows, lambda s, r: s, {"human"}, u.HUMAN_GAP, u.HUMAN_FLOOR, False)
        self.assertEqual(sum(u.total_seconds(v) for v in two.values()), 25 * self.M)    # per-session sum would double count

    def test_sidechain_lines_excluded_from_hours_unless_asked(self):
        rows = [("A", "r", "assistant", 0, 1000), ("A", "r", "assistant", 1, 1500), ("A", "r", "assistant", 0, 1600)]
        off = u.hours_by_group(rows, lambda s, r: "c", {"assistant"}, u.AGENT_GAP, 0, False)
        on = u.hours_by_group(rows, lambda s, r: "c", {"assistant"}, u.AGENT_GAP, 0, True)
        self.assertEqual((u.total_seconds(off["c"]), u.total_seconds(on["c"])), (600, 600))
        rows = [("A", "r", "assistant", 0, 1000), ("A", "r", "assistant", 1, 1500), ("A", "r", "assistant", 0, 2000)]
        off = u.hours_by_group(rows, lambda s, r: "c", {"assistant"}, u.AGENT_GAP, 0, False)
        on = u.hours_by_group(rows, lambda s, r: "c", {"assistant"}, u.AGENT_GAP, 0, True)
        self.assertEqual((u.total_seconds(off["c"]), u.total_seconds(on["c"])), (0, 1000))   # 1000s gap > 600s cap alone

    def test_report_hours_end_to_end_with_overall_union_across_clients(self):
        self.clients({"clients": [{"client": "A", "repos": ["a-*"]}, {"client": "B", "repos": ["b-*"]}]})
        ca, cb = [os.path.join(self.env.repos_root, n) for n in ("a-one", "b-one")]
        self.write(self.sess("sa"), [user("12:00:00", ca, "sa"), user("12:10:00", ca, "sa")])     # 15 min
        self.write(self.sess("sb"), [user("12:05:00", cb, "sb"), user("12:20:00", cb, "sb")])     # 20 min, overlaps A
        self.ingest()
        rep = u.build_report(self.env, "2026-09-10", "2026-09-10")
        hours = {r["group"]: r["your_hours"] for r in rep["rows"]}
        self.assertAlmostEqual(hours["A"], 15 / 60)
        self.assertAlmostEqual(hours["B"], 20 / 60)
        self.assertAlmostEqual(rep["all_your_h"], 25 / 60)                  # 12:00 .. 12:25 merged
        day = u.build_report(self.env, "2026-09-10", "2026-09-10", by="day")
        self.assertAlmostEqual(sum(r["your_hours"] for r in day["rows"]), 25 / 60)


class TestTokenMath(unittest.TestCase):
    def test_opus_equivalent_weights(self):
        # (input + output + cache_write) * weight  +  cache_read * 0.1 * weight
        self.assertEqual(u.opus_equiv("claude-opus-5", 100, 50, 20, 5, 1000), 4 * 175 + 0.1 * 4 * 1000)
        self.assertEqual(u.opus_equiv("claude-opus-4-7", 1, 1, 1, 0, 10), 4 * 3 + 0.1 * 4 * 10)
        self.assertEqual(u.opus_equiv("claude-sonnet-5-5", 100, 50, 20, 5, 1000), 2 * 175 + 0.1 * 2 * 1000)
        self.assertEqual(u.opus_equiv("claude-haiku-4-5-20251001", 100, 50, 20, 5, 1000), 1 * 175 + 0.1 * 1 * 1000)
        self.assertEqual(u.opus_equiv("claude-opus-5", 0, 0, 0, 0, 0), 0)

    def test_family_detection(self):
        fam = u.model_family
        self.assertEqual([fam(m) for m in ("claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5-20251001",
                                           "claude-fable-5-1", "gpt-x", None)],
                         ["opus", "sonnet", "haiku", "fable", "other", "other"])

    def test_api_equivalent_usd_uses_family_price_and_cache_multipliers(self):
        # opus family: $5 in, $25 out, 5m write 1.25x, 1h write 2x, read $0.50 per MTok
        got = u.usd_of("claude-opus-5", 1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000)
        self.assertAlmostEqual(got, 5 + 25 + 5 * 1.25 + 5 * 2 + 0.5)
        # exact-model override: opus 5.5 is cheaper than the rest of the family
        self.assertAlmostEqual(u.usd_of("claude-opus-5-5", 1_000_000, 0, 0, 0, 0), 4.0)
        self.assertAlmostEqual(u.usd_of("claude-haiku-4-5-20251001", 0, 1_000_000, 0, 0, 0), 5.0)
        self.assertIn("fable", u.PRICES)


class TestPanel(Base):
    def lines(self, *objs):
        return [json.dumps(o) for o in objs]

    def test_panel_ingest_dedupes_by_line_hash_and_is_incremental(self):
        a = {"ts": D + "12:00:00Z", "repo": "acme-studio", "expert": "groq", "model": "gpt-oss", "input_tokens": 100,
             "output_tokens": 10, "ms": 900, "class": "client", "gate": "pass"}
        b = dict(a, expert="local", input_tokens=200)
        os.makedirs(self.env.ledger_dir, mode=0o700)
        with open(self.env.panel_log, "w") as fh:
            fh.write("\n".join(self.lines(a, b, a)) + "\n")                # a twice: identical line
        self.assertEqual(self.ingest()["panel_rows"], 2)
        self.assertEqual(self.ingest()["panel_rows"], 0)
        with open(self.env.panel_log, "a") as fh:
            fh.write(json.dumps(dict(a, ms=901)) + "\n")
        self.assertEqual(self.ingest()["panel_rows"], 1)
        with open(self.env.panel_log, "a") as fh:
            fh.write(json.dumps(dict(a, ms=901)) + "\n")                   # replayed line is not a new row
        self.assertEqual(self.ingest()["panel_rows"], 0)

    def test_missing_panel_log_is_fine_and_report_groups_panel_tokens_by_client(self):
        self.assertEqual(self.ingest()["panel_rows"], 0)
        self.clients({"clients": [{"client": "Acme Studio", "repos": ["acme-studio*"]}]})
        rows = [{"ts": D + "12:00:00Z", "repo": os.path.join(self.env.repos_root, "acme-studio-wf-x"), "expert": "groq",
                 "model": "m", "input_tokens": 300, "output_tokens": 30, "ms": 1, "class": "client", "gate": "pass"},
                {"ts": D + "12:01:00Z", "repo": "unmapped", "expert": "groq", "model": "m", "input_tokens": 7,
                 "output_tokens": 1, "ms": 1, "class": "own", "gate": "pass"}]
        with open(self.env.panel_log, "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
        self.ingest()
        got = {r["group"]: (r["panel_in"], r["panel_out"]) for r in u.build_report(self.env, "2026-09-10", "2026-09-10")["rows"]}
        self.assertEqual(got, {"Acme Studio": (300, 30), "own:unmapped": (7, 1)})


class TestCommitsAndReport(Base):
    def test_commits_counted_per_parent_repo_by_author_and_clone_not_double_counted(self):
        main = self.repo("proj", commits=2)                                  # 2 commits by a listed author
        with open(os.path.join(main, "x"), "w") as fh:
            fh.write("x")
        git("add", ".", cwd=main)
        git("commit", "-q", "-m", "stranger", cwd=main, env=dict(GIT_ENV, GIT_AUTHOR_EMAIL="stranger@example.com"))
        git("clone", "-q", main, os.path.join(self.env.repos_root, "proj-clone"))      # a clone: same 3 commits
        wt = os.path.join(self.env.repos_root, "proj-wt")
        git("worktree", "add", "-q", "-b", "wtbranch", wt, cwd=main)
        with open(os.path.join(wt, "w"), "w") as fh:
            fh.write("w")
        git("add", ".", cwd=wt)
        git("commit", "-q", "-m", "on worktree branch", cwd=wt)                         # visible through the parent's refs
        self.write(self.sess(), [asst("m1", "12:00:00", main), asst("m2", "12:00:05", os.path.join(self.env.repos_root, "proj-clone")),
                                 asst("m3", "12:00:10", wt)])
        self.ingest()
        repos = dict(self.q("SELECT name, path FROM repos"))
        self.assertEqual(sorted(repos), ["proj", "proj-clone"])             # the worktree folded into proj
        lo, hi = u.day_bounds("2000-01-01")
        conn = sqlite3.connect(self.env.db_path)
        head = u.commits_by_repo(conn, lo, hi, emails=["dev@example.com"])
        allb = u.commits_by_repo(conn, lo, hi, emails=["dev@example.com"], all_branches=True)
        conn.close()
        self.assertEqual(sum(len(v) for v in head.values()), 2)             # HEAD only: stranger excluded, clone adds none
        self.assertEqual(sum(len(v) for v in allb.values()), 3)             # + the worktree-branch commit via the parent's refs
        self.assertEqual(len(allb["proj"]), 3)
        self.assertNotIn("proj-clone", allb)
        self.clients({"clients": [], "author_emails": ["dev@example.com"]})   # the report reads identities from the map
        self.assertEqual(sum(r["commits"] for r in u.build_report(self.env, "2000-01-01")["rows"]), 2)
        self.assertEqual(sum(r["commits"] for r in u.build_report(self.env, "2000-01-01", all_branches=True)["rows"]), 3)

    def test_report_text_and_csv_shapes(self):
        self.clients({"clients": [{"client": "Contract Co", "repos": ["gpbp*"], "note": "billable via Insightful"}]})
        cwd = os.path.join(self.env.repos_root, "gpbp")
        self.write(self.sess(), [user("12:00:00", cwd), asst("m1", "12:00:30", cwd, model="claude-opus-5", i=1000, o=500, w5=100, w1=0, r=10000),
                                 asst("m2", "12:01:00", cwd, model="claude-sonnet-5-5", i=10, o=20, w5=30, w1=0, r=40)])
        self.ingest()
        out = io.StringIO()
        self.assertEqual(u.main(["report", "--since", "2026-09-10", "--until", "2026-09-10", "--csv"], env=self.env, out=out), 0)
        rows = list(csv.DictReader(io.StringIO(out.getvalue())))
        contract = next(r for r in rows if r["group"] == "Contract Co")
        self.assertEqual(contract["note"], "billable via Insightful")
        self.assertEqual((contract["opus_input"], contract["opus_output"], contract["opus_cache_write"], contract["opus_cache_read"]),
                         ("1000", "500", "100", "10000"))
        self.assertEqual(contract["sonnet_input"], "10")
        expect = 4 * 1600 + 0.4 * 10000 + 2 * 60 + 0.2 * 40
        self.assertAlmostEqual(float(contract["opus_equiv_tokens"]), expect, places=0)
        self.assertEqual(rows[-1]["group"], "ALL (merged union)")
        text = io.StringIO()
        u.main(["report", "--since", "2026-09-10", "--until", "2026-09-10", "--by", "model"], env=self.env, out=text)
        self.assertIn("claude-opus-5", text.getvalue())
        self.assertIn("API-EQUIVALENT", text.getvalue())
        for by in ("repo", "day", "client"):
            self.assertEqual(u.main(["report", "--since", "2026-09-10", "--by", by], env=self.env, out=io.StringIO()), 0)

    def test_clients_command_lists_mapping_and_tags(self):
        self.clients({"clients": [{"client": "Contract Co", "repos": ["gpbp*"]}]})
        self.write(self.sess(), [asst("m1", "12:00:00", os.path.join(self.env.repos_root, "gpbp")),
                                 asst("m2", "12:00:05", os.path.join(self.env.repos_root, "mystery"))])
        self.ingest()
        u.main(["tag", "s1", "--client", "Elsewhere"], env=self.env, out=io.StringIO())
        out = io.StringIO()
        self.assertEqual(u.main(["clients"], env=self.env, out=out), 0)
        text = out.getvalue()
        self.assertRegex(text, r"Contract Co\s+gpbp\s+gpbp\*")
        self.assertRegex(text, r"own:mystery\s+mystery\s+\(no rule\)")
        self.assertIn("Session tags", text)

    def test_bad_date_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(u.main(["report", "--since", "09/01/2026"], env=self.env, out=io.StringIO()), 2)


if __name__ == "__main__":
    unittest.main()
