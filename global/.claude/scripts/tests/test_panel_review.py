"""Tests for panel_review.py. No network, no Docker, no real clock: every transport, runner,
key reader, scanner and clock is a fake injected through panel_review.Deps."""

import contextlib
import copy
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ledger_log  # noqa: E402
import panel_review as pr  # noqa: E402

CLEAN = lambda diff: "clean"  # noqa: E731
SPEC = "Add an owner greeting. Contact ops at ops@corp.io."
ACCEPT = "- prints hello\n- owner is configurable"
DIFF = ('diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1,3 @@\n'
        '+owner = "jane.doe@gmail.com"  # call (423) 555-0199\n+print("hello")\n')
ORIGINALS = ["jane.doe@gmail.com", "555-0199", "ops@corp.io"]
KEYS = {"groq": "gsk_SECRETGROQKEY", "gemini": "AIzaSECRETGEMINIKEY",
        "cloudflare": "cf_SECRETTOKEN", "openrouter": "sk-or-SECRETKEY"}
ACCOUNT_ID = "ACCT_ID_0123456789"
PASS = {"verdict": "pass", "blocking": []}
REVISE = {"verdict": "revise", "blocking": ["off by one"]}
ALL_EXPERTS = ["groq", "groq_qwen", "gemini", "cloudflare", "antigravity", "openrouter", "grok", "local"]
API_EXPERTS = ["groq", "groq_qwen", "gemini", "cloudflare", "openrouter", "local"]   # reached by post_json
TRAINING = ("gemini", "antigravity", "openrouter", "grok")
AGY_TOKEN = "~/.gemini/antigravity-cli/antigravity-oauth-token"


class FakeClock:
    """Thread-safe: the panel asks its experts on worker threads, all sharing this clock."""

    def __init__(self):
        self.t, self.sleeps = 1000.0, []
        self._lock = threading.Lock()

    def now(self):
        return self.t

    def sleep(self, s):
        with self._lock:
            self.sleeps.append(s)
            self.t += s


class FakeNet:
    """Answers per provider. A value may be a verdict dict, raw text, (status, obj), or a callable
    taking (payload, call_number)."""

    OUT = 20   # completion tokens every fake provider reports; prompt tokens are `usage` minus this

    def __init__(self, clock):
        self.clock, self.posts, self.gets = clock, [], []
        self.answers = {n: PASS for n in API_EXPERTS}
        self.calls = {n: 0 for n in API_EXPERTS}
        self.tags_ok = True
        self.usage = 100          # total tokens per call as the provider reports them
        self.report_usage = True  # False: a provider that returns no usage block at all
        self.neurons = {}         # {expert: float}: providers that meter in neurons (Cloudflare)
        self.content = {}         # {expert: "str" | "dict" | "list"}: shape of choices[0].message.content
        self._lock = threading.Lock()

    @staticmethod
    def provider(url, payload):
        if "groq" in url:   # one Groq URL, two experts: told apart by model
            return "groq_qwen" if str(payload.get("model", "")).startswith("qwen/") else "groq"
        for needle, who in (("cloudflare", "cloudflare"), ("openrouter", "openrouter"),
                            ("generativelanguage", "gemini")):
            if needle in url:
                return who
        return "local"

    def post_json(self, url, headers, payload, timeout):
        who = self.provider(url, payload)
        with self._lock:
            self.calls[who] += 1
            n = self.calls[who]
            self.posts.append({"who": who, "url": url, "headers": headers, "payload": payload,
                               "t": self.clock.now(), "timeout": timeout})
        ans = self.answers[who]
        if callable(ans):
            ans = ans(payload, n)
        if isinstance(ans, tuple):
            return ans
        text = ans if isinstance(ans, str) else json.dumps(ans)
        pt, ct = self.usage - self.OUT, self.OUT
        if who == "gemini":
            body = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
            if self.report_usage:
                body["usageMetadata"] = {"promptTokenCount": pt, "candidatesTokenCount": ct,
                                         "totalTokenCount": pt + ct}
            return 200, body
        if who == "local":
            body = {"message": {"content": text}}
            if self.report_usage:
                body.update({"prompt_eval_count": pt, "eval_count": ct})
            return 200, body
        style = self.content.get(who, "str")
        shaped = (json.loads(text) if style == "dict"
                  else [{"type": "text", "text": text[:7]}, {"type": "text", "text": text[7:]}] if style == "list"
                  else text)
        body = {"choices": [{"message": {"content": shaped}}]}   # groq, groq_qwen, cloudflare, openrouter
        if self.report_usage:
            body["usage"] = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
            if who in self.neurons:
                body["usage"]["neurons"] = self.neurons[who]
        return 200, body

    def get_json(self, url, timeout):
        self.gets.append((url, timeout))
        if not self.tags_ok:
            raise OSError("ollama down")
        return 200, {"models": []}


class FakeRunner:
    def __init__(self, reply=PASS, rc=0):
        self.calls, self.reply, self.rc = [], reply, rc

    def __call__(self, cmd, timeout):
        self.calls.append({"cmd": cmd, "timeout": timeout})
        return self.rc, (self.reply if isinstance(self.reply, str) else json.dumps(self.reply))


