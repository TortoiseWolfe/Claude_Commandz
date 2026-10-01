"""Tests for codex_review.py. No network, no real codex: the runner, `which`, the existence check and the
secret scanner are all injected; the ledger lives in a temp dir."""

import builtins
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import codex_review as cr  # noqa: E402

CLEAN = lambda text: "clean"  # noqa: E731
PLAN = "GOAL: add a README section\n1. write section\n   accept: grep -q Usage README.md\n"
FORBIDDEN = ("--dangerously-bypass-approvals-and-sandbox", "--full-auto", "--dangerously-bypass-hook-trust",
             "workspace-write", "danger-full-access")
PASS = '{"verdict":"pass","concerns":[],"missing":[]}'
REVISE = '{"verdict":"revise","concerns":["a","b"],"missing":["c"]}'


class Runner:
    """Stands in for codex: records the call, writes last.txt like `-o` does."""

    def __init__(self, reply=PASS, rc=0, output="", exc=None):
        self.reply, self.rc, self.output, self.exc = reply, rc, output, exc
        self.calls = []

    def __call__(self, argv, prompt, cwd, timeout):
        self.calls.append({"argv": argv, "prompt": prompt, "cwd": cwd, "timeout": timeout,
                           "cwd_entries": os.listdir(cwd)})
        if self.exc:
            raise self.exc
        if self.reply is not None:
            Path(argv[argv.index("-o") + 1]).write_text(self.reply)
        return self.rc, self.output


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.plan = os.path.join(self.tmp, "plan.md")
        Path(self.plan).write_text(PLAN)
        self.ledger = os.path.join(self.tmp, "ledger", "panel.jsonl")
        self.terms = os.path.join(self.tmp, "no-terms.txt")

    def run_main(self, runner=None, cls="public", plan_text=None, extra=(), which=True, auth=True,
                 scanner=CLEAN, classes_path=None, repo="t"):
        if plan_text is not None:
            Path(self.plan).write_text(plan_text)
        self.exists_calls = []

        def exists(p):
            self.exists_calls.append(p)
            return auth

        deps = cr.Deps(run=runner or Runner(), which=lambda n: "/usr/bin/codex" if which else None,
                       exists=exists, scanner=scanner, ledger_path=self.ledger,
                       classes_path=classes_path or os.path.join(self.tmp, "no-classes.json"))
        argv = ["--plan", self.plan, "--class", cls, "--repo", repo, "--terms-file", self.terms, *extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cr.main(argv, deps)
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def ledger_rows(self):
        if not os.path.exists(self.ledger):
            return []
        return [json.loads(line) for line in Path(self.ledger).read_text().splitlines()]

    def test_class_gating_runs_nothing(self):
        for cls in ("own", "client"):
            r = Runner()
            out = self.run_main(r, cls=cls)
            self.assertIn("__CODEX=skipped__", out)
            self.assertIn("__CODEX_REASON=class__", out)
            self.assertEqual(r.calls, [])

    def test_gate_skip(self):
        r = Runner()
        out = self.run_main(r, scanner=lambda t: "found")
        self.assertIn("__CODEX=skipped__", out)
        self.assertIn("__CODEX_REASON=gate-skip__", out)
        self.assertEqual(r.calls, [])

    def test_gate_local_via_never_send(self):
        r = Runner()
        out = self.run_main(r, plan_text="diff --git a/secrets/x b/secrets/x\n+++ b/secrets/x\n+hi\n",
                            extra=["--never-send", "secrets/*"])
        self.assertIn("__CODEX_REASON=gate-local__", out)
        self.assertEqual(r.calls, [])

    def test_unavailable_without_binary_or_auth(self):
        for kw in ({"which": False}, {"auth": False}):
            r = Runner()
            out = self.run_main(r, **kw)
            self.assertIn("__CODEX=unavailable__", out)
            self.assertEqual(r.calls, [])

    def test_auth_file_never_opened(self):
        opened = []
        real_open = builtins.open

        def spy(file, *a, **k):
            opened.append(str(file))
            return real_open(file, *a, **k)

        builtins.open = spy
        try:
            self.run_main(Runner())
        finally:
            builtins.open = real_open
        self.assertTrue(any(".codex" in p for p in self.exists_calls))
        self.assertFalse([p for p in opened if ".codex" in p])

    def test_pass(self):
        out = self.run_main(Runner(PASS))
        self.assertIn("__CODEX=pass__", out)
        self.assertIn("__CODEX_CONCERNS=0__", out)
        self.assertLess(out.index("__CODEX_CONCERNS"), out.index("CODEX REVIEW:"))
        self.assertEqual(json.loads(out.split("CODEX REVIEW:\n", 1)[1])["verdict"], "pass")

    def test_revise_counts_concerns(self):
        out = self.run_main(Runner(REVISE))
        self.assertIn("__CODEX=revise__", out)
        self.assertIn("__CODEX_CONCERNS=2__", out)
        self.assertEqual(json.loads(out.split("CODEX REVIEW:\n", 1)[1])["missing"], ["c"])

    def test_falls_back_to_stdout(self):
        out = self.run_main(Runner(reply=None, output="noise\n" + REVISE + "\ntokens used\n5"))
        self.assertIn("__CODEX=revise__", out)

    def test_garbage_is_error(self):
        out = self.run_main(Runner("I think it is fine", output="chatter"))
        self.assertIn("__CODEX=error__", out)
        self.assertNotIn("chatter", out)

    def test_bad_verdict_is_error(self):
        out = self.run_main(Runner('{"verdict":"maybe","concerns":[]}'))
        self.assertIn("__CODEX=error__", out)

    def test_nonzero_exit_is_error(self):
        out = self.run_main(Runner(PASS, rc=2, output="SECRET-FROM-CODEX"))
        self.assertIn("__CODEX=error__", out)
        self.assertIn("__CODEX_REASON=exit 2__", out)
        self.assertNotIn("SECRET-FROM-CODEX", out)

    def test_timeout_is_error(self):
        out = self.run_main(Runner(exc=subprocess.TimeoutExpired("codex", 1)))
        self.assertIn("__CODEX=error__", out)
        self.assertIn("__CODEX_REASON=timeout__", out)

    def test_exact_argv(self):
        r = Runner()
        self.run_main(r, extra=["--timeout", "77"])
        argv = r.calls[0]["argv"]
        self.assertEqual(argv[:3], ["codex", "exec", "--skip-git-repo-check"])
        for need in ("--ephemeral", "read-only", "model_reasoning_effort=high", "-o"):
            self.assertIn(need, argv)
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")
        self.assertEqual(argv[argv.index("-c") + 1], "model_reasoning_effort=high")
        self.assertEqual(argv[-1], "-")
        self.assertEqual(r.calls[0]["timeout"], 77)
        for bad in FORBIDDEN:
            self.assertFalse(any(bad in x for x in argv), bad)

    def test_effort_is_passed_through(self):
        r = Runner()
        self.run_main(r, extra=["--effort", "low"])
        self.assertIn("model_reasoning_effort=low", r.calls[0]["argv"])

    def test_cwd_is_empty_temp_dir_removed_after(self):
        r = Runner()
        self.run_main(r)
        cwd = r.calls[0]["cwd"]
        self.assertEqual(r.calls[0]["cwd_entries"], [])
        self.assertFalse(os.path.exists(cwd))
        self.assertFalse(os.path.exists(os.path.dirname(r.calls[0]["argv"][-2])))

    def test_only_redacted_text_reaches_runner(self):
        r = Runner()
        self.run_main(r, plan_text=PLAN + "ask planted.person@acme-planted.org about it\n")
        prompt = r.calls[0]["prompt"]
        self.assertNotIn("planted.person@acme-planted.org", prompt)
        self.assertIn("[EMAIL_1]", prompt)
        self.assertIn("add a README section", prompt)

    def test_usage_logged_with_parsed_tokens(self):
        self.run_main(Runner(PASS, output="model: gpt-5-codex\ntokens used\n12,345\n"))
        rows = self.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["expert"], "codex")
        self.assertEqual(rows[0]["model"], "gpt-5-codex")
        self.assertEqual(rows[0]["input_tokens"], 12345)
        self.assertNotIn("estimated", rows[0])
        self.assertEqual(rows[0]["class"], "public")

    def test_usage_estimated_without_count(self):
        self.run_main(Runner(PASS, output=""))
        row = self.ledger_rows()[0]
        self.assertTrue(row["estimated"])
        self.assertEqual(row["model"], "codex")
        self.assertGreater(row["input_tokens"], 0)

    def test_logging_failure_does_not_change_verdict(self):
        self.ledger = "/proc/nope/ledger.jsonl"
        out = self.run_main(Runner(PASS))
        self.assertIn("__CODEX=pass__", out)

    def test_skipped_runs_log_nothing(self):
        self.run_main(Runner(), cls="own")
        self.assertEqual(self.ledger_rows(), [])

    def test_unknown_effort_is_an_error_and_runs_nothing(self):
        for bad in ("high -c sandbox_mode=danger-full-access", "extreme", ""):
            r = Runner()
            out = self.run_main(r, extra=["--effort", bad])
            self.assertIn("__CODEX=error__", out, bad)
            self.assertIn("bad effort", out)
            self.assertEqual(r.calls, [])

    def test_every_documented_effort_is_accepted(self):
        for e in ("low", "medium", "high", "xhigh", "ultra", "minimal", "none"):
            r = Runner()
            self.run_main(r, extra=["--effort", e])
            self.assertIn(f"model_reasoning_effort={e}", r.calls[0]["argv"])

    def test_listed_class_makes_a_public_plan_local_only(self):
        cp = os.path.join(self.tmp, "classes.json")
        Path(cp).write_text(json.dumps({"repos": {"T": "client"}}))
        r = Runner()
        out = self.run_main(r, classes_path=cp, repo="t")
        self.assertIn("__CODEX=skipped__", out)
        self.assertIn("__CODEX_REASON=class__", out)
        self.assertEqual(r.calls, [])
        r = Runner()
        out = self.run_main(r, classes_path=cp, repo="other")   # unlisted = client, as classes.json says
        self.assertIn("__CODEX=skipped__", out)
        self.assertEqual(r.calls, [])

    def test_subprocess_gets_a_minimal_environment(self):
        from unittest import mock
        seen = {}

        class P:
            pid = 1
            returncode = 0

            def communicate(self, prompt=None, timeout=None):
                return "ok", None

        def popen(argv, **kw):
            seen.update(kw)
            return P()
        env = {"PATH": "/bin", "HOME": "/h", "LANG": "C", "CODEX_HOME": "/c", "OPENAI_API_KEY": "k",
               "GH_TOKEN": "t", "AWS_SECRET_ACCESS_KEY": "s"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(cr.subprocess, "Popen", popen):
            cr.real_run(["codex"], "p", self.tmp, 5)
        self.assertEqual(seen["env"], {"PATH": "/bin", "HOME": "/h", "LANG": "C", "CODEX_HOME": "/c",
                                       "TERM": "dumb"})


if __name__ == "__main__":
    unittest.main()
