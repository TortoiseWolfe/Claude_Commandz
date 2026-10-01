"""Tests for panel_review.py. No network, no Docker, no real clock: every transport, runner,
key reader, scanner and clock is a fake injected through panel_review.Deps."""

import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import panel_review as pr  # noqa: E402

CLEAN = lambda diff: "clean"  # noqa: E731
SPEC = "Add an owner greeting. Contact ops at ops@corp.io."
ACCEPT = "- prints hello\n- owner is configurable"
DIFF = ('diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1,3 @@\n'
        '+owner = "jane.doe@gmail.com"  # call (423) 555-0199\n+print("hello")\n')
ORIGINALS = ["jane.doe@gmail.com", "555-0199", "ops@corp.io"]
KEYS = {"groq": "gsk_SECRETGROQKEY", "gemini": "AIzaSECRETGEMINIKEY"}
PASS = {"verdict": "pass", "blocking": []}
REVISE = {"verdict": "revise", "blocking": ["off by one"]}


class FakeClock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class FakeNet:
    """Answers per provider. A value may be a verdict dict, raw text, (status, obj), or a callable
    taking (payload, call_number)."""

    def __init__(self, clock):
        self.clock, self.posts, self.gets = clock, [], []
        self.answers = {"groq": PASS, "gemini": PASS, "local": PASS}
        self.calls = {"groq": 0, "gemini": 0, "local": 0}
        self.tags_ok = True
        self.usage = 100

    @staticmethod
    def provider(url):
        return "groq" if "groq" in url else "gemini" if "generativelanguage" in url else "local"

    def post_json(self, url, headers, payload, timeout):
        who = self.provider(url)
        self.calls[who] += 1
        self.posts.append({"who": who, "url": url, "headers": headers, "payload": payload,
                           "t": self.clock.now(), "timeout": timeout})
        ans = self.answers[who]
        if callable(ans):
            ans = ans(payload, self.calls[who])
        if isinstance(ans, tuple):
            return ans
        text = ans if isinstance(ans, str) else json.dumps(ans)
        if who == "groq":
            return 200, {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": self.usage}}
        if who == "gemini":
            return 200, {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        return 200, {"message": {"content": text}}

    def get_json(self, url, timeout):
        self.gets.append((url, timeout))
        if not self.tags_ok:
            raise OSError("ollama down")
        return 200, {"models": []}


class FakeRunner:
    def __init__(self, reply=PASS, rc=0):
        self.calls, self.reply, self.rc = [], reply, rc

    def __call__(self, cmd, stdin_text, timeout):
        self.calls.append({"cmd": cmd, "stdin": stdin_text, "timeout": timeout})
        return self.rc, (self.reply if isinstance(self.reply, str) else json.dumps(self.reply))


def make_deps(net, clock, runner=None, keys=KEYS, installed=("grok",), scanner=CLEAN):
    def read_key(path):
        for name, val in keys.items():
            if f"/{name}/" in path:
                return val
        return None
    return pr.Deps(post_json=net.post_json, get_json=net.get_json, run_cmd=runner or FakeRunner(),
                   which=lambda n: f"/usr/bin/{n}" if n in installed else None,
                   now=clock.now, sleep=clock.sleep, read_key=read_key, scanner=scanner)


def max_window(events, window=60.0):
    """Largest token total inside any rolling `window`. events: (time, tokens)."""
    worst = 0
    for t, _ in events:
        worst = max(worst, sum(k for u, k in events if t - window < u <= t))
    return worst


class ExtractVerdictTests(unittest.TestCase):
    def test_plain_fenced_and_noisy(self):
        want = ("revise", ["off by one"])
        raw = '{"verdict":"revise","blocking":["off by one"]}'
        for text in (raw, f"```json\n{raw}\n```", f"Sure! Here you go:\n{raw}\nHope that helps.",
                     f"<think>hmm {{not json}}</think> {raw}"):
            self.assertEqual(pr.extract_verdict(text), want, text)

    def test_nested_wrapper_and_first_valid_object_wins(self):
        inner = '{"verdict":"pass","blocking":[]}'
        self.assertEqual(pr.extract_verdict('{"result": ' + inner + '}'), ("pass", []))
        self.assertEqual(pr.extract_verdict(inner + ' then {"verdict":"revise","blocking":["x"]}'), ("pass", []))

    def test_schema_echo_is_skipped_for_the_real_answer(self):
        text = 'Format: {"verdict":"pass"|"revise","blocking":["..."]}\n{"verdict":"pass","blocking":[]}'
        self.assertEqual(pr.extract_verdict(text), ("pass", []))

    def test_missing_blocking_means_empty(self):
        self.assertEqual(pr.extract_verdict('{"verdict":"pass"}'), ("pass", []))

    def test_strictness(self):
        for bad in ('{"verdict":"maybe","blocking":[]}', '{"verdict":"Pass","blocking":[]}',
                    '{"verdict":"pass","blocking":"none"}', '{"verdict":"pass","blocking":[1]}',
                    '{"blocking":[]}', "no json here", "", "{broken", "[]", None, 7):
            self.assertIsNone(pr.extract_verdict(bad), bad)


class MajorityTests(unittest.TestCase):
    def test_counting(self):
        m = pr.majority
        self.assertEqual(m({"a": "pass", "b": "pass", "c": "revise"}), "pass")
        self.assertEqual(m({"a": "revise", "b": "revise", "c": "pass"}), "revise")
        self.assertEqual(m({"a": "pass", "b": "revise"}), "split")
        self.assertEqual(m({"a": "pass", "b": "pass", "c": "revise", "d": "revise"}), "split")
        self.assertEqual(m({"a": "pass"}), "pass")
        self.assertEqual(m({"a": "revise"}), "revise")

    def test_errors_and_unavailable_do_not_count(self):
        self.assertEqual(pr.majority({"a": "error", "b": "unavailable"}), "none")
        self.assertEqual(pr.majority({}), "none")
        self.assertEqual(pr.majority({"a": "pass", "b": "error", "c": "unavailable"}), "pass")
        self.assertEqual(pr.majority({"a": "pass", "b": "revise", "c": "error"}), "split")

    def test_chunk_merge_revise_dominates(self):
        self.assertEqual(pr.merge_chunks([("pass", []), ("revise", ["x"]), ("pass", [])]), ("revise", ["x"]))
        self.assertEqual(pr.merge_chunks([("pass", []), ("revise", ["x"]), ("error", [])])[0], "revise")
        self.assertEqual(pr.merge_chunks([("pass", []), ("error", [])])[0], "error")
        self.assertEqual(pr.merge_chunks([("pass", []), ("pass", [])])[0], "pass")


class SplitDiffTests(unittest.TestCase):
    def files(self, n, size):
        return "".join(f"diff --git a/f{i}.py b/f{i}.py\n+++ b/f{i}.py\n" + ("+x = 1\n" * (size // 7))
                       for i in range(n))

    def test_small_diff_is_one_chunk(self):
        self.assertEqual(pr.split_diff(DIFF, 20000), [DIFF])

    def test_nothing_lost_nothing_oversize(self):
        diff = self.files(10, 3000)
        chunks = pr.split_diff(diff, 8000)
        self.assertEqual("".join(chunks), diff)
        self.assertTrue(all(len(c) <= 8000 for c in chunks))
        self.assertGreater(len(chunks), 3)

    def test_chunks_break_at_file_boundaries_when_files_fit(self):
        for chunk in pr.split_diff(self.files(10, 3000), 8000):
            self.assertTrue(chunk.startswith("diff --git"))

    def test_oversize_file_and_long_line_are_split(self):
        big = "diff --git a/b b/b\n" + "+y\n" * 5000
        chunks = pr.split_diff(big, 2000)
        self.assertEqual("".join(chunks), big)
        self.assertTrue(all(len(c) <= 2000 for c in chunks))
        long_line = "+" + "z" * 5000 + "\n"
        chunks = pr.split_diff(long_line, 1000)
        self.assertEqual("".join(chunks), long_line)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))

    def test_empty_diff_is_one_empty_chunk(self):
        self.assertEqual(pr.split_diff("", 1000), [""])


class PacerTests(unittest.TestCase):
    def pacer(self, limit=7000):
        c = FakeClock()
        return pr.Pacer(limit, c.now, c.sleep), c

    def test_fits_means_no_wait(self):
        p, c = self.pacer()
        p.acquire(3000)
        p.acquire(3000)
        self.assertEqual(c.sleeps, [])

    def test_waits_exactly_until_enough_has_expired(self):
        p, c = self.pacer()
        p.acquire(3000)
        p.acquire(3000)
        t0 = c.t
        p.acquire(3000)            # 9000 > 7000: the first 3000 must age out (60 s after it was spent)
        self.assertEqual(c.sleeps, [60.0])
        self.assertEqual(c.t, t0 + 60.0)

    def test_partial_expiry_waits_only_for_the_oldest(self):
        p, c = self.pacer()
        p.acquire(4000)
        c.sleep(30)                # 4000 spent 30 s ago
        p.acquire(2500)            # total 6500 fits
        c.sleeps.clear()
        p.acquire(4000)            # needs the first 4000 to expire: 30 s more
        self.assertEqual(c.sleeps, [30.0])

    def test_no_rolling_minute_exceeds_the_limit(self):
        p, c = self.pacer(7000)
        spent = []
        for n in (3500, 3500, 3500, 6500, 6500, 2000, 5000, 5000, 3000):
            p.acquire(n)
            spent.append((c.t, n))
        self.assertLessEqual(max_window(spent), 7000)

    def test_request_bigger_than_the_budget_does_not_spin(self):
        p, c = self.pacer(7000)
        p.acquire(10_000)
        self.assertEqual(c.sleeps, [])

    def test_waits_are_accounted(self):
        p, c = self.pacer()
        p.acquire(6500)
        p.acquire(6500)
        self.assertEqual(p.waited, 60.0)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_defaults_when_no_file(self):
        cfg, status = pr.load_config(os.path.join(self.tmp, "none.json"))
        self.assertEqual((cfg, status), (pr.DEFAULT_CONFIG, "defaults"))

    def test_file_overrides_section_by_section(self):
        p = os.path.join(self.tmp, "c.json")
        Path(p).write_text(json.dumps({"groq": {"model": "other/model"}, "grok": {"cmd": ["g", "--ask"]}}))
        cfg, status = pr.load_config(p)
        self.assertEqual(status, "file")
        self.assertEqual(cfg["groq"]["model"], "other/model")
        self.assertEqual(cfg["groq"]["endpoint"], pr.DEFAULT_CONFIG["groq"]["endpoint"])
        self.assertEqual(cfg["grok"]["cmd"], ["g", "--ask"])
        self.assertEqual(pr.DEFAULT_CONFIG["groq"]["model"], "openai/gpt-oss-120b")   # defaults not mutated

    def test_invalid_file_falls_back(self):
        p = os.path.join(self.tmp, "c.json")
        Path(p).write_text("{nope")
        self.assertEqual(pr.load_config(p), (pr.DEFAULT_CONFIG, "invalid"))

    def test_write_example_roundtrips_and_dir_is_private(self):
        p = pr.write_example(os.path.join(self.tmp, "panel", "config.example.json"))
        self.assertEqual(json.loads(Path(p).read_text()), pr.DEFAULT_CONFIG)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(p)).st_mode), 0o700)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.clock = FakeClock()
        self.net = FakeNet(self.clock)
        self.runner = FakeRunner()
        self.cfg = json.loads(json.dumps(pr.DEFAULT_CONFIG))

    def deps(self, **kw):
        return make_deps(self.net, self.clock, self.runner, **kw)

    def many_files(self, n, size):
        return "".join(f"diff --git a/f{i}.py b/f{i}.py\n+++ b/f{i}.py\n" + ("+x = 1\n" * (size // 7))
                       for i in range(n))


class GroqTests(Base):
    def test_request_shape_and_key(self):
        r = pr.review_with(pr.Groq(), self.cfg, self.deps(), SPEC, ACCEPT, DIFF)
        self.assertEqual(r["verdict"], "pass")
        post = self.net.posts[0]
        self.assertEqual(post["url"], "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(post["headers"], {"Authorization": "Bearer " + KEYS["groq"]})
        self.assertEqual(post["payload"]["model"], "openai/gpt-oss-120b")
        self.assertEqual(post["timeout"], 120)

    def test_chunks_stay_near_5k_tokens_and_no_minute_exceeds_7k(self):
        diff = self.many_files(8, 7500)
        r = pr.review_with(pr.Groq(), self.cfg, self.deps(), SPEC, ACCEPT, diff)
        self.assertGreaterEqual(r["chunks"], 3)
        spent = []
        for p in self.net.posts:
            prompt = p["payload"]["messages"][0]["content"]
            self.assertLessEqual(pr.est_tokens(prompt), 5001)
            spent.append((p["t"], pr.est_tokens(prompt) + self.cfg["groq"]["max_completion_tokens"]))
        self.assertLessEqual(max_window(spent), 7000)
        self.assertTrue(self.clock.sleeps, "back-to-back 5K chunks must have been paced")
        # every chunk of the diff was actually sent, once
        sent = "".join(p["payload"]["messages"][0]["content"] for p in self.net.posts)
        for i in range(8):
            self.assertEqual(sent.count(f"+++ b/f{i}.py"), 1)

    def test_small_chunks_share_a_minute_without_waiting(self):
        self.cfg["groq"]["chunk_tokens"] = 1500
        diff = self.many_files(2, 4000)
        r = pr.review_with(pr.Groq(), self.cfg, self.deps(), SPEC, ACCEPT, diff)
        self.assertEqual((r["chunks"], self.clock.sleeps), (2, []))

    def test_real_usage_above_the_estimate_delays_the_next_chunk(self):
        self.cfg["groq"]["chunk_tokens"] = 1500
        self.net.usage = 6900
        pr.review_with(pr.Groq(), self.cfg, self.deps(), SPEC, ACCEPT, self.many_files(2, 4000))
        self.assertTrue(self.clock.sleeps)

    def test_revise_if_any_chunk_revises(self):
        self.net.answers["groq"] = lambda payload, n: REVISE if n == 2 else PASS
        r = pr.review_with(pr.Groq(), self.cfg, self.deps(), SPEC, ACCEPT, self.many_files(8, 7500))
        self.assertEqual((r["verdict"], r["blocking"]), ("revise", ["off by one"]))

    def test_one_retry_on_429_then_success(self):
        self.net.answers["groq"] = lambda payload, n: (429, {}) if n == 1 else PASS
        r = pr.review_with(pr.Groq(), self.cfg, self.deps(), SPEC, ACCEPT, DIFF)
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(self.net.calls["groq"], 2)
        self.assertIn(self.cfg["groq"]["retry_wait"], self.clock.sleeps)

    def test_second_429_is_an_error_with_a_content_free_note(self):
        self.net.answers["groq"] = (429, {})
        r = pr.review_with(pr.Groq(), self.cfg, self.deps(), SPEC, ACCEPT, DIFF)
        self.assertEqual((r["verdict"], r["note"]), ("error", "HTTP_429"))

    def test_available_only_with_a_key(self):
        self.assertTrue(pr.Groq().available(self.cfg, self.deps()))
        self.assertFalse(pr.Groq().available(self.cfg, self.deps(keys={})))

    def test_chunk_cap_is_flagged_not_silent(self):
        self.cfg["max_chunks"] = 2
        r = pr.review_with(pr.Groq(), self.cfg, self.deps(), SPEC, ACCEPT, self.many_files(8, 7500))
        self.assertEqual((r["chunks"], r["truncated"]), (2, True))


class GeminiTests(Base):
    def test_request_shape(self):
        r = pr.review_with(pr.Gemini(), self.cfg, self.deps(), SPEC, ACCEPT, DIFF)
        self.assertEqual(r["verdict"], "pass")
        post = self.net.posts[0]
        self.assertEqual(post["url"], "https://generativelanguage.googleapis.com/v1beta/models/"
                                      "gemini-2.5-flash:generateContent")
        self.assertEqual(post["headers"], {"x-goog-api-key": KEYS["gemini"]})
        self.assertIn("DIFF", post["payload"]["contents"][0]["parts"][0]["text"])

    def test_model_is_configurable_and_parts_are_joined(self):
        self.cfg["gemini"]["model"] = "gemini-x"
        self.net.answers["gemini"] = lambda p, n: (200, {"candidates": [{"content": {"parts": [
            {"text": '{"verdict":"re'}, {"text": 'vise","blocking":["a"]}'}]}}]})
        r = pr.review_with(pr.Gemini(), self.cfg, self.deps(), SPEC, ACCEPT, DIFF)
        self.assertIn("gemini-x:generateContent", self.net.posts[0]["url"])
        self.assertEqual(r["verdict"], "revise")

    def test_available_only_with_a_key(self):
        self.assertFalse(pr.Gemini().available(self.cfg, self.deps(keys={"groq": "k"})))


class GrokTests(Base):
    def test_prompt_is_the_last_argument(self):
        r = pr.review_with(pr.Grok(), self.cfg, self.deps(), SPEC, ACCEPT, DIFF)
        call = self.runner.calls[0]
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(call["cmd"][:2], ["grok", "-p"])
        self.assertIn("ACCEPTANCE", call["cmd"][-1])
        self.assertIsNone(call["stdin"])
        self.assertEqual(call["timeout"], 120)

    def test_stdin_mode(self):
        self.cfg["grok"]["stdin"] = True
        pr.review_with(pr.Grok(), self.cfg, self.deps(), SPEC, ACCEPT, DIFF)
        call = self.runner.calls[0]
        self.assertEqual(call["cmd"], ["grok", "-p"])
        self.assertIn("SPEC:", call["stdin"])

    def test_prompt_placeholder_template(self):
        self.cfg["grok"]["cmd"] = ["grok", "--prompt", "{prompt}", "--json"]
        pr.review_with(pr.Grok(), self.cfg, self.deps(), SPEC, ACCEPT, DIFF)
        cmd = self.runner.calls[0]["cmd"]
        self.assertEqual((cmd[0], cmd[1], cmd[3]), ("grok", "--prompt", "--json"))
        self.assertIn("DIFF", cmd[2])

    def test_noisy_cli_output_is_tolerated_and_failure_is_an_error(self):
        self.runner.reply = 'thinking...\n```json\n{"verdict":"revise","blocking":["x"]}\n```\ndone'
        self.assertEqual(pr.review_with(pr.Grok(), self.cfg, self.deps(), SPEC, ACCEPT, DIFF)["verdict"], "revise")
        self.runner.rc = 1
        r = pr.review_with(pr.Grok(), self.cfg, self.deps(), SPEC, ACCEPT, DIFF)
        self.assertEqual((r["verdict"], r["note"]), ("error", "EXIT_1"))

    def test_available_only_if_the_binary_is_on_path(self):
        self.assertTrue(pr.Grok().available(self.cfg, self.deps(installed=("grok",))))
        self.assertFalse(pr.Grok().available(self.cfg, self.deps(installed=())))
        self.cfg["grok"]["cmd"] = ["mygrok", "-p"]
        self.assertFalse(pr.Grok().available(self.cfg, self.deps(installed=("grok",))))


class LocalTests(Base):
    def test_available_when_tags_answer_within_2s(self):
        self.assertTrue(pr.Local().available(self.cfg, self.deps()))
        self.assertEqual(self.net.gets, [("http://127.0.0.1:11434/api/tags", 2)])

    def test_unavailable_when_tags_fail(self):
        self.net.tags_ok = False
        self.assertFalse(pr.Local().available(self.cfg, self.deps()))

    def test_chat_request_shape(self):
        r = pr.review_with(pr.Local(), self.cfg, self.deps(), SPEC, ACCEPT, DIFF)
        post = self.net.posts[0]
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(post["url"], "http://127.0.0.1:11434/api/chat")
        self.assertEqual(post["payload"]["model"], "qwen2.5-coder:7b")
        self.assertIs(post["payload"]["stream"], False)
        self.assertEqual(post["payload"]["options"]["num_ctx"], 16384)


class Result:
    def __init__(self, stdout, rc):
        self.stdout, self.rc, self.lines = stdout, rc, {}
        for line in stdout.splitlines():
            if line.startswith("__") and line.endswith("__") and "=" in line:
                k, v = line[2:-2].split("=", 1)
                self.lines.setdefault(k, v)
        self.json = json.loads(self.lines["PANEL_JSON"]) if "PANEL_JSON" in self.lines else None


class FlowTests(Base):
    def run_panel(self, *extra, diff=DIFF, cls="public", spec=SPEC, scanner=CLEAN, experts=None, **dep_kw):
        paths = {}
        for name, text in (("spec", spec), ("acc", ACCEPT), ("diff", diff), ("terms", "Acme Corp\n")):
            paths[name] = os.path.join(self.tmp, name + ".txt")
            Path(paths[name]).write_text(text, encoding="utf-8")
        argv = ["--spec", paths["spec"], "--acceptance", paths["acc"], "--diff", paths["diff"],
                "--class", cls, "--terms-file", paths["terms"],
                "--config", os.path.join(self.tmp, "no-config.json"), *extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pr.main(argv, deps=self.deps(scanner=scanner, **dep_kw), experts=experts)
        return Result(buf.getvalue(), rc)

    def called(self):
        names = [w for w in ("groq", "gemini", "local") if self.net.calls[w]]
        return names + (["grok"] if self.runner.calls else [])

    # routing ---------------------------------------------------------------
    def test_public_asks_all_four(self):
        r = self.run_panel()
        self.assertEqual([r.lines[f"PANEL_{n}"] for n in ("groq", "gemini", "grok", "local")], ["pass"] * 4)
        self.assertEqual((r.lines["PANEL_MAJORITY"], r.rc), ("pass", 0))
        self.assertEqual(r.lines["GATE"], "send")

    def test_own_is_groq_and_local_only(self):
        r = self.run_panel(cls="own")
        self.assertEqual(sorted(self.called()), ["groq", "local"])
        self.assertNotIn("PANEL_gemini", r.lines)
        self.assertNotIn("PANEL_grok", r.lines)
        self.assertEqual(r.json["allowed"], ["groq", "local"])

    def test_client_is_local_only(self):
        r = self.run_panel(cls="client")
        self.assertEqual(self.called(), ["local"])
        self.assertEqual((r.lines["GATE"], r.lines["PANEL_local"]), ("local", "pass"))
        self.assertEqual(r.json["allowed"], ["local"])

    def test_never_send_narrows_public_to_local(self):
        r = self.run_panel("--never-send", "src/*")
        self.assertEqual(self.called(), ["local"])
        self.assertEqual(r.lines["GATE"], "local")

    def test_secret_finding_stops_everything(self):
        r = self.run_panel(scanner=lambda d: "found")
        self.assertEqual(self.called(), [])
        self.assertEqual(self.net.gets, [])
        self.assertEqual((r.lines["GATE"], r.lines["PANEL_MAJORITY"]), ("skip", "none"))
        self.assertEqual(r.lines["PANEL_REASON"], "secret scan found a finding")

    def test_scanner_unavailable_stops_everything(self):
        r = self.run_panel(scanner=lambda d: "unavailable")
        self.assertEqual((self.called(), r.lines["PANEL_MAJORITY"]), ([], "none"))
        self.assertEqual(r.lines["PANEL_REASON"], "secret scan unavailable")

    # availability and verdict maths ----------------------------------------
    def test_nothing_configured_means_all_unavailable_and_majority_none(self):
        self.net.tags_ok = False
        r = self.run_panel(keys={}, installed=())
        for n in ("groq", "gemini", "grok", "local"):
            self.assertEqual(r.lines[f"PANEL_{n}"], "unavailable")
        self.assertEqual((r.lines["PANEL_MAJORITY"], r.rc), ("none", 0))
        self.assertEqual(self.net.posts, [])

    def test_split_when_experts_disagree(self):
        self.net.answers["groq"] = REVISE
        self.net.tags_ok = False
        r = self.run_panel(keys={"groq": "k"}, installed=("grok",))
        self.assertEqual((r.lines["PANEL_groq"], r.lines["PANEL_grok"]), ("revise", "pass"))
        self.assertEqual(r.lines["PANEL_MAJORITY"], "split")

    def test_errors_are_shown_but_not_counted_and_exit_is_zero(self):
        self.net.answers["groq"] = (500, {})
        self.net.answers["gemini"] = "I think it looks fine to me"
        r = self.run_panel()
        self.assertEqual((r.lines["PANEL_groq"], r.lines["PANEL_gemini"]), ("error", "error"))
        self.assertEqual((r.lines["PANEL_MAJORITY"], r.rc), ("pass", 0))
        self.assertEqual(r.json["experts"]["groq"]["note"], "HTTP_500")
        self.assertEqual(r.json["experts"]["gemini"]["note"], "unparseable reply")

    def test_all_error_still_exits_zero(self):
        for w in ("groq", "gemini", "local"):
            self.net.answers[w] = (503, {})
        self.runner.rc = 2
        r = self.run_panel()
        self.assertEqual((r.lines["PANEL_MAJORITY"], r.rc), ("none", 0))

    def test_internal_failure_still_exits_zero(self):
        r = self.run_panel(experts={"nope": None})   # experts mapping broken: a bug in the tool, not the diff
        self.assertEqual((r.rc, r.lines["PANEL_MAJORITY"]), (0, "none"))
        self.assertIn("internal error", r.lines["PANEL_REASON"])

    # dry run -----------------------------------------------------------------
    def test_dry_run_makes_no_calls_at_all(self):
        r = self.run_panel("--dry-run")
        self.assertEqual((self.net.posts, self.net.gets, self.runner.calls), ([], [], []))
        self.assertEqual([r.lines[f"PANEL_{n}"] for n in ("groq", "gemini", "grok", "local")],
                         ["would_call"] * 4)
        self.assertEqual(r.lines["PANEL_DRYRUN"], "groq,gemini,grok,local")
        self.assertEqual((r.lines["GATE"], r.lines["PANEL_MAJORITY"], r.rc), ("send", "none", 0))
        self.assertTrue(r.json["dry_run"])

    def test_dry_run_reports_unconfigured_experts_and_respects_routing(self):
        r = self.run_panel("--dry-run", cls="own", keys={"groq": "k"})
        self.assertEqual(r.lines["PANEL_DRYRUN"], "groq,local")
        r = self.run_panel("--dry-run", keys={}, installed=())
        self.assertEqual((r.lines["PANEL_groq"], r.lines["PANEL_grok"]), ("unavailable", "unavailable"))
        self.assertEqual(r.lines["PANEL_DRYRUN"], "local")

    # privacy -----------------------------------------------------------------
    def test_only_the_redacted_diff_reaches_any_transport(self):
        r = self.run_panel()
        wire = json.dumps(self.net.posts) + json.dumps(self.runner.calls)
        self.assertEqual(len(self.net.posts), 3)
        self.assertEqual(len(self.runner.calls), 1)
        for original in ORIGINALS:
            self.assertNotIn(original, wire)
            self.assertNotIn(original, r.stdout)
        self.assertIn("[EMAIL_1]", wire)
        self.assertIn("[PHONE_1]", wire)

    def test_every_expert_gets_the_same_prompt(self):
        self.run_panel()
        prompts = {p["who"]: (p["payload"]["contents"][0]["parts"][0]["text"] if p["who"] == "gemini"
                              else p["payload"]["messages"][0]["content"]) for p in self.net.posts}
        prompts["grok"] = self.runner.calls[0]["cmd"][-1]
        self.assertEqual(len(set(prompts.values())), 1)
        p = prompts["groq"]
        for part in ("SPEC:", "ACCEPTANCE:", "DIFF:", "prints hello", '"verdict":"pass"|"revise"'):
            self.assertIn(part, p)

    def test_keys_go_in_headers_and_never_in_output(self):
        r = self.run_panel()
        heads = {p["who"]: p["headers"] for p in self.net.posts}
        self.assertEqual(heads["groq"], {"Authorization": "Bearer " + KEYS["groq"]})
        self.assertEqual(heads["gemini"], {"x-goog-api-key": KEYS["gemini"]})
        for key in KEYS.values():
            self.assertNotIn(key, r.stdout)

    # output ------------------------------------------------------------------
    def test_json_summary_truncates_reasons_and_carries_gate_and_timings(self):
        self.net.answers["gemini"] = {"verdict": "revise", "blocking": ["x" * 500, "short"]}
        r = self.run_panel(cls="public")
        j = r.json
        self.assertEqual([len(b) for b in j["experts"]["gemini"]["blocking"]], [200, 5])
        self.assertEqual((j["gate"], j["gate_reason"], j["class"], j["majority"]), ("send", "ok", "public", "pass"))
        self.assertIn("ms", j)
        self.assertIn("ms", j["experts"]["gemini"])
        self.assertEqual(j["experts"]["gemini"]["chunks"], 1)
        self.assertEqual(r.stdout.count("__PANEL_JSON="), 1)
        self.assertNotIn(", ", r.lines["PANEL_JSON"])   # compact separators


if __name__ == "__main__":
    unittest.main()