def make_deps(net, clock, runner=None, keys=KEYS, installed=("grok", "agy"), scanner=CLEAN, existing=(AGY_TOKEN,), **kw):
    def read_key(path):
        for name, val in keys.items():
            if f"/{name}/" in path:
                # the Cloudflare account id is a second file in the same directory as the token
                return ACCOUNT_ID if path.endswith("account-id") else val
        return None
    return pr.Deps(post_json=net.post_json, get_json=net.get_json, run_cmd=runner or FakeRunner(),
                   which=lambda n: f"/usr/bin/{n}" if n in installed else None,
                   exists=lambda path: path in existing, now=clock.now, sleep=clock.sleep, read_key=read_key,
                   scanner=scanner, **kw)


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

    def load(self, obj):
        p = os.path.join(self.tmp, "c.json")
        Path(p).write_text(obj if isinstance(obj, str) else json.dumps(obj))
        return pr.load_config(p)

    @staticmethod
    def by_name(cfg):
        return {e["name"]: e for e in cfg["experts"]}

    def test_default_experts_and_their_shape(self):
        d = self.by_name(pr.DEFAULT_CONFIG)
        self.assertEqual(list(d), ALL_EXPERTS)
        for e in d.values():
            for field in ("name", "kind", "classes"):
                self.assertIn(field, e)
        want = {"groq": ("openai_compat", ["own", "public"], False),
                "groq_qwen": ("openai_compat", ["own", "public"], False),
                "gemini": ("gemini", ["public"], True),
                "cloudflare": ("openai_compat", ["own", "public"], False),
                "antigravity": ("cli", ["public"], True),
                "openrouter": ("openai_compat", ["public"], True),
                "grok": ("cli", ["public"], True),
                "local": ("ollama", ["public", "own", "client"], False)}
        for name, (kind, classes, trains) in want.items():
            self.assertEqual((d[name]["kind"], d[name]["classes"], d[name]["trains"]), (kind, classes, trains), name)
        self.assertEqual(d["gemini"]["model"], "gemini-3-flash-preview")
        self.assertEqual(d["groq_qwen"]["model"], "qwen/qwen3.8-27b")
        self.assertEqual(d["groq_qwen"]["endpoint"], d["groq"]["endpoint"])
        self.assertEqual(d["groq_qwen"]["key_file"], d["groq"]["key_file"])
        self.assertEqual((d["groq_qwen"]["pacer"], d["groq"]["pacer"]), ("groq_qwen", "groq"))   # limits are per model
        self.assertEqual(pr.DEFAULT_CONFIG["pacers"], {"groq": {"tpm_limit": 7000}, "groq_qwen": {"tpm_limit": 7000}})
        self.assertEqual((d["gemini"]["timeout"], d["gemini"]["thinking_level"]), (180, "low"))
        self.assertEqual((d["gemini"]["retries_429"], d["gemini"]["max_retry_wait"]), (1, 60))
        self.assertFalse({"retries_429", "max_retry_wait"} & set(d["groq"]))   # Groq keeps one retry, 30 s cap
        self.assertEqual(d["groq"]["extra"], {"reasoning_effort": "low"})   # gpt-oss otherwise burns its token cap thinking
        self.assertNotIn("timeout", d["groq"])   # the others keep the top-level timeout
        self.assertEqual(d["cloudflare"]["endpoint"], "https://api.cloudflare.com/client/v4/accounts/"
                                                      "{account_id}/ai/v1/chat/completions")
        self.assertEqual(d["cloudflare"]["model"], "@cf/qwen/qwen2.5-coder-32b-instruct")
        self.assertEqual(d["cloudflare"]["key_file"], "~/.config/cloudflare/api-token")
        self.assertEqual(d["cloudflare"]["vars"], {"account_id": "~/.config/cloudflare/account-id"})
        self.assertEqual((d["antigravity"]["cmd"], d["antigravity"]["enabled_if"]),
                         (["agy", "--print-timeout", "120s", "-p={prompt}"], ["which:agy", "exists:" + AGY_TOKEN]))
        self.assertNotIn("model", d["antigravity"])   # --model is opt-in
        self.assertEqual((d["grok"]["cmd"], d["grok"]["enabled_if"]), (["grok", "-p"], "which:grok"))
        self.assertEqual(d["antigravity"]["enabled_if"], ["which:agy", "exists:" + AGY_TOKEN])   # never read
        self.assertEqual([n for n, e in d.items() if "enabled" in e], [])   # nothing ships switched off
        self.assertTrue(d["openrouter"]["model"].endswith(":free"))
        self.assertEqual(d["openrouter"]["key_file"], "~/.config/openrouter/api-key")
        self.assertEqual(d["local"]["endpoint"], "http://127.0.0.1:11434")
        self.assertEqual(d["local"]["num_ctx"], 16384)

    def test_experts_override_by_name_field_by_field(self):
        cfg, status = self.load({"experts": [{"name": "groq", "model": "other/model"},
                                             {"name": "grok", "cmd": ["g", "--ask"]}]})
        d = self.by_name(cfg)
        self.assertEqual(status, "file")
        self.assertEqual(d["groq"]["model"], "other/model")
        self.assertEqual(d["groq"]["endpoint"], self.by_name(pr.DEFAULT_CONFIG)["groq"]["endpoint"])
        self.assertEqual(d["grok"]["cmd"], ["g", "--ask"])
        self.assertEqual(list(d), ALL_EXPERTS)   # order and membership unchanged
        self.assertEqual(self.by_name(pr.DEFAULT_CONFIG)["groq"]["model"], "openai/gpt-oss-120b")   # not mutated

    def test_a_new_expert_is_appended_and_a_bad_one_is_dropped_with_a_flag(self):
        cfg, status = self.load({"experts": [
            {"name": "mistral", "kind": "openai_compat", "model": "m", "classes": ["public"]},
            {"name": "bad kind", "kind": "openai_compat", "classes": []},
            {"name": "nokind", "classes": ["public"]},
            {"name": "MAJORITY", "kind": "cli", "cmd": ["x"], "classes": ["public"]},
            {"name": "groq", "classes": "public"}]})
        names = list(self.by_name(cfg))
        self.assertIn("mistral", names)
        for gone in ("bad kind", "nokind", "MAJORITY", "groq"):   # the last has malformed classes
            self.assertNotIn(gone, names)
        self.assertEqual(status, "invalid")

    def test_the_old_config_shape_is_still_honoured(self):
        cfg, status = self.load({"groq": {"model": "old/model", "tpm_limit": 5000},
                                 "gemini": {"model": "gemini-x"},
                                 "local": {"base_url": "http://127.0.0.1:9999", "num_ctx": 8192}})
        d = self.by_name(cfg)
        self.assertEqual((status, d["groq"]["model"], d["gemini"]["model"]), ("file", "old/model", "gemini-x"))
        self.assertEqual(cfg["pacers"]["groq"]["tpm_limit"], 5000)
        self.assertEqual((d["local"]["endpoint"], d["local"]["num_ctx"]), ("http://127.0.0.1:9999", 8192))

    def test_pacers_and_top_level_values_override(self):
        cfg, _ = self.load({"timeout": 30, "pacers": {"groq": {"tpm_limit": 3000}}, "_comment": "ignored"})
        self.assertEqual((cfg["timeout"], cfg["pacers"]["groq"]["tpm_limit"]), (30, 3000))
        self.assertNotIn("_comment", cfg)

    def test_invalid_file_falls_back(self):
        self.assertEqual(self.load("{nope"), (pr.DEFAULT_CONFIG, "invalid"))
        self.assertEqual(self.load("[1]"), (pr.DEFAULT_CONFIG, "invalid"))

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
        # Safety net: nothing in this suite may ever write the real ~/.local/share/ledger/panel.jsonl.
        self.ledger = os.path.join(self.tmp, "ledger", "panel.jsonl")
        patch = mock.patch.object(ledger_log, "DEFAULT_PATH", self.ledger)
        patch.start()
        self.addCleanup(patch.stop)
        self._experts = None

    def deps(self, **kw):
        return make_deps(self.net, self.clock, self.runner, **kw)

    def ex(self, name):
        """The configured expert `name`; every call shares one pacer dict, like a real run."""
        if self._experts is None:
            self._experts = pr.make_experts(self.cfg)
        return self._experts[name]

    def spec(self, name):
        """The mutable config entry for `name` (experts are built from these same dicts)."""
        return next(e for e in self.cfg["experts"] if e["name"] == name)

    def review(self, name, diff=DIFF, **dep_kw):
        return pr.review_with(self.ex(name), self.cfg, self.deps(**dep_kw), SPEC, ACCEPT, diff)

    def ledger_lines(self):
        if not os.path.exists(self.ledger):
            return []
        return [json.loads(line) for line in Path(self.ledger).read_text().splitlines()]

    def many_files(self, n, size):
        return "".join(f"diff --git a/f{i}.py b/f{i}.py\n+++ b/f{i}.py\n" + ("+x = 1\n" * (size // 7))
                       for i in range(n))


class GroqTests(Base):
    def test_request_shape_and_key(self):
        r = self.review("groq")
        self.assertEqual(r["verdict"], "pass")
        post = self.net.posts[0]
        self.assertEqual(post["url"], "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(post["headers"], {"Authorization": "Bearer " + KEYS["groq"]})
        self.assertEqual(post["payload"]["model"], "openai/gpt-oss-120b")
        self.assertEqual(post["payload"]["max_completion_tokens"], 1500)
        self.assertNotIn("max_tokens", post["payload"])
        self.assertEqual(post["timeout"], 120)

    def test_groq_qwen_uses_the_same_endpoint_and_key_with_its_own_model(self):
        r = self.review("groq_qwen")
        self.assertEqual(r["verdict"], "pass")
        post = self.net.posts[0]
        self.assertEqual((post["who"], post["url"]), ("groq_qwen", "https://api.groq.com/openai/v1/chat/completions"))
        self.assertEqual(post["headers"], {"Authorization": "Bearer " + KEYS["groq"]})
        self.assertEqual(post["payload"]["model"], "qwen/qwen3.8-27b")

    def test_two_experts_naming_one_pacer_group_draw_on_one_budget(self):
        self.spec("groq_qwen")["pacer"] = "groq"   # the old shape; the defaults now give each model its own
        diff = self.many_files(1, 15000)      # one chunk of about 4K prompt tokens (+1500 reserved)
        deps = self.deps()
        self.assertIs(self.ex("groq")._pacer(self.cfg, deps), self.ex("groq_qwen")._pacer(self.cfg, deps))
        self.review("groq", diff)
        self.assertEqual(self.clock.sleeps, [], "the first Groq call fits the minute")
        self.review("groq_qwen", diff)
        self.assertTrue(self.clock.sleeps, "the second Groq expert must wait for the first one's spend")
        spent = [(p["t"], pr.est_tokens(p["payload"]["messages"][0]["content"]) + 1500) for p in self.net.posts]
        self.assertEqual({p["who"] for p in self.net.posts}, {"groq", "groq_qwen"})
        self.assertLessEqual(max_window(spent), 7000)

    def test_two_runs_of_separate_pacers_would_overspend_the_minute(self):
        # the control for the test above: without a shared pacer the same two calls land in one minute
        diff = self.many_files(1, 15000)
        for name in ("groq", "groq_qwen"):
            pr.review_with(pr.make_experts(self.cfg)[name], self.cfg, self.deps(), SPEC, ACCEPT, diff)
        self.assertEqual(self.clock.sleeps, [])
        spent = [(p["t"], pr.est_tokens(p["payload"]["messages"][0]["content"]) + 1500) for p in self.net.posts]
        self.assertGreater(max_window(spent), 7000)

    def test_a_non_groq_expert_is_never_paced(self):
        for name in ("cloudflare", "openrouter", "gemini", "local"):
            self.review(name, self.many_files(1, 15000))
        self.assertEqual(self.clock.sleeps, [])

    def test_chunks_stay_near_5k_tokens_and_no_minute_exceeds_7k(self):
        diff = self.many_files(8, 7500)
        r = self.review("groq", diff)
        self.assertGreaterEqual(r["chunks"], 3)
        spent = []
        for p in self.net.posts:
            prompt = p["payload"]["messages"][0]["content"]
            self.assertLessEqual(pr.est_tokens(prompt), 5001)
            spent.append((p["t"], pr.est_tokens(prompt) + self.spec("groq")["max_completion_tokens"]))
        self.assertLessEqual(max_window(spent), 7000)
        self.assertTrue(self.clock.sleeps, "back-to-back 5K chunks must have been paced")
        # every chunk of the diff was actually sent, once
        sent = "".join(p["payload"]["messages"][0]["content"] for p in self.net.posts)
        for i in range(8):
            self.assertEqual(sent.count(f"+++ b/f{i}.py"), 1)

    def test_small_chunks_share_a_minute_without_waiting(self):
        self.spec("groq")["chunk_tokens"] = 1500
        r = self.review("groq", self.many_files(2, 4000))
        self.assertEqual((r["chunks"], self.clock.sleeps), (2, []))

    def test_real_usage_above_the_estimate_delays_the_next_chunk(self):
        self.spec("groq")["chunk_tokens"] = 1500
        self.net.usage = 6900
        self.review("groq", self.many_files(2, 4000))
        self.assertTrue(self.clock.sleeps)

    def test_real_usage_from_one_expert_delays_another_in_the_same_group(self):
        self.spec("groq_qwen")["pacer"] = "groq"
        self.net.usage = 6900
        self.review("groq")
        self.review("groq_qwen")
        self.assertTrue(self.clock.sleeps)

    def test_revise_if_any_chunk_revises(self):
        self.net.answers["groq"] = lambda payload, n: REVISE if n == 2 else PASS
        r = self.review("groq", self.many_files(8, 7500))
        self.assertEqual((r["verdict"], r["blocking"]), ("revise", ["off by one"]))

    def test_one_retry_on_429_then_success(self):
        self.net.answers["groq"] = lambda payload, n: (429, {}) if n == 1 else PASS
        r = self.review("groq")
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(self.net.calls["groq"], 2)
        self.assertIn(self.spec("groq")["retry_wait"], self.clock.sleeps)

    def test_second_429_is_an_error_with_a_content_free_note(self):
        self.net.answers["groq"] = (429, {})
        r = self.review("groq")
        self.assertEqual((r["verdict"], r["note"]), ("error", "HTTP_429"))

    def test_available_only_with_a_key(self):
        self.assertTrue(self.ex("groq").available(self.cfg, self.deps()))
        self.assertFalse(self.ex("groq").available(self.cfg, self.deps(keys={})))
        self.assertFalse(self.ex("groq_qwen").available(self.cfg, self.deps(keys={})))

    def test_chunk_cap_is_flagged_not_silent(self):
        self.cfg["max_chunks"] = 2
        r = self.review("groq", self.many_files(8, 7500))
        self.assertEqual((r["chunks"], r["truncated"]), (2, True))


class GeminiTests(Base):
    def test_request_shape(self):
        r = self.review("gemini")
        self.assertEqual(r["verdict"], "pass")
        post = self.net.posts[0]
        self.assertEqual(post["url"], "https://generativelanguage.googleapis.com/v1beta/models/"
                                      "gemini-3-flash-preview:generateContent")
        self.assertEqual(post["headers"], {"x-goog-api-key": KEYS["gemini"]})
        self.assertIn("DIFF", post["payload"]["contents"][0]["parts"][0]["text"])

    def test_model_is_configurable_and_parts_are_joined(self):
        self.spec("gemini")["model"] = "gemini-x"
        self.net.answers["gemini"] = lambda p, n: (200, {"candidates": [{"content": {"parts": [
            {"text": '{"verdict":"re'}, {"text": 'vise","blocking":["a"]}'}]}}]})
        r = self.review("gemini")
        self.assertIn("gemini-x:generateContent", self.net.posts[0]["url"])
        self.assertEqual(r["verdict"], "revise")

    def test_available_only_with_a_key(self):
        self.assertFalse(self.ex("gemini").available(self.cfg, self.deps(keys={"groq": "k"})))


class CloudflareTests(Base):
    def test_request_shape_endpoint_token_and_account_id(self):
        r = self.review("cloudflare")
        post = self.net.posts[0]
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(post["url"], f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"
                                      "/ai/v1/chat/completions")
        self.assertEqual(post["headers"], {"Authorization": "Bearer " + KEYS["cloudflare"]})
        self.assertEqual(post["payload"]["model"], "@cf/qwen/qwen2.5-coder-32b-instruct")
        self.assertEqual(post["payload"]["max_tokens"], 1500)
        self.assertNotIn("max_completion_tokens", post["payload"])

    def test_unavailable_without_the_token_or_without_the_account_id(self):
        ex = self.ex("cloudflare")
        self.assertTrue(ex.available(self.cfg, self.deps()))
        self.assertFalse(ex.available(self.cfg, self.deps(keys={})))   # no token
        read = self.deps().read_key
        no_account = lambda p: None if p.endswith("account-id") else read(p)   # noqa: E731
        d = self.deps()
        d.read_key = no_account
        self.assertFalse(ex.available(self.cfg, d))
        self.assertIsNone(ex._url(d), "an unresolved {account_id} must never reach the wire")


class OpenRouterTests(Base):
    def test_request_shape_and_free_model(self):
        r = self.review("openrouter")
        post = self.net.posts[0]
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(post["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(post["headers"], {"Authorization": "Bearer " + KEYS["openrouter"]})
        self.assertTrue(post["payload"]["model"].endswith(":free"))

    def test_unavailable_without_a_key(self):
        self.assertFalse(self.ex("openrouter").available(self.cfg, self.deps(keys={"groq": "k"})))


def cli_prompt(call):
    """The prompt a CLI expert was given: its last argument, minus the attached `-p=` if there is one."""
    arg = call["cmd"][-1]
    return arg[3:] if arg.startswith("-p=") else arg


class GrokTests(Base):
    def test_prompt_is_the_last_argument(self):
        r = self.review("grok")
        call = self.runner.calls[0]
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(call["cmd"][:2], ["grok", "-p"])
        self.assertIn("ACCEPTANCE", call["cmd"][-1])
        self.assertEqual(call["timeout"], 120)

    def test_antigravity_command_puts_dash_p_last_with_the_prompt_attached(self):
        # Verified against agy v1.2.14: `agy -p --print-timeout 120s PROMPT` fails ("-p took
        # --print-timeout as its prompt"), because -p consumes the NEXT argument. So flags first, prompt attached.
        r = self.review("antigravity")
        call = self.runner.calls[0]
        self.assertEqual((r["verdict"], call["cmd"][:3]), ("pass", ["agy", "--print-timeout", "120s"]))
        self.assertEqual(len(call["cmd"]), 4)
        self.assertTrue(call["cmd"][3].startswith("-p="))
        self.assertIn("ACCEPTANCE", call["cmd"][3])
        self.assertEqual(call["timeout"], 120)   # the subprocess timeout is enforced as well

    def test_no_stdin_mode_the_prompt_always_travels_in_argv(self):
        self.spec("grok")["stdin"] = True          # an old config option: ignored now
        self.review("grok")
        call = self.runner.calls[0]
        self.assertEqual(call["cmd"][:2], ["grok", "-p"])
        self.assertIn("SPEC:", call["cmd"][-1])
        self.assertNotIn("stdin", call)

    def test_prompt_placeholder_template(self):
        self.spec("grok")["cmd"] = ["grok", "--prompt", "{prompt}", "--json"]
        self.review("grok")
        cmd = self.runner.calls[0]["cmd"]
        self.assertEqual((cmd[0], cmd[1], cmd[3]), ("grok", "--prompt", "--json"))
        self.assertIn("DIFF", cmd[2])

    def test_noisy_cli_output_is_tolerated_and_failure_is_an_error(self):
        self.runner.reply = 'thinking...\n```json\n{"verdict":"revise","blocking":["x"]}\n```\ndone'
        self.assertEqual(self.review("grok")["verdict"], "revise")
        self.runner.rc = 1
        r = self.review("grok")
        self.assertEqual((r["verdict"], r["note"]), ("error", "EXIT_1"))

    def test_available_only_if_the_binary_is_on_path(self):
        self.assertTrue(self.ex("grok").available(self.cfg, self.deps(installed=("grok",))))
        self.assertFalse(self.ex("grok").available(self.cfg, self.deps(installed=())))
        self.spec("grok")["cmd"] = ["mygrok", "-p"]
        self.assertFalse(self.ex("grok").available(self.cfg, self.deps(installed=("grok",))))

    def test_enabled_if_gates_an_expert_whose_binary_is_present(self):
        self.assertFalse(self.ex("antigravity").available(self.cfg, self.deps(installed=("grok",))))
        self.assertTrue(self.ex("antigravity").available(self.cfg, self.deps(installed=("agy",))))
        self.spec("antigravity")["enabled_if"] = "which:something-else"   # on PATH is not enough
        self.assertFalse(self.ex("antigravity").available(self.cfg, self.deps(installed=("agy",))))
        self.spec("antigravity")["enabled_if"] = "weird:thing"            # unknown condition: not met
        self.assertFalse(self.ex("antigravity").available(self.cfg, self.deps(installed=("agy",))))
        self.spec("antigravity")["enabled_if"] = "file:/x/cloudflare/api-token"
        self.assertTrue(self.ex("antigravity").available(self.cfg, self.deps(installed=("agy",))))
        self.assertFalse(self.ex("antigravity").available(self.cfg, self.deps(installed=("agy",), keys={})))

    def test_antigravity_runs_only_when_its_oauth_token_file_exists(self):
        # On a signed-out machine every `agy -p` opens a browser OAuth flow, so signed out means agy is NOT run.
        signed_out = self.deps(installed=("agy", "grok"), existing=())
        ex = self.ex("antigravity")
        self.assertFalse(ex.available(self.cfg, signed_out))
        self.assertEqual(self.runner.calls, [])
        signed_in = self.deps(installed=("agy", "grok"), existing=(AGY_TOKEN,))
        self.assertTrue(ex.available(self.cfg, signed_in))
        self.assertFalse(ex.available(self.cfg, self.deps(installed=("grok",), existing=(AGY_TOKEN,))))   # no agy
        self.assertFalse(ex.available(self.cfg, self.deps(installed=("agy",), existing=("/some/other/file",))))
        self.assertEqual(self.runner.calls, [], "availability never runs anything")

    def test_the_token_is_only_checked_for_existence_never_read(self):
        d = self.deps(installed=("agy",), existing=(AGY_TOKEN,))
        reads = []
        d.read_key = lambda path: reads.append(path)
        d.run_cmd = lambda *a, **k: self.fail("availability must not run a command")
        self.assertTrue(self.ex("antigravity").available(self.cfg, d))
        self.assertEqual(reads, [], "the OAuth token file must never be opened")

    def test_enabled_false_wins_even_when_signed_in(self):
        self.spec("antigravity")["enabled"] = False
        self.assertFalse(self.ex("antigravity").available(self.cfg, self.deps(installed=("agy",))))
        self.assertEqual(self.runner.calls, [])

    def test_enabled_false_switches_off_any_expert(self):
        for name in ("groq", "gemini", "cloudflare", "local", "grok"):
            self.assertTrue(self.ex(name).available(self.cfg, self.deps()), name)
            probes = len(self.net.gets)
            self.spec(name)["enabled"] = False
            self.assertFalse(pr.make_experts(self.cfg)[name].available(self.cfg, self.deps()), name)
            self.assertEqual(len(self.net.gets), probes, f"a disabled {name} must not be probed")

    def test_exists_probe_and_list_conditions_read_nothing_and_run_nothing(self):
        token = "~/.gemini/antigravity-cli/some-token-file"
        self.spec("antigravity")["enabled_if"] = ["which:agy", f"exists:{token}"]
        d = self.deps(installed=("agy",), existing=())
        d.read_key = d.run_cmd = lambda *a, **k: self.fail("a probe must not read a file or run a command")
        ex = self.ex("antigravity")
        self.assertFalse(ex.available(self.cfg, d))                                  # not signed in: no token file
        d.exists = lambda path: path == token
        self.assertTrue(ex.available(self.cfg, d))                                   # signed in: it exists
        d.which = lambda name: None
        self.assertFalse(ex.available(self.cfg, d))                                  # every item must hold
        self.assertTrue(pr.condition_met([], d) and pr.condition_met(None, d))
        self.assertFalse(pr.condition_met(["which:agy", "bogus:x"], self.deps(installed=("agy",))))
        self.assertEqual(self.runner.calls, [])

    def test_model_flag_is_only_passed_when_a_model_is_set(self):
        self.review("antigravity")
        self.assertNotIn("--model", self.runner.calls[0]["cmd"])
        self.spec("antigravity")["model"] = "gemini-x"
        self.review("antigravity")
        cmd = self.runner.calls[1]["cmd"]
        self.assertEqual(cmd[:6], ["agy", "--model", "gemini-x", "--print-timeout", "120s", cmd[5]])
        self.assertEqual(self.ex("antigravity").model(), "gemini-x")
        self.assertEqual(pr.make_experts(pr.DEFAULT_CONFIG)["antigravity"].model(), "agy")   # logged as the binary
        self.spec("grok")["model"], self.spec("grok")["model_flag"] = "grok-x", "-m"
        self.review("grok")
        self.assertEqual(self.runner.calls[2]["cmd"][:4], ["grok", "-m", "grok-x", "-p"])

    def test_an_auto_approve_argument_is_refused_before_anything_runs(self):
        for bad in ("--dangerously-skip-permissions", "--yolo", "--auto-approve", "--autoapprove", "--allow-all",
                    "--yes", "-y", "--mode=accept-edits", "--trust-all-tools", "--bypass-permissions"):
            self.spec("antigravity")["cmd"] = ["agy", bad, "-p={prompt}"]
            ex = pr.make_experts(self.cfg)["antigravity"]
            self.assertFalse(ex.available(self.cfg, self.deps()), bad)
            r = pr.review_with(ex, self.cfg, self.deps(), SPEC, ACCEPT, DIFF)   # even if called anyway
            self.assertEqual((r["verdict"], r["note"]), ("error", "UNSAFE_FLAG"), bad)
        self.spec("antigravity")["cmd"] = ["agy", "--mode", "accept-edits", "-p={prompt}"]   # as a separate value
        self.assertFalse(pr.make_experts(self.cfg)["antigravity"].available(self.cfg, self.deps()))
        self.assertEqual(self.runner.calls, [], "no unsafe command was ever executed")
        for e in pr.DEFAULT_CONFIG["experts"]:   # and the shipped defaults carry none
            if e["kind"] == "cli":
                self.assertFalse(pr.unsafe_cli(e["cmd"]), e["name"])

    def test_the_prompt_text_itself_may_mention_those_words(self):
        r = self.run_review_with_diff("+ # use --dangerously-skip-permissions --yolo here\n")
        self.assertEqual(r["verdict"], "pass")
        self.assertIn("--yolo", self.runner.calls[0]["cmd"][-1])

    def run_review_with_diff(self, diff):
        return pr.review_with(self.ex("grok"), self.cfg, self.deps(), SPEC, ACCEPT, diff)

    def test_an_oversize_prompt_is_an_error_never_a_stdin_fallback(self):
        self.spec("grok")["chunk_chars"] = 400_000
        diff = "diff --git a/f b/f\n" + ("+é" * 80_000 + "\n")        # 2 bytes per char: ~320 KB in argv
        r = self.run_review_with_diff(diff)
        self.assertTrue(all(len(c["cmd"][-1].encode()) <= pr.MAX_ARGV_BYTES for c in self.runner.calls))
        self.assertGreater(r["chunks"], 1, "the chunk size is capped so each prompt fits one argv string")
        direct = self.ex("grok")
        with self.assertRaises(pr.ExpertError):
            direct.ask("é" * (pr.MAX_ARGV_BYTES // 2 + 10), self.cfg, self.deps())

    def test_a_timeout_stops_asking_that_expert_for_the_remaining_chunks(self):
        import subprocess
        calls = []

        def hang(cmd, timeout):
            calls.append(cmd)
            raise subprocess.TimeoutExpired(cmd, timeout)
        deps = self.deps()
        deps.run_cmd = hang
        r = pr.review_with(self.ex("antigravity"), self.cfg, deps, SPEC, ACCEPT, self.many_files(8, 20000))
        self.assertGreater(r["chunks"], 1)
        self.assertEqual((len(calls), r["verdict"]), (1, "error"))
        self.assertIn("TimeoutExpired", r["note"])
        self.assertIn("later chunks skipped", r["note"])


class RealRunCmdTests(unittest.TestCase):
    """real_run_cmd with real (local, offline) processes: python itself is the stand-in CLI."""

    def run_py(self, code, timeout=20):
        return pr.real_run_cmd([sys.executable, "-c", code], timeout)

    def test_cwd_is_a_fresh_empty_temp_dir_that_is_deleted_afterwards(self):
        here = os.getcwd()
        rc, out = self.run_py("import os; print(os.getcwd()); print(len(os.listdir('.')))")
        cwd, count = out.split()
        self.assertEqual((rc, count), (0, "0"))
        self.assertNotEqual(os.path.realpath(cwd), os.path.realpath(here))
        self.assertTrue(os.path.basename(cwd).startswith("panel-cli-"))
        self.assertFalse(os.path.exists(cwd), "the temp dir must be gone after the run")
        self.assertEqual(os.getcwd(), here)

    def test_each_run_gets_its_own_dir_and_files_it_writes_do_not_survive(self):
        _, a = self.run_py("import os; open('leak.txt','w').write('x'); print(os.getcwd())")
        _, b = self.run_py("import os; print(os.listdir('.')); print(os.getcwd())")
        self.assertNotEqual(a.strip(), b.splitlines()[-1])
        self.assertEqual(b.splitlines()[0], "[]")
        self.assertFalse(os.path.exists(a.strip()))

    def test_stdin_is_dev_null_so_a_prompt_for_permission_cannot_hang(self):
        rc, out = self.run_py("import sys; print(repr(sys.stdin.read())); print(sys.stdin.isatty())", timeout=10)
        self.assertEqual((rc, out.split()), (0, ["''", "False"]))

    def test_the_command_is_an_argv_list_never_a_shell(self):
        rc, out = pr.real_run_cmd([sys.executable, "-c", "import sys; print(sys.argv[1])", "$(echo hi); `id` | cat"], 20)
        self.assertEqual(out.strip(), "$(echo hi); `id` | cat")   # metacharacters arrive literally
        with mock.patch.object(pr.subprocess, "Popen") as popen:
            popen.return_value.communicate.return_value = ("", None)
            popen.return_value.returncode = 0
            pr.real_run_cmd(["agy", "-p"], 5)
        kw = popen.call_args.kwargs
        self.assertIs(kw["shell"], False)
        self.assertEqual(kw["stdin"], pr.subprocess.DEVNULL)
        self.assertTrue(kw["start_new_session"])
        self.assertIsInstance(popen.call_args.args[0], list)
        self.assertTrue(os.path.basename(kw["cwd"]).startswith("panel-cli-"))

    def test_a_timeout_kills_the_whole_process_group_and_still_removes_the_dir(self):
        import time
        pidfile = os.path.join(tempfile.mkdtemp(), "grandchild.pid")
        self.addCleanup(shutil.rmtree, os.path.dirname(pidfile), True)
        code = ("import os, subprocess, sys, time\n"
                "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({pidfile!r}, 'w').write(str(g.pid))\n"
                "print(os.getcwd(), flush=True); time.sleep(60)")
        seen = {}
        real_popen = pr.subprocess.Popen

        def spy(*a, **kw):
            seen["cwd"] = kw["cwd"]
            return real_popen(*a, **kw)
        with mock.patch.object(pr.subprocess, "Popen", spy):
            with self.assertRaises(pr.subprocess.TimeoutExpired):
                pr.real_run_cmd([sys.executable, "-c", code], 2)
        self.assertFalse(os.path.exists(seen["cwd"]))
        grandchild = int(Path(pidfile).read_text())
        alive = True
        for _ in range(40):   # up to 2 s for the kernel to reap it
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                alive = False
                break
            time.sleep(0.05)
        if alive:
            os.kill(grandchild, 9)
        self.assertFalse(alive, "a grandchild the CLI spawned must die with it")


class LocalTests(Base):
    def test_available_when_tags_answer_within_2s(self):
        self.assertTrue(self.ex("local").available(self.cfg, self.deps()))
        self.assertEqual(self.net.gets, [("http://127.0.0.1:11434/api/tags", 2)])

    def test_unavailable_when_tags_fail(self):
        self.net.tags_ok = False
        self.assertFalse(self.ex("local").available(self.cfg, self.deps()))

    def test_dry_run_probe_makes_no_call(self):
        self.assertTrue(self.ex("local").available(self.cfg, self.deps(), probe=False))
        self.assertEqual(self.net.gets, [])

    def test_chat_request_shape(self):
        r = self.review("local")
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
    def run_panel(self, *extra, diff=DIFF, cls="public", spec=SPEC, scanner=CLEAN, experts=None,
                  config=None, signed_in=True, **dep_kw):
        """signed_in=False simulates a machine with no agy OAuth token file (agy must then never run)."""
        if not signed_in:
            dep_kw.setdefault("existing", ())
        paths = {}
        for name, text in (("spec", spec), ("acc", ACCEPT), ("diff", diff), ("terms", "Acme Corp\n")):
            paths[name] = os.path.join(self.tmp, name + ".txt")
            Path(paths[name]).write_text(text, encoding="utf-8")
        cfg_path = os.path.join(self.tmp, "no-config.json")
        if config is not None:
            cfg_path = os.path.join(self.tmp, "config.json")
            Path(cfg_path).write_text(json.dumps(config))
        argv = ["--spec", paths["spec"], "--acceptance", paths["acc"], "--diff", paths["diff"],
                "--class", cls, "--terms-file", paths["terms"], "--config", cfg_path, *extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pr.main(argv, deps=self.deps(scanner=scanner, **dep_kw), experts=experts)
        return Result(buf.getvalue(), rc)

    def called(self):
        """Names of the experts that actually received a request, in panel order."""
        cli = {"grok": "grok", "agy": "antigravity"}
        hit = {n for n in API_EXPERTS if self.net.calls[n]} | {cli[c["cmd"][0]] for c in self.runner.calls}
        return [n for n in ALL_EXPERTS if n in hit]

    # routing ---------------------------------------------------------------
    def test_public_asks_every_expert(self):
        r = self.run_panel()
        self.assertEqual([r.lines[f"PANEL_{n}"] for n in ALL_EXPERTS], ["pass"] * 8)
        self.assertEqual(self.called(), ALL_EXPERTS)
        self.assertEqual((r.lines["PANEL_MAJORITY"], r.rc), ("pass", 0))
        self.assertEqual(r.lines["GATE"], "send")
        self.assertEqual(r.json["allowed"], ALL_EXPERTS)

    def test_own_reaches_only_the_experts_that_do_not_train(self):
        r = self.run_panel(cls="own")
        self.assertEqual(self.called(), ["groq", "groq_qwen", "cloudflare", "local"])
        for n in TRAINING:
            self.assertNotIn(f"PANEL_{n}", r.lines)
        self.assertEqual(r.json["allowed"], ["groq", "groq_qwen", "cloudflare", "local"])

    def test_client_is_local_only(self):
        r = self.run_panel(cls="client")
        self.assertEqual(self.called(), ["local"])
        self.assertEqual((r.lines["GATE"], r.lines["PANEL_local"]), ("local", "pass"))
        self.assertEqual(r.json["allowed"], ["local"])
        self.assertEqual([k for k in r.lines if k.startswith("PANEL_") and k[6:] in ALL_EXPERTS], ["PANEL_local"])

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
        for n in ALL_EXPERTS:
            self.assertEqual(r.lines[f"PANEL_{n}"], "unavailable")
        self.assertEqual((r.lines["PANEL_MAJORITY"], r.rc), ("none", 0))
        self.assertEqual(self.net.posts, [])
        self.assertEqual(self.ledger_lines(), [], "no call, no usage line")

    def test_cloudflare_without_a_token_is_unavailable_and_the_rest_still_answer(self):
        keys = {k: v for k, v in KEYS.items() if k != "cloudflare"}
        r = self.run_panel(cls="own", keys=keys)
        self.assertEqual((r.lines["PANEL_cloudflare"], r.lines["PANEL_groq"]), ("unavailable", "pass"))
        self.assertEqual(self.called(), ["groq", "groq_qwen", "local"])
        self.assertEqual(r.lines["PANEL_MAJORITY"], "pass")

    def test_split_when_experts_disagree(self):
        self.net.answers["groq"] = self.net.answers["groq_qwen"] = REVISE
        self.net.tags_ok = False
        r = self.run_panel(keys={"groq": "k"}, installed=("grok", "agy"))
        self.assertEqual((r.lines["PANEL_groq"], r.lines["PANEL_groq_qwen"]), ("revise", "revise"))
        self.assertEqual((r.lines["PANEL_grok"], r.lines["PANEL_antigravity"]), ("pass", "pass"))
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
        for w in API_EXPERTS:
            self.net.answers[w] = (503, {})
        self.runner.rc = 2
        r = self.run_panel()
        self.assertEqual((r.lines["PANEL_MAJORITY"], r.rc), ("none", 0))
        self.assertEqual([r.lines[f"PANEL_{n}"] for n in ALL_EXPERTS], ["error"] * 8)

    def test_sentinels_are_distinct_for_experts_whose_names_share_a_prefix(self):
        self.net.answers["groq_qwen"] = REVISE
        r = self.run_panel(cls="own")
        self.assertEqual((r.lines["PANEL_groq"], r.lines["PANEL_groq_qwen"]), ("pass", "revise"))
        for line in r.stdout.splitlines():
            if line.startswith("__PANEL_"):
                self.assertRegex(line, r"^__PANEL_[A-Za-z0-9_]+=[^=]*__$")

    def test_internal_failure_still_exits_zero(self):
        r = self.run_panel(experts={"nope": None})   # experts mapping broken: a bug in the tool, not the diff
        self.assertEqual((r.rc, r.lines["PANEL_MAJORITY"]), (0, "none"))
        self.assertIn("internal error", r.lines["PANEL_REASON"])

    # dry run -----------------------------------------------------------------
    def test_dry_run_makes_no_calls_at_all(self):
        r = self.run_panel("--dry-run")
        self.assertEqual((self.net.posts, self.net.gets, self.runner.calls), ([], [], []))
        self.assertEqual([r.lines[f"PANEL_{n}"] for n in ALL_EXPERTS], ["would_call"] * 8)
        self.assertEqual(r.lines["PANEL_DRYRUN"], ",".join(ALL_EXPERTS))
        self.assertEqual((r.lines["GATE"], r.lines["PANEL_MAJORITY"], r.rc), ("send", "none", 0))
        self.assertTrue(r.json["dry_run"])
        self.assertEqual(self.ledger_lines(), [], "a dry run spends nothing, so it logs nothing")

    def test_dry_run_reports_unconfigured_experts_and_respects_routing(self):
        r = self.run_panel("--dry-run", cls="own", keys={"groq": "k"})
        self.assertEqual(r.lines["PANEL_DRYRUN"], "groq,groq_qwen,local")
        self.assertEqual(r.lines["PANEL_cloudflare"], "unavailable")
        r = self.run_panel("--dry-run", keys={}, installed=())
        self.assertEqual((r.lines["PANEL_groq"], r.lines["PANEL_grok"]), ("unavailable", "unavailable"))
        self.assertEqual(r.lines["PANEL_DRYRUN"], "local")

    # privacy -----------------------------------------------------------------
    def test_only_the_redacted_diff_reaches_any_transport(self):
        r = self.run_panel()
        wire = json.dumps(self.net.posts) + json.dumps(self.runner.calls)
        self.assertEqual(len(self.net.posts), 6)       # groq, groq_qwen, gemini, cloudflare, openrouter, local
        self.assertEqual(len(self.runner.calls), 2)    # antigravity, grok
        for original in ORIGINALS:
            self.assertNotIn(original, wire)
            self.assertNotIn(original, r.stdout)
            self.assertNotIn(original, Path(self.ledger).read_text())
        self.assertIn("[EMAIL_1]", wire)
        self.assertIn("[PHONE_1]", wire)

    def test_every_expert_gets_the_same_prompt(self):
        self.run_panel()
        prompts = {p["who"]: (p["payload"]["contents"][0]["parts"][0]["text"] if p["who"] == "gemini"
                              else p["payload"]["messages"][0]["content"]) for p in self.net.posts}
        for c in self.runner.calls:
            prompts[c["cmd"][0]] = cli_prompt(c)
        self.assertEqual(len(prompts), 8)
        self.assertEqual(len(set(prompts.values())), 1)
        p = prompts["groq"]
        for part in ("SPEC:", "ACCEPTANCE:", "DIFF:", "prints hello", '"verdict":"pass"|"revise"'):
            self.assertIn(part, p)

    def test_keys_go_in_headers_and_never_in_output(self):
        r = self.run_panel("--repo", "ScriptHammer")
        heads = {p["who"]: p["headers"] for p in self.net.posts}
        bearer = lambda k: {"Authorization": "Bearer " + KEYS[k]}   # noqa: E731
        self.assertEqual(heads["groq"], bearer("groq"))
        self.assertEqual(heads["groq_qwen"], bearer("groq"))
        self.assertEqual(heads["cloudflare"], bearer("cloudflare"))
        self.assertEqual(heads["openrouter"], bearer("openrouter"))
        self.assertEqual(heads["gemini"], {"x-goog-api-key": KEYS["gemini"]})
        self.assertEqual(heads["local"], {})
        shown = r.stdout + Path(self.ledger).read_text()
        for secret in (*KEYS.values(), ACCOUNT_ID):
            self.assertNotIn(secret, shown)

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

    # antigravity: agy runs only when its OAuth token file exists ---------------------------
    def test_signed_out_means_agy_is_never_launched_even_though_it_is_installed(self):
        r = self.run_panel(signed_in=False)        # agy is on PATH in the fake deps, the token file is absent
        self.assertEqual(r.lines["PANEL_antigravity"], "unavailable")
        self.assertEqual([c["cmd"][0] for c in self.runner.calls], ["grok"])
        self.assertNotIn("antigravity", [x["expert"] for x in self.ledger_lines()])
        self.assertEqual(r.lines["PANEL_MAJORITY"], "pass")

    def test_signed_in_means_agy_is_asked_like_any_other_expert(self):
        r = self.run_panel()
        self.assertEqual(r.lines["PANEL_antigravity"], "pass")
        self.assertEqual(sorted(c["cmd"][0] for c in self.runner.calls), ["agy", "grok"])   # concurrent: any order

    def test_dry_run_launches_nothing_whether_or_not_signed_in(self):
        r = self.run_panel("--dry-run", signed_in=False)
        self.assertEqual((self.runner.calls, r.lines["PANEL_antigravity"]), ([], "unavailable"))
        self.assertEqual(r.lines["PANEL_DRYRUN"], "groq,groq_qwen,gemini,cloudflare,openrouter,grok,local")
        r = self.run_panel("--dry-run")
        self.assertEqual((self.runner.calls, r.lines["PANEL_antigravity"]), ([], "would_call"))

    def test_config_can_still_switch_it_off_while_signed_in(self):
        r = self.run_panel(config={"experts": [{"name": "antigravity", "enabled": False}]})
        self.assertEqual(r.lines["PANEL_antigravity"], "unavailable")
        self.assertEqual([c["cmd"][0] for c in self.runner.calls], ["grok"])

    def test_the_probe_path_can_be_overridden_in_config(self):
        cfg = {"experts": [{"name": "antigravity", "enabled_if": ["which:agy", "exists:~/elsewhere/token"]}]}
        r = self.run_panel(config=cfg)             # default token file exists, the configured one does not
        self.assertEqual(r.lines["PANEL_antigravity"], "unavailable")
        r = self.run_panel(config=cfg, existing=("~/elsewhere/token",))
        self.assertEqual(r.lines["PANEL_antigravity"], "pass")

    # hard rules, end to end: a config file can not loosen them ---------------------
    def loosened(self, **overrides):
        """A user config that tries to open every expert to every class."""
        return {"experts": [{"name": n, "classes": ["public", "own", "client"], **overrides.get(n, {})}
                            for n in ALL_EXPERTS]}

    def test_a_config_that_lists_client_for_everyone_still_reaches_only_local(self):
        r = self.run_panel(cls="client", config=self.loosened())
        self.assertEqual(self.called(), ["local"])
        self.assertEqual(r.json["allowed"], ["local"])

    def test_a_config_that_lists_own_for_everyone_never_reaches_a_trainer(self):
        r = self.run_panel(cls="own", config=self.loosened())
        self.assertEqual(self.called(), ["groq", "groq_qwen", "cloudflare", "local"])
        self.assertEqual(r.json["allowed"], ["groq", "groq_qwen", "cloudflare", "local"])
        for n in TRAINING:
            self.assertNotIn(f"PANEL_{n}", r.lines)

    def test_a_non_loopback_ollama_never_sees_client_or_a_local_gate(self):
        far = {"name": "remote", "kind": "ollama", "model": "m", "endpoint": "http://10.1.2.3:11434",
               "classes": ["public", "own", "client"], "trains": False}
        for cls, extra in (("client", []), ("public", ["--never-send", "src/*"])):
            r = self.run_panel(*extra, cls=cls, config={"experts": [far]})
            self.assertNotIn("PANEL_remote", r.lines, cls)
        r = self.run_panel(cls="own", config={"experts": [far]})
        self.assertIn("PANEL_remote", r.lines)   # allowed where the diff may leave the machine anyway

    # usage log (panel.jsonl) -----------------------------------------------------
    LEDGER_KEYS = ["class", "expert", "gate", "input_tokens", "model", "ms", "output_tokens", "repo", "ts"]

    def test_one_exact_schema_line_per_called_expert(self):
        self.run_panel("--repo", "ScriptHammer", cls="own")
        lines = self.ledger_lines()   # experts run concurrently, so lines land in completion order
        self.assertEqual(sorted(x["expert"] for x in lines), ["cloudflare", "groq", "groq_qwen", "local"])
        want_model = {"groq": "openai/gpt-oss-120b", "groq_qwen": "qwen/qwen3.8-27b",
                      "cloudflare": "@cf/qwen/qwen2.5-coder-32b-instruct", "local": "qwen2.5-coder:7b"}
        for x in lines:
            self.assertEqual(sorted(x), self.LEDGER_KEYS)       # exact: no extras, no `estimated`
            self.assertRegex(x["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertEqual((x["repo"], x["class"], x["gate"]), ("ScriptHammer", "own", "send"))
            self.assertEqual(x["model"], want_model[x["expert"]])
            for k in ("input_tokens", "output_tokens", "ms"):
                self.assertIs(type(x[k]), int, k)

    def test_repo_defaults_to_null_and_the_gate_and_class_are_recorded(self):
        self.run_panel("--never-send", "src/*", cls="public")
        (line,) = self.ledger_lines()
        self.assertEqual((line["expert"], line["repo"], line["class"], line["gate"]), ("local", None, "public", "local"))

    def test_real_token_counts_per_kind_are_summed_over_chunks(self):
        r = self.run_panel("--repo", "r", cls="own", diff=self.many_files(8, 7500))
        lines = {x["expert"]: x for x in self.ledger_lines()}
        for name in ("groq", "groq_qwen", "cloudflare", "local"):
            chunks = r.json["experts"][name]["chunks"]
            self.assertGreater(chunks, 1, name)
            # usage.prompt_tokens/completion_tokens (openai_compat) and prompt_eval_count/eval_count (ollama)
            self.assertEqual((lines[name]["input_tokens"], lines[name]["output_tokens"]),
                             (80 * chunks, 20 * chunks), name)
            self.assertNotIn("estimated", lines[name])

    def test_gemini_counts_come_from_usage_metadata(self):
        self.net.usage = 530
        self.run_panel("--repo", "r")
        gem = next(x for x in self.ledger_lines() if x["expert"] == "gemini")
        self.assertEqual((gem["input_tokens"], gem["output_tokens"]), (510, 20))
        self.assertNotIn("estimated", gem)

    def test_cli_usage_is_estimated_chars_over_four_and_flagged(self):
        self.run_panel("--repo", "r")
        lines = {x["expert"]: x for x in self.ledger_lines()}
        reply = json.dumps(PASS)
        for call, name in zip(self.runner.calls, ("antigravity", "grok")):
            self.assertEqual(lines[name]["input_tokens"], len(cli_prompt(call)) // 4)
            self.assertEqual(lines[name]["output_tokens"], len(reply) // 4)
            self.assertIs(lines[name]["estimated"], True)
        self.assertEqual(sorted(lines["grok"]), sorted(self.LEDGER_KEYS + ["estimated"]))

    def test_the_cloudflare_line_carries_neurons_as_an_extra_optional_field(self):
        self.net.neurons["cloudflare"] = 3.60909090909
        self.run_panel("--repo", "ScriptHammer", cls="own")
        lines = {x["expert"]: x for x in self.ledger_lines()}
        cf = lines["cloudflare"]
        self.assertEqual(cf["neurons"], 3.6091)
        self.assertEqual(sorted(cf), sorted(self.LEDGER_KEYS + ["neurons"]))
        self.assertEqual((cf["input_tokens"], cf["output_tokens"]), (80, 20))
        for other in ("groq", "groq_qwen", "local"):
            self.assertNotIn("neurons", lines[other])

    def test_neurons_are_summed_over_chunks(self):
        self.net.neurons["cloudflare"] = 3.60909090909
        r = self.run_panel("--repo", "r", cls="own", diff=self.many_files(4, 20000))
        chunks = r.json["experts"]["cloudflare"]["chunks"]
        cf = next(x for x in self.ledger_lines() if x["expert"] == "cloudflare")
        self.assertGreater(chunks, 1)
        self.assertEqual(cf["neurons"], round(3.60909090909 * chunks, 4))

    def test_no_neurons_field_when_the_provider_reports_none(self):
        self.run_panel("--repo", "r", cls="own")
        self.assertTrue(all("neurons" not in x for x in self.ledger_lines()))

    def test_cloudflare_dict_content_and_neurons_work_together_end_to_end(self):
        self.net.content["cloudflare"] = "dict"
        self.net.neurons["cloudflare"] = 2.5
        self.net.answers["cloudflare"] = REVISE
        r = self.run_panel("--repo", "r", cls="own")
        self.assertEqual((r.lines["PANEL_cloudflare"], r.lines["PANEL_MAJORITY"]), ("revise", "pass"))
        self.assertEqual(next(x for x in self.ledger_lines() if x["expert"] == "cloudflare")["neurons"], 2.5)

    def test_a_provider_that_reports_no_usage_is_estimated_and_flagged(self):
        self.net.report_usage = False
        self.run_panel("--repo", "r", cls="own")
        prompt = self.net.posts[0]["payload"]["messages"][0]["content"]
        for x in self.ledger_lines():
            self.assertIs(x["estimated"], True, x["expert"])
            self.assertEqual(x["input_tokens"], len(prompt) // 4)

    def test_an_errored_expert_is_still_logged_with_zero_tokens(self):
        self.net.answers["groq"] = (500, {})
        r = self.run_panel("--repo", "r", cls="own")
        groq = next(x for x in self.ledger_lines() if x["expert"] == "groq")
        self.assertEqual((r.lines["PANEL_groq"], groq["input_tokens"], groq["output_tokens"]), ("error", 0, 0))

    def test_no_text_is_ever_logged(self):
        marker = {"verdict": "revise", "blocking": ["SECRETREASON-Zq7"]}
        for n in API_EXPERTS:
            self.net.answers[n] = marker
        self.runner.reply = marker
        self.run_panel("--repo", "ScriptHammer", spec="SPECMARKER-Zq7 " + SPEC)
        raw = Path(self.ledger).read_text()
        for text in ("SECRETREASON", "SPECMARKER", "prints hello", "owner", "print(", "DIFF", "SPEC", "verdict",
                     "blocking", *ORIGINALS, "[EMAIL_1]"):
            self.assertNotIn(text, raw)
        for line in self.ledger_lines():
            self.assertTrue(all(v is None or isinstance(v, (str, int, bool)) for v in line.values()))
            self.assertLess(max(len(str(v)) for v in line.values()), 60)

    def test_skip_and_unreadable_inputs_log_nothing(self):
        self.run_panel("--repo", "r", scanner=lambda d: "found")
        self.run_panel("--repo", "r", scanner=lambda d: "unavailable")
        self.assertEqual(self.ledger_lines(), [])
        self.assertFalse(os.path.exists(os.path.dirname(self.ledger)))

    def test_runs_append_and_an_explicit_ledger_path_wins(self):
        self.run_panel("--repo", "a", cls="client")
        self.run_panel("--repo", "b", cls="client")
        self.assertEqual([x["repo"] for x in self.ledger_lines()], ["a", "b"])
        other = os.path.join(self.tmp, "elsewhere", "p.jsonl")
        self.run_panel("--repo", "c", cls="client", ledger_path=other)
        self.assertEqual(len(self.ledger_lines()), 2)
        self.assertEqual(json.loads(Path(other).read_text())["repo"], "c")

    def test_an_unwritable_ledger_never_breaks_the_review(self):
        blocker = os.path.join(self.tmp, "afile")
        Path(blocker).write_text("x")
        with mock.patch.object(ledger_log, "DEFAULT_PATH", os.path.join(blocker, "sub", "panel.jsonl")):
            r = self.run_panel("--repo", "r", cls="own")
        self.assertEqual((r.lines["PANEL_MAJORITY"], r.rc), ("pass", 0))
        self.assertNotIn("internal error", r.stdout)


class HardRuleTests(unittest.TestCase):
    """may_serve() and route() are the code-level class rules; nothing in a config entry can loosen them."""
    ALL = ["public", "own", "client"]
    LOCAL = "http://127.0.0.1:11434"

    def spec(self, kind, **kw):
        base = {"name": "x", "kind": kind, "classes": list(self.ALL), "trains": False,
                "endpoint": self.LOCAL if kind == "ollama" else "https://api.example.com/v1"}
        return {**base, **kw}

    def serves(self, spec):
        return [c for c in self.ALL if pr.may_serve(spec, c)]

    def test_a_kind_other_than_ollama_can_never_serve_client(self):
        for kind in ("openai_compat", "gemini", "cli"):
            self.assertEqual(self.serves(self.spec(kind)), ["public", "own"], kind)

    def test_a_loopback_ollama_serves_every_class_it_lists(self):
        self.assertEqual(self.serves(self.spec("ollama")), self.ALL)
        for url in ("http://localhost:11434", "http://[::1]:11434", "http://127.0.0.1:9"):
            self.assertEqual(self.serves(self.spec("ollama", endpoint=url)), self.ALL, url)
        self.assertEqual(self.serves(self.spec("ollama", classes=["own"])), ["own"])

    def test_an_ollama_that_is_not_on_this_machine_is_not_local(self):
        for url in ("http://10.0.0.5:11434", "https://ollama.example.com", "http://127.0.0.1.evil.com:11434",
                    "http://localhost.evil.com", "", None, "not a url", "http://[bad"):
            s = self.spec("ollama", endpoint=url)
            self.assertFalse(pr.is_local(s), url)
            self.assertEqual(self.serves(s), ["public", "own"], url)

    def test_trains_true_serves_public_only_whatever_the_kind(self):
        for kind in ("openai_compat", "gemini", "cli", "ollama"):
            self.assertEqual(self.serves(self.spec(kind, trains=True)), ["public"], kind)

    def test_an_expert_that_does_not_say_it_is_safe_is_treated_as_training(self):
        for kind in ("openai_compat", "gemini", "cli"):
            s = self.spec(kind)
            del s["trains"]
            self.assertEqual(self.serves(s), ["public"], kind)
            self.assertEqual(self.serves({**s, "trains": None}), ["public"], kind)
            self.assertEqual(self.serves({**s, "trains": "no"}), ["public"], kind)   # only literal False counts
        local = self.spec("ollama")
        del local["trains"]
        self.assertEqual(self.serves(local), self.ALL)   # on this machine nothing is trained on

    def test_unknown_classes_and_malformed_entries_serve_nothing_extra(self):
        self.assertEqual(self.serves(self.spec("openai_compat", classes=["everyone", "admin"])), [])
        self.assertEqual(self.serves(self.spec("openai_compat", classes=None)), [])
        self.assertEqual(self.serves({"kind": "cli"}), [])

    def test_the_default_experts_serve_exactly_what_the_brief_says(self):
        want = {"groq": ["own", "public"], "groq_qwen": ["own", "public"], "gemini": ["public"],
                "cloudflare": ["own", "public"], "antigravity": ["public"], "openrouter": ["public"],
                "grok": ["public"], "local": ["public", "own", "client"]}
        for e in pr.DEFAULT_CONFIG["experts"]:
            self.assertEqual(sorted(self.serves(e)), sorted(want[e["name"]]), e["name"])
        for cls in self.ALL:
            self.assertEqual(pr.route(pr.DEFAULT_CONFIG, cls, "send"), [n for n in ALL_EXPERTS if cls in want[n]])

    def test_a_local_gate_narrows_to_on_machine_experts_only(self):
        far = self.spec("ollama", name="remote", endpoint="http://10.0.0.5:11434")
        cfg = {"experts": pr.DEFAULT_CONFIG["experts"] + [far]}
        self.assertEqual(pr.route(cfg, "public", "send"), ALL_EXPERTS + ["remote"])
        self.assertEqual(pr.route(cfg, "public", "local"), ["local"])
        self.assertEqual(pr.route(cfg, "own", "local"), ["local"])
        self.assertEqual(pr.route(cfg, "client", "local"), ["local"])


class LedgerLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.path = os.path.join(self.tmp, "new", "ledger", "panel.jsonl")

    def test_record_is_the_exact_schema(self):
        rec = ledger_log.record("ScriptHammer", "groq", "m", 10, 5, 1234, "own", "send", ts="2026-10-01T00:00:00Z")
        self.assertEqual(rec, {"ts": "2026-10-01T00:00:00Z", "repo": "ScriptHammer", "expert": "groq", "model": "m",
                               "input_tokens": 10, "output_tokens": 5, "ms": 1234, "class": "own", "gate": "send"})
        self.assertEqual(list(rec), list(ledger_log.FIELDS))
        self.assertEqual(ledger_log.record(None, "e", "m", 0, 0, 0, "own", "send")["repo"], None)
        self.assertEqual(ledger_log.record("", "e", "m", 0, 0, 0, "own", "send")["repo"], None)
        est = ledger_log.record("r", "e", "m", 1, 1, 1, "own", "send", estimated=True)
        self.assertIs(est["estimated"], True)
        self.assertNotIn("estimated", ledger_log.record("r", "e", "m", 1, 1, 1, "own", "send"))
        self.assertRegex(rec["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertRegex(ledger_log.record("r", "e", "m", 1, 1, 1, "own", "send")["ts"], r"Z$")

    def test_neurons_is_an_optional_non_negative_number(self):
        base = ledger_log.record("r", "e", "m", 1, 1, 1, "own", "send")
        self.assertNotIn("neurons", base)
        self.assertEqual(ledger_log.record("r", "e", "m", 1, 1, 1, "own", "send", neurons=3.60909090909)["neurons"], 3.6091)
        self.assertEqual(ledger_log.record("r", "e", "m", 1, 1, 1, "own", "send", neurons=0)["neurons"], 0.0)
        for junk in (None, -1, True, "3", float("nan") if False else None):
            self.assertNotIn("neurons", ledger_log.record("r", "e", "m", 1, 1, 1, "own", "send", neurons=junk))
        self.assertEqual(list(ledger_log.record("r", "e", "m", 1, 1, 1, "own", "send", neurons=1.5)),
                         list(ledger_log.FIELDS) + ["neurons"])

    def test_counts_are_always_non_negative_ints(self):
        rec = ledger_log.record("r", "e", "m", -5, True, "7", "own", "send")
        self.assertEqual((rec["input_tokens"], rec["output_tokens"], rec["ms"]), (0, 0, 0))

    def test_append_creates_a_private_dir_and_file_and_appends_json_lines(self):
        self.assertTrue(ledger_log.append(ledger_log.record("a", "e", "m", 1, 2, 3, "own", "send"), self.path))
        self.assertTrue(ledger_log.append(ledger_log.record("b", "e", "m", 1, 2, 3, "own", "send"), self.path))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        rows = [json.loads(x) for x in Path(self.path).read_text().splitlines()]
        self.assertEqual([r["repo"] for r in rows], ["a", "b"])

    def test_append_never_raises(self):
        blocker = os.path.join(self.tmp, "afile")
        Path(blocker).write_text("x")
        self.assertFalse(ledger_log.append({"a": 1}, os.path.join(blocker, "x", "p.jsonl")))

    def test_the_default_path_is_the_ledger_directory(self):
        self.assertEqual(ledger_log.DEFAULT_PATH, "~/.local/share/ledger/panel.jsonl")


class UsageExtractionTests(unittest.TestCase):
    def test_each_kind_reads_its_own_provider_fields(self):
        u = pr.usage_openai({"usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})
        self.assertEqual((u.input, u.output, u.neurons), (7, 3, None))
        g = pr.usage_gemini({"usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4}})
        self.assertEqual((g.input, g.output, g.neurons), (9, 4, None))
        o = pr.usage_ollama({"prompt_eval_count": 11, "eval_count": 6})
        self.assertEqual((o.input, o.output, o.neurons), (11, 6, None))

    def test_neurons_come_from_the_openai_usage_block_and_may_be_fractional(self):
        u = pr.usage_openai({"usage": {"prompt_tokens": 45, "completion_tokens": 10, "total_tokens": 55,
                                       "prompt_tokens_details": {"cached_tokens": 0}, "neurons": 3.60909090909}})
        self.assertEqual((u.input, u.output, u.neurons), (45, 10, 3.60909090909))
        self.assertEqual(pr.usage_openai({"usage": {"prompt_tokens": 1, "completion_tokens": 1, "neurons": 4}}).neurons, 4)
        for junk in ("3.6", -1, True, None):
            self.assertIsNone(pr.usage_openai({"usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                                          "neurons": junk}}).neurons)

    def test_a_missing_counter_is_zero_and_missing_everything_is_none(self):
        g = pr.usage_gemini({"usageMetadata": {"promptTokenCount": 9}})   # Gemini omits zeros
        self.assertEqual((g.input, g.output), (9, 0))
        for f, empty in ((pr.usage_openai, {}), (pr.usage_openai, {"usage": None}), (pr.usage_gemini, {}),
                         (pr.usage_ollama, {}), (pr.usage_openai, None), (pr.usage_openai, []),
                         (pr.usage_openai, {"usage": {"neurons": 5}})):   # neurons alone is not a token count
            self.assertIsNone(f(empty))

    def test_junk_counters_are_not_trusted(self):
        self.assertIsNone(pr.usage_openai({"usage": {"prompt_tokens": "7", "completion_tokens": -1}}))
        self.assertIsNone(pr.usage_ollama({"prompt_eval_count": True, "eval_count": 2.5}))


class ContentShapeTests(Base):
    """openai_compat `message.content` is a string for most providers, but Cloudflare Workers AI sends an
    already-parsed JSON object, and some providers send a list of parts."""

    def test_content_text_accepts_string_dict_and_list_of_parts(self):
        raw = '{"verdict":"revise","blocking":["x"]}'
        self.assertEqual(pr.content_text(raw), raw)
        self.assertEqual(json.loads(pr.content_text({"verdict": "revise", "blocking": ["x"]})),
                         {"verdict": "revise", "blocking": ["x"]})
        parts = [{"type": "text", "text": raw[:9]}, {"type": "text", "text": raw[9:]}]
        self.assertEqual(pr.content_text(parts), raw)
        self.assertEqual(pr.content_text([raw[:9], {"text": raw[9:]}, {"type": "image"}, 7, None]), raw)
        for nothing in (None, 7, 3.5, True, []):
            self.assertEqual(pr.content_text(nothing), "")

    def test_each_shape_gives_a_verdict_end_to_end(self):
        for style in ("str", "dict", "list"):
            self.net.content["cloudflare"] = style
            self.net.answers["cloudflare"] = REVISE
            r = self.review("cloudflare")
            self.assertEqual((r["verdict"], r["blocking"]), ("revise", ["off by one"]), style)
            self.assertNotIn("note", r, style)

    def test_every_openai_compat_expert_takes_all_three_shapes(self):
        for name in ("groq", "groq_qwen", "openrouter", "cloudflare"):
            for style in ("str", "dict", "list"):
                self.net.content[name] = style
                self.assertEqual(self.review(name)["verdict"], "pass", (name, style))

    def test_a_dict_content_still_goes_through_the_strict_verdict_check(self):
        self.net.content["cloudflare"] = "dict"
        self.net.answers["cloudflare"] = lambda payload, n: {"verdict": "maybe", "blocking": []}
        r = self.review("cloudflare")
        self.assertEqual((r["verdict"], r["note"]), ("error", "unparseable reply"))
        self.net.answers["cloudflare"] = lambda payload, n: {"result": {"verdict": "pass", "blocking": []}}
        self.assertEqual(self.review("cloudflare")["verdict"], "pass")   # a wrapped verdict is found, as for text

    def test_empty_or_null_content_is_an_unparseable_reply_not_a_crash(self):
        for content in (None, "", [], 7):
            self.net.answers["cloudflare"] = lambda payload, n, c=content: (
                200, {"choices": [{"message": {"content": c}}], "usage": {"prompt_tokens": 5, "completion_tokens": 0}})
            r = self.review("cloudflare")
            self.assertEqual((r["verdict"], r["note"]), ("error", "unparseable reply"), content)


class NeuronsTests(Base):
    def test_review_totals_neurons_over_chunks_and_omits_them_when_unreported(self):
        self.net.neurons["cloudflare"] = 3.60909090909
        r = self.review("cloudflare", self.many_files(4, 20000))
        self.assertGreater(r["chunks"], 1)
        self.assertEqual(r["neurons"], round(3.60909090909 * r["chunks"], 4))
        self.assertNotIn("neurons", self.review("groq"))
        self.assertNotIn("neurons", self.review("local"))


class ConcurrencyTests(Base):
    """The panel asks its experts on threads. Barriers and events, not sleeps, make these deterministic:
    a one-at-a-time panel would deadlock on them and the 5 s guard would turn that into an error verdict."""

    run_panel = FlowTests.run_panel
    called = FlowTests.called

    def test_experts_are_in_flight_at_the_same_time(self):
        barrier = threading.Barrier(4)   # groq, groq_qwen, gemini, cloudflare must all be waiting at once

        def meet(payload, n):
            barrier.wait(timeout=5)
            return PASS
        for name in ("groq", "groq_qwen", "gemini", "cloudflare"):
            self.net.answers[name] = meet
        r = self.run_panel()
        for name in ("groq", "groq_qwen", "gemini", "cloudflare"):
            self.assertEqual(r.lines["PANEL_" + name], "pass", name)

    def test_a_slow_expert_does_not_hold_up_the_others(self):
        others_done = threading.Event()
        answered = []

        def slow(payload, n):   # first in panel order, and it refuses to answer until two others have
            self.assertTrue(others_done.wait(timeout=5), "the panel serialised its experts")
            return PASS

        def quick(payload, n):
            answered.append(1)
            if len(answered) >= 2:
                others_done.set()
            return PASS
        self.net.answers.update({"groq": slow, "groq_qwen": quick, "cloudflare": quick})
        r = self.run_panel(cls="own")
        self.assertEqual([r.lines["PANEL_" + n] for n in ("groq", "groq_qwen", "cloudflare", "local")], ["pass"] * 4)
        self.assertEqual(r.lines["PANEL_MAJORITY"], "pass")

    def test_the_summary_keeps_panel_order_whatever_order_they_finish_in(self):
        r = self.run_panel()
        self.assertEqual(list(r.json["experts"]), ["groq", "groq_qwen", "gemini", "cloudflare", "antigravity",
                                                   "openrouter", "grok", "local"])

    def test_every_sentinel_is_printed_once_and_whole(self):
        r = self.run_panel()
        for name in ("groq", "groq_qwen", "gemini", "cloudflare", "antigravity", "openrouter", "grok", "local"):
            self.assertEqual(r.stdout.count(f"__PANEL_{name}="), 1, name)

    def test_a_worker_that_blows_up_costs_only_its_own_verdict(self):
        real = pr.review_with

        def flaky(ex, *a, **kw):
            if ex.name == "groq":
                raise RuntimeError("bug in one worker")
            return real(ex, *a, **kw)
        with mock.patch.object(pr, "review_with", flaky):
            r = self.run_panel(cls="own")
        self.assertEqual(r.lines["PANEL_groq"], "error")
        self.assertEqual(r.json["experts"]["groq"]["note"], "RuntimeError")
        for name in ("groq_qwen", "cloudflare", "local"):
            self.assertEqual(r.lines["PANEL_" + name], "pass", name)
        self.assertEqual(r.rc, 0)

    def test_wall_time_is_the_slowest_expert_not_the_sum(self):
        # real threads, real (tiny) sleeps: 4 experts x 0.4 s would take 1.6 s one at a time
        import time as real_time
        for name in ("groq", "groq_qwen", "gemini", "cloudflare"):
            self.net.answers[name] = lambda payload, n: (real_time.sleep(0.4), PASS)[1]
        t0 = real_time.monotonic()
        self.run_panel()
        self.assertLess(real_time.monotonic() - t0, 1.2)

    def test_ledger_lines_from_concurrent_experts_never_interleave(self):
        self.run_panel("--repo", "R")
        raw = Path(self.ledger).read_text().splitlines()
        self.assertEqual(len(raw), 8)
        for line in raw:
            self.assertEqual(json.loads(line)["repo"], "R")   # every line is a whole JSON object

    def test_ledger_log_append_is_safe_from_many_threads(self):
        path = os.path.join(self.tmp, "many", "panel.jsonl")
        big = "x" * 300

        def hammer(i):
            for j in range(150):
                ledger_log.append(ledger_log.record(big, f"e{i}", "m", j, j, j, "public", "send"), path)
        ts = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        lines = Path(path).read_text().splitlines()
        self.assertEqual(len(lines), 8 * 150)
        self.assertEqual({json.loads(x)["expert"] for x in lines}, {f"e{i}" for i in range(8)})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class PerModelPacerTests(Base):
    def test_each_groq_model_has_its_own_pacer_at_7k(self):
        deps = self.deps()
        a, b = self.ex("groq")._pacer(self.cfg, deps), self.ex("groq_qwen")._pacer(self.cfg, deps)
        self.assertIsNot(a, b)
        self.assertEqual((a.limit, b.limit), (7000, 7000))
        self.assertIs(a, self.ex("groq")._pacer(self.cfg, deps))   # but one model keeps one pacer

    def test_one_models_spend_does_not_make_the_other_wait(self):
        diff = self.many_files(1, 15000)      # about 4K prompt tokens + 1500 reserved = ~5.5K per call
        self.net.usage = 6900
        self.review("groq", diff)
        self.review("groq_qwen", diff)
        self.assertEqual(self.clock.sleeps, [], "a different model has a different minute")
        self.review("groq", diff)             # but the same model does wait for its own
        self.assertTrue(self.clock.sleeps)

    def test_a_call_is_budgeted_as_prompt_plus_max_completion_tokens(self):
        self.net.usage = 10   # the provider reports almost nothing; the reservation must still be 1500 + prompt
        deps = self.deps()
        self.review("groq")
        prompt = self.net.posts[0]["payload"]["messages"][0]["content"]
        (ts, spent), = self.ex("groq")._pacer(self.cfg, deps).events
        self.assertEqual(spent, pr.est_tokens(prompt) + 1500)

    def test_concurrent_acquires_never_overspend_a_minute(self):
        clock = FakeClock()
        real_time = __import__("time")

        def slow_now():   # widen any check-then-act window so an unlocked pacer would overspend
            real_time.sleep(0.0005)
            return clock.now()
        p = pr.Pacer(7000, slow_now, clock.sleep)
        spent, lock = [], threading.Lock()

        def worker():
            for _ in range(4):
                ev = p.acquire(3000)
                with lock:
                    spent.append((ev[0], ev[1]))
        ts = [threading.Thread(target=worker) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(spent), 24)
        self.assertLessEqual(max_window(spent), 7000)


def rate_limited(headers=None, message=None, details=None):
    """A 429 as post_json delivers it: (429, body) with the rate-limit headers under "_headers"."""
    body = {}
    if headers:
        body["_headers"] = headers
    if message is not None or details:
        body["error"] = {"message": message or "", **({"details": details} if details else {})}
    return 429, body


class WaitParsingTests(unittest.TestCase):
    def test_parse_wait_reads_seconds_and_unit_suffixes(self):
        for raw, want in (("3", 3.0), ("1.687s", 1.687), ("2m59.56s", 179.56), ("250ms", 0.25), (" 21s ", 21.0)):
            self.assertAlmostEqual(pr.parse_wait(raw), want, msg=raw)
        for raw in ("", None, "soon", "Wed, 21 Oct 2026 07:28:00 GMT", "1.5x"):
            self.assertIsNone(pr.parse_wait(raw), raw)

    def test_retry_delay_prefers_retry_after_then_reset_tokens_then_body_then_default(self):
        both = {"_headers": {"retry-after": "4", "x-ratelimit-reset-tokens": "9s"}}
        m = pr.RETRY_MARGIN   # a hint is followed by a small margin: providers' "retry in" runs a hair short
        self.assertEqual(pr.retry_delay(both, 20), 4.0 + m)
        self.assertEqual(pr.retry_delay({"_headers": {"x-ratelimit-reset-tokens": "1.687s"}}, 20), 1.687 + m)
        self.assertEqual(pr.retry_delay({"error": {"details": [{"@type": "x"}, {"retryDelay": "21s"}]}}, 20), 21.0 + m)
        self.assertEqual(pr.retry_delay({}, 20), 20.0)
        self.assertEqual(pr.retry_delay({"_headers": {"retry-after": "junk"}}, 7), 7.0)

    def test_retry_delay_is_capped_at_30_seconds(self):
        self.assertEqual(pr.retry_delay({"_headers": {"retry-after": "600"}}, 20), 30.0)
        self.assertEqual(pr.retry_delay({"_headers": {"retry-after": "29.5"}}, 20), 30.0)   # margin counts toward the cap
        self.assertEqual(pr.retry_delay({}, 90), 30.0)

    def test_scrub_masks_keys_and_urls_squeezes_space_and_cuts_to_200(self):
        out = pr.scrub("Limit for gsk_abcdefghijkl1234 and MYKEY\n  see https://console.groq.com/docs/rate-limits", ("MYKEY",))
        self.assertEqual(out, "Limit for <key> and <key> see <url>")
        self.assertEqual(len(pr.scrub("y" * 900)), 200)

    def test_error_body_keeps_the_json_and_only_the_rate_limit_headers(self):
        import email.message
        import urllib.error
        hdrs = email.message.Message()
        hdrs["Retry-After"] = "3"
        hdrs["X-Ratelimit-Reset-Tokens"] = "1.687s"
        hdrs["Set-Cookie"] = "secret=1"
        err = urllib.error.HTTPError("u", 429, "Too Many", hdrs, io.BytesIO(b'{"error":{"message":"slow down"}}'))
        body = pr.error_body(err)
        self.assertEqual(body["error"]["message"], "slow down")
        self.assertEqual(body["_headers"], {"retry-after": "3", "x-ratelimit-reset-tokens": "1.687s"})
        junk = urllib.error.HTTPError("u", 500, "x", email.message.Message(), io.BytesIO(b"<html>nope</html>"))
        self.assertEqual(pr.error_body(junk), {})

    def test_real_post_json_hands_back_status_and_error_body_for_an_http_error(self):
        import email.message
        import urllib.error
        hdrs = email.message.Message()
        hdrs["retry-after"] = "5"
        err = urllib.error.HTTPError("u", 429, "x", hdrs, io.BytesIO(b'{"error":{"message":"m"}}'))
        with mock.patch.object(pr.urllib.request, "urlopen", side_effect=err):
            status, obj = pr.real_post_json("https://x", {}, {}, 5)
        self.assertEqual((status, obj["error"]["message"], obj["_headers"]), (429, "m", {"retry-after": "5"}))


class RateLimitRetryTests(Base):
    def test_waits_what_retry_after_says_plus_the_margin_then_retries_once(self):
        self.net.answers["groq_qwen"] = lambda p, n: rate_limited({"retry-after": "3"}) if n == 1 else PASS
        r = self.review("groq_qwen")
        self.assertEqual((r["verdict"], self.net.calls["groq_qwen"]), ("pass", 2))
        self.assertEqual(self.clock.sleeps, [3.0 + pr.RETRY_MARGIN])

    def test_falls_back_to_x_ratelimit_reset_tokens(self):
        self.net.answers["groq"] = lambda p, n: rate_limited({"x-ratelimit-reset-tokens": "1.687s"}) if n == 1 else PASS
        self.assertEqual(self.review("groq")["verdict"], "pass")
        self.assertEqual(self.clock.sleeps, [1.687 + pr.RETRY_MARGIN])

    def test_a_long_wait_is_capped_at_30_seconds(self):
        self.net.answers["groq"] = lambda p, n: rate_limited({"retry-after": "120"}) if n == 1 else PASS
        self.review("groq")
        self.assertEqual(self.clock.sleeps, [30.0])

    def test_no_hint_uses_the_experts_retry_wait(self):
        self.net.answers["groq"] = lambda p, n: (429, {}) if n == 1 else PASS
        self.review("groq")
        self.assertEqual(self.clock.sleeps, [20])

    def test_gemini_429_is_retried_after_its_body_retry_delay(self):
        self.net.answers["gemini"] = lambda p, n: rate_limited(details=[{"retryDelay": "21s"}]) if n == 1 else PASS
        self.assertEqual(self.review("gemini")["verdict"], "pass")
        self.assertEqual(self.clock.sleeps, [21.0 + pr.RETRY_MARGIN])

    def test_a_retry_that_worked_is_still_mentioned_so_a_slow_expert_can_be_explained(self):
        self.net.answers["gemini"] = lambda p, n: rate_limited({"retry-after": "9"}) if n == 1 else PASS
        r = self.review("gemini")
        self.assertEqual((r["verdict"], r["note"]), ("pass", "retried after 429"))

    def test_retries_429_sets_how_many_times_a_429_is_retried(self):
        self.net.answers["gemini"] = lambda p, n: rate_limited({"retry-after": "2"}) if n <= 2 else PASS
        self.spec("gemini")["retries_429"] = 2
        r = self.review("gemini")
        self.assertEqual((r["verdict"], self.net.calls["gemini"], r["note"]), ("pass", 3, "retried after 429 x2"))
        self.assertEqual(self.clock.sleeps, [2.0 + pr.RETRY_MARGIN] * 2)
        self.net.answers["gemini"] = rate_limited({"retry-after": "1"}, "quota")
        r = self.review("gemini")   # 2 retries, then it gives up: 3 calls
        self.assertEqual((r["verdict"], r["note"], self.net.calls["gemini"]), ("error", "HTTP_429: quota", 3 + 3))

    def test_a_quota_failure_names_which_limit_was_hit(self):
        body = {"error": {"message": "You exceeded your current quota. " + "pad " * 80, "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20"}]}]}}
        self.net.answers["gemini"] = (429, body)
        note = self.review("gemini")["note"]
        self.assertTrue(note.startswith("HTTP_429 [GenerateRequestsPerDayPerProjectPerModel-FreeTier limit 20]: You exceeded"), note)
        self.assertEqual(pr.quota_summary({"error": {"details": [{"violations": [{"quotaId": "bad id!"}]}]}}), "")
        self.assertEqual(pr.quota_summary({}), "")

    def test_max_retry_wait_raises_or_lowers_the_cap_per_expert(self):
        self.net.answers["gemini"] = lambda p, n: rate_limited({"retry-after": "53.6"}) if n == 1 else PASS
        self.review("gemini")   # gemini's own cap is 60: it waits out the whole 53.6 s hint (+ margin)
        self.assertEqual(self.clock.sleeps, [53.6 + pr.RETRY_MARGIN])
        self.clock.sleeps.clear()
        self.net.answers["groq"] = lambda p, n: rate_limited({"retry-after": "53.6"}) if n == 1 else PASS
        self.review("groq")     # everyone else keeps the 30 s cap
        self.assertEqual(self.clock.sleeps, [30.0])
        self.clock.sleeps.clear()
        for junk in (0, -1, 500, "60", True, None):
            self.spec("groq")["max_retry_wait"] = junk
            self.assertEqual(self.ex("groq").max_retry_wait(), 30.0, junk)

    def test_junk_retries_429_means_the_default_of_one(self):
        for junk in (-1, 99, "3", True, None, 2.5):
            self.spec("groq")["retries_429"] = junk
            self.assertEqual(self.ex("groq").retries_429(), 1, junk)
        self.spec("groq")["retries_429"] = 0
        self.net.answers["groq"] = (429, {})
        self.assertEqual((self.review("groq")["note"], self.net.calls["groq"]), ("HTTP_429", 1))

    def test_the_second_429_puts_the_provider_message_in_the_note_cut_to_200(self):
        self.net.answers["groq_qwen"] = rate_limited({"retry-after": "1"}, "Rate limit reached for model qwen " + "z" * 400)
        r = self.review("groq_qwen")
        self.assertEqual(r["verdict"], "error")
        self.assertEqual(r["note"], "HTTP_429: " + ("Rate limit reached for model qwen " + "z" * 400)[:200])
        self.assertEqual(self.net.calls["groq_qwen"], 2)   # one retry, never more

    def test_the_note_never_carries_a_key_or_a_url(self):
        msg = f"bad {KEYS['groq']} at https://console.groq.com/x?k=1"
        self.net.answers["groq"] = rate_limited(message=msg)
        note = self.review("groq")["note"]
        self.assertEqual(note, "HTTP_429: bad <key> at <url>")

    def test_only_a_429_message_is_echoed(self):
        self.net.answers["groq"] = (500, {"error": {"message": "internal text that must not be shown"}})
        self.assertEqual(self.review("groq")["note"], "HTTP_500")
        self.assertEqual(self.net.calls["groq"], 1)


class JsonModeTests(Base):
    def rf(self, post):
        return post["payload"].get("response_format")

    def test_every_openai_compat_expert_asks_for_json_mode_and_the_others_do_not(self):
        for name in ("groq", "groq_qwen", "cloudflare", "openrouter"):
            self.review(name)
        for name in ("gemini", "local"):
            self.review(name)
        for post in self.net.posts:
            if post["who"] in ("gemini", "local"):
                self.assertNotIn("response_format", post["payload"], post["who"])
            else:
                self.assertEqual(self.rf(post), {"type": "json_object"}, post["who"])

    def test_json_mode_can_be_switched_off_per_expert(self):
        self.spec("groq")["json_mode"] = False
        self.review("groq")
        self.assertNotIn("response_format", self.net.posts[0]["payload"])

    def test_a_400_about_response_format_retries_once_without_it_and_remembers(self):
        self.net.answers["groq"] = lambda p, n: ((400, {"error": {"message": "response_format is not supported by this model"}})
                                                 if "response_format" in p else PASS)
        r = self.review("groq", self.many_files(8, 7500))   # several chunks
        self.assertEqual(r["verdict"], "pass")
        with_rf = [p for p in self.net.posts if self.rf(p)]
        self.assertEqual(len(with_rf), 1, "tried once, then never again for this expert in this run")
        self.assertEqual(self.net.calls["groq"], r["chunks"] + 1)
        self.assertNotIn("response_format", self.net.posts[-1]["payload"])
        self.assertEqual(r["note"], "provider rejected response_format; sent without")

    def test_the_note_names_the_providers_error_code_when_it_gives_one(self):
        self.net.answers["groq"] = lambda p, n: ((400, {"error": {"message": "Failed to validate JSON", "code": "json_validate_failed"}})
                                                 if "response_format" in p else PASS)
        self.assertEqual(self.review("groq")["note"], "provider rejected response_format (json_validate_failed); sent without")

    def test_groq_gets_low_reasoning_effort_so_it_cannot_think_its_whole_budget_away(self):
        self.review("groq")
        self.assertEqual(self.net.posts[0]["payload"]["reasoning_effort"], "low")
        self.review("groq_qwen")
        self.assertNotIn("reasoning_effort", self.net.posts[1]["payload"])

    def test_the_fallback_is_per_expert(self):
        self.net.answers["groq"] = lambda p, n: ((400, {"error": {"message": "invalid response_format"}})
                                                 if "response_format" in p else PASS)
        self.review("groq")
        self.review("groq_qwen")
        self.assertEqual(self.rf(next(p for p in self.net.posts if p["who"] == "groq_qwen")), {"type": "json_object"})

    def test_a_400_about_something_else_is_an_error_not_a_retry(self):
        self.net.answers["groq"] = (400, {"error": {"message": "model not found"}})
        r = self.review("groq")
        self.assertEqual((r["verdict"], r["note"], self.net.calls["groq"]), ("error", "HTTP_400", 1))

    def test_a_400_that_persists_without_json_mode_is_an_error_after_one_retry(self):
        self.net.answers["groq"] = (400, {"error": {"message": "response_format again"}})
        r = self.review("groq")
        self.assertEqual((r["verdict"], r["note"], self.net.calls["groq"]), ("error", "HTTP_400", 2))


class UnparseableRetryTests(Base):
    def test_one_follow_up_after_an_unreadable_reply_and_its_answer_is_used(self):
        self.net.answers["groq"] = lambda p, n: "I think it looks fine, no JSON here" if n == 1 else REVISE
        r = self.review("groq")
        self.assertEqual((r["verdict"], r["blocking"], self.net.calls["groq"]), ("revise", ["off by one"], 2))
        self.assertEqual(r["note"], "reply needed one JSON retry")

    def test_the_follow_up_is_the_prompt_the_bad_reply_and_the_short_instruction(self):
        self.net.answers["groq"] = lambda p, n: "not json at all" if n == 1 else PASS
        self.review("groq")
        first, second = (p["payload"]["messages"] for p in self.net.posts)
        self.assertEqual(len(first), 1)
        self.assertEqual([m["role"] for m in second], ["user", "assistant", "user"])
        self.assertEqual(second[0]["content"], first[0]["content"])
        self.assertEqual(second[1]["content"], "not json at all")
        self.assertEqual(second[2]["content"], "Your previous reply was not valid JSON. Reply with ONLY the JSON object.")

    def test_gives_up_after_exactly_one_retry(self):
        self.net.answers["groq"] = "still prose"
        r = self.review("groq")
        self.assertEqual((r["verdict"], r["note"], self.net.calls["groq"]), ("error", "unparseable reply", 2))

    def test_an_empty_reply_gets_the_instruction_on_the_prompt_not_an_empty_assistant_turn(self):
        self.net.answers["groq"] = lambda p, n: "" if n == 1 else PASS
        self.assertEqual(self.review("groq")["verdict"], "pass")
        second = self.net.posts[1]["payload"]["messages"]
        self.assertEqual([m["role"] for m in second], ["user"])
        self.assertTrue(second[0]["content"].endswith("Reply with ONLY the JSON object."))

    def test_a_readable_first_reply_is_never_retried(self):
        self.review("groq")
        self.assertEqual(self.net.calls["groq"], 1)

    def test_every_chunk_gets_its_own_single_retry(self):
        self.net.answers["groq"] = lambda p, n: "prose" if n % 2 else PASS
        r = self.review("groq", self.many_files(8, 7500))
        self.assertEqual((r["verdict"], self.net.calls["groq"]), ("pass", 2 * r["chunks"]))

    def test_tokens_are_counted_for_both_calls(self):
        self.net.answers["groq"] = lambda p, n: "prose" if n == 1 else PASS
        r = self.review("groq")
        self.assertEqual((r["input_tokens"], r["output_tokens"]), (2 * (self.net.usage - 20), 40))

    def test_gemini_and_local_use_their_own_roles_for_the_follow_up(self):
        for name in ("gemini", "local"):
            self.net.answers[name] = lambda p, n: "prose" if n == 1 else PASS
            self.assertEqual(self.review(name)["verdict"], "pass", name)
        gem1, gem2 = [p for p in self.net.posts if p["who"] == "gemini"]
        self.assertEqual(len(gem1["payload"]["contents"]), 1)
        self.assertEqual([c["role"] for c in gem2["payload"]["contents"]], ["user", "model", "user"])
        loc2 = [p for p in self.net.posts if p["who"] == "local"][1]
        self.assertEqual([m["role"] for m in loc2["payload"]["messages"]], ["user", "assistant", "user"])

    def test_a_cli_expert_is_re_asked_with_the_instruction_appended(self):
        replies = iter(["prose", json.dumps(PASS)])
        calls = self.runner.calls
        self.deps_run_cmd = lambda cmd, timeout: (calls.append({"cmd": cmd, "timeout": timeout}), (0, next(replies)))[1]
        deps = self.deps()
        deps.run_cmd = self.deps_run_cmd
        r = pr.review_with(self.ex("antigravity"), self.cfg, deps, SPEC, ACCEPT, DIFF)
        self.assertEqual((r["verdict"], len(self.runner.calls)), ("pass", 2))
        self.assertTrue(cli_prompt(self.runner.calls[1]).endswith(pr.RETRY_NOTE))
        self.assertNotIn(pr.RETRY_NOTE, cli_prompt(self.runner.calls[0]))

    def test_every_prompt_tells_the_expert_it_has_no_tools(self):
        # agy is a full agent: told nothing, it tried `run_command ls scripts/`, was denied, and printed nothing
        self.review("groq")
        sent = self.net.posts[0]["payload"]["messages"][0]["content"]
        self.assertIn("You have no tools and no shell here", sent)
        self.assertIn("do not run commands, read files or browse", sent)


class RawReplyCaptureTests(Base):
    """Unreadable replies are kept for diagnosis for the public class ONLY."""
    run_panel = FlowTests.run_panel

    def errors_dir(self):
        return Path(self.ledger).parent / "panel-errors"

    def files(self):
        d = self.errors_dir()
        return sorted(d.iterdir()) if d.exists() else []

    def test_public_keeps_the_raw_reply_up_to_2000_chars_with_private_modes(self):
        self.net.answers["groq"] = "here is a long essay " + "w" * 5000
        self.run_panel(cls="public")
        files = [f for f in self.files() if "-groq." in f.name or re.search(r"-groq-\d+\.txt$", f.name)]
        self.assertEqual(len(files), 2, "the first reply and the follow-up's reply are both kept")
        for f in files:
            self.assertRegex(f.name, r"^\d{8}T\d{6}Z-groq(-\d+)?\.txt$")
            self.assertEqual(f.read_text(), ("here is a long essay " + "w" * 5000)[:2000])
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.errors_dir().stat().st_mode), 0o700)

    def test_own_and_client_never_write_one(self):
        for cls in ("own", "client"):
            self.net.answers.update({n: "prose only" for n in API_EXPERTS})
            self.run_panel(cls=cls)
            self.assertFalse(self.errors_dir().exists(), cls)

    def test_a_public_run_that_the_gate_narrowed_to_local_writes_none(self):
        self.net.answers["local"] = "prose only"
        self.run_panel("--never-send", "*.py", cls="public")
        self.assertEqual(self.files(), [])

    def test_a_readable_reply_writes_nothing(self):
        self.run_panel(cls="public")
        self.assertEqual(self.files(), [])

    def test_the_summary_and_ledger_carry_no_reply_text(self):
        self.net.answers["groq"] = "SECRETREPLYTEXT " * 20
        r = self.run_panel(cls="public")
        self.assertNotIn("SECRETREPLYTEXT", r.stdout)
        self.assertNotIn("SECRETREPLYTEXT", Path(self.ledger).read_text())

    def test_the_errors_dir_can_be_pointed_elsewhere(self):
        self.net.answers["groq"] = "prose"
        target = os.path.join(self.tmp, "elsewhere")
        self.run_panel(cls="public", errors_dir=target)
        self.assertTrue(os.listdir(target))
        self.assertFalse(self.errors_dir().exists())

    def test_save_raw_reply_never_raises_and_never_overwrites(self):
        d = os.path.join(self.tmp, "e")
        a, b = pr.save_raw_reply(d, "x", "one"), pr.save_raw_reply(d, "x", "two")
        self.assertNotEqual(a, b)
        self.assertEqual((Path(a).read_text(), Path(b).read_text()), ("one", "two"))
        blocked = os.path.join(self.tmp, "afile")
        Path(blocked).write_text("x")
        self.assertIsNone(pr.save_raw_reply(os.path.join(blocked, "sub"), "x", "t"))


class TimeoutConfigTests(Base):
    run_panel = FlowTests.run_panel

    def test_gemini_gets_180_and_the_others_the_top_level_timeout(self):
        self.review("gemini")
        self.review("groq")
        t = {p["who"]: p["timeout"] for p in self.net.posts}
        self.assertEqual((t["gemini"], t["groq"]), (180, 120))

    def test_a_top_level_timeout_does_not_shorten_geminis_own(self):
        self.cfg["timeout"] = 30
        self.review("gemini")
        self.review("local")
        t = {p["who"]: p["timeout"] for p in self.net.posts}
        self.assertEqual((t["gemini"], t["local"]), (180, 30))

    def test_a_config_file_can_set_any_experts_timeout(self):
        cfg_path = os.path.join(self.tmp, "c.json")
        Path(cfg_path).write_text(json.dumps({"experts": [{"name": "gemini", "timeout": 45},
                                                          {"name": "antigravity", "timeout": 300}]}))
        cfg, status = pr.load_config(cfg_path)
        self.assertEqual(status, "file")
        experts = pr.make_experts(cfg)
        deps = self.deps()
        pr.review_with(experts["gemini"], cfg, deps, SPEC, ACCEPT, DIFF)
        pr.review_with(experts["antigravity"], cfg, deps, SPEC, ACCEPT, DIFF)
        self.assertEqual(self.net.posts[0]["timeout"], 45)
        self.assertEqual(self.runner.calls[0]["timeout"], 300)

    def test_a_junk_timeout_falls_back_to_the_top_level_one(self):
        for junk in (0, -5, "fast", True, None):
            self.spec("gemini")["timeout"] = junk
            self.assertEqual(self.ex("gemini").timeout(self.cfg), 120, junk)

    def test_a_cli_expert_without_its_own_timeout_uses_the_top_level_one(self):
        self.review("antigravity")
        self.assertEqual(self.runner.calls[0]["timeout"], 120)


class GeminiThinkingTests(Base):
    def gen(self, post):
        return post["payload"]["generationConfig"]

    def test_the_request_asks_for_low_thinking_by_default(self):
        self.review("gemini")
        self.assertEqual(self.gen(self.net.posts[0])["thinkingConfig"], {"thinkingLevel": "low"})
        self.assertEqual(self.gen(self.net.posts[0])["responseMimeType"], "application/json")

    def test_no_thinking_level_means_no_thinking_config(self):
        del self.spec("gemini")["thinking_level"]
        self.review("gemini")
        self.assertNotIn("thinkingConfig", self.gen(self.net.posts[0]))

    def test_a_model_that_rejects_it_gets_one_retry_without_and_it_is_remembered(self):
        self.net.answers["gemini"] = lambda p, n: (
            (400, {"error": {"message": "Invalid value at 'generation_config.thinking_config.thinking_level'"}})
            if "thinkingConfig" in p["generationConfig"] else PASS)
        r = self.review("gemini", self.many_files(3, 150000))
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(sum("thinkingConfig" in self.gen(p) for p in self.net.posts), 1)
        self.assertEqual(self.net.calls["gemini"], r["chunks"] + 1)
        self.assertEqual(r["note"], "provider rejected thinkingConfig; sent without")


class FakeResponse:
    """What urlopen returns, delivering `pieces` one read1() at a time; each read1 moves `clock` on."""

    def __init__(self, pieces, clock=None, tick=0.0, status=200):
        self.pieces, self.clock, self.tick, self.status = list(pieces), clock, tick, status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read1(self, n=-1):
        if self.clock is not None:
            self.clock[0] += self.tick
        return self.pieces.pop(0) if self.pieces else b""


class DeadlineTests(Base):
    def test_a_reply_that_arrives_in_pieces_is_assembled_and_parsed(self):
        resp = FakeResponse([b"   ", b'{"a":', b" 1}"])
        with mock.patch.object(pr.urllib.request, "urlopen", return_value=resp):
            self.assertEqual(pr.real_post_json("https://x", {}, {}, 30), (200, {"a": 1}))

    def test_a_slow_reply_kept_alive_with_trickled_bytes_still_hits_the_deadline(self):
        # OpenRouter sends a few bytes every ~3 s while the model thinks: urllib's socket timeout never fires
        clock = [1000.0]
        resp = FakeResponse([b"\n"] * 100 + [b'{"ok": true}'], clock, tick=3.0)
        with mock.patch.object(pr.urllib.request, "urlopen", return_value=resp), \
                mock.patch.object(pr.time, "monotonic", lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                pr.real_post_json("https://x", {}, {}, 120)
        self.assertLess(clock[0] - 1000.0, 130, "gave up near the deadline, not after the whole 100 pieces")

    def test_the_same_reply_inside_the_deadline_is_fine(self):
        clock = [1000.0]
        resp = FakeResponse([b"\n"] * 10 + [b'{"ok": true}'], clock, tick=3.0)
        with mock.patch.object(pr.urllib.request, "urlopen", return_value=resp), \
                mock.patch.object(pr.time, "monotonic", lambda: clock[0]):
            self.assertEqual(pr.real_post_json("https://x", {}, {}, 120), (200, {"ok": True}))

    def test_a_deadline_miss_is_an_error_verdict_not_a_hang_and_later_chunks_are_skipped(self):
        def late(url, headers, payload, timeout):
            raise TimeoutError("no complete reply within 120s")
        deps = self.deps()
        deps.post_json = late
        r = pr.review_with(self.ex("openrouter"), self.cfg, deps, SPEC, ACCEPT, self.many_files(3, 150000))
        self.assertEqual(r["verdict"], "error")
        self.assertEqual(r["note"], "TimeoutError; later chunks skipped")

    def test_openrouter_is_sent_reasoning_off_and_other_experts_are_not(self):
        self.review("openrouter")
        self.review("cloudflare")
        sent = {p["who"]: p["payload"] for p in self.net.posts}
        self.assertEqual(sent["openrouter"]["reasoning"], {"enabled": False})
        self.assertNotIn("reasoning", sent["cloudflare"])

    def test_extra_in_the_config_file_brings_the_thinking_back(self):
        p = os.path.join(self.tmp, "c.json")
        Path(p).write_text(json.dumps({"experts": [{"name": "openrouter", "extra": {}}]}))
        cfg, _ = pr.load_config(p)
        pr.review_with(pr.make_experts(cfg)["openrouter"], cfg, self.deps(), SPEC, ACCEPT, DIFF)
        self.assertNotIn("reasoning", self.net.posts[0]["payload"])


if __name__ == "__main__":
    unittest.main()
