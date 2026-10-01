"""Tests for jev_precheck.py's usage logging. No network: `urlopen` is replaced, and the key file and the
ledger both live in a temp dir."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jev_precheck as jp  # noqa: E402
import ledger_log  # noqa: E402

DIFF = '+++ b/app.py\n+owner = "jane.doe@planted-mail.org"  # SECRET-DIFF-MARKER\n+print("hello")\n'
ACCEPT = "- prints hello\n- ACCEPT-MARKER"
KEY = "ts_SECRETTYPESAFEKEY"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class JevTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.key = os.path.join(self.tmp, "api-key")
        Path(self.key).write_text(KEY)
        self.ledger = os.path.join(self.tmp, "ledger", "panel.jsonl")
        self.scanned, self.scan_result = [], "clean"
        self.requests, self.reply = [], {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.97}},
                                         "usage": {"input_tokens": 290, "output_tokens": 20}}
        for p in (mock.patch.object(jp, "KEY_PATH", self.key), mock.patch.object(jp, "urlopen", self.urlopen),
                  mock.patch.object(ledger_log, "DEFAULT_PATH", self.ledger),
                  mock.patch.object(jp, "SCANNER", lambda text: self.scan(text)),
                  mock.patch.object(jp, "CLASSES_PATH", os.path.join(self.tmp, "classes.json"))):
            p.start()
            self.addCleanup(p.stop)
        self.terms = os.path.join(self.tmp, "terms.txt")
        Path(self.terms).write_text("Acme Corp\n")
        Path(os.path.join(self.tmp, "acc.txt")).write_text(ACCEPT)
        Path(os.path.join(self.tmp, "diff.txt")).write_text(DIFF)
        Path(os.path.join(self.tmp, "q.json")).write_text(json.dumps(["Does it print hello?", "Is owner set?"]))

    def scan(self, text):
        self.scanned.append(text)
        return self.scan_result

    def urlopen(self, req, timeout=None):
        self.requests.append((req, timeout))
        if isinstance(self.reply, Exception):
            raise self.reply
        return FakeResponse(json.dumps(self.reply).encode())

    def run_jev(self, *extra):
        argv = ["--acceptance", os.path.join(self.tmp, "acc.txt"), "--diff", os.path.join(self.tmp, "diff.txt"), *extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = jp.main(argv)
        return rc, buf.getvalue()

    def lines(self):
        return [json.loads(x) for x in Path(self.ledger).read_text().splitlines()] if os.path.exists(self.ledger) else []

    def test_one_request_logs_the_providers_own_usage_in_the_exact_schema(self):
        rc, out = self.run_jev("--repo", "AsBuilt-Expo")
        (line,) = self.lines()
        self.assertEqual((rc, out.strip()), (0, "__JEV_P=0.970__"))
        self.assertEqual(sorted(line), ["class", "expert", "gate", "input_tokens", "model", "ms", "output_tokens",
                                        "repo", "ts"])
        self.assertEqual((line["repo"], line["expert"], line["model"]), ("AsBuilt-Expo", "jev", "jev-latest"))
        self.assertEqual((line["input_tokens"], line["output_tokens"]), (290, 20))
        self.assertEqual((line["class"], line["gate"]), (None, None))   # Jev has neither a class nor a gate
        self.assertRegex(line["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertIs(type(line["ms"]), int)
        self.assertNotIn("estimated", line)

    def test_a_questions_file_sums_every_request_into_one_line(self):
        rc, out = self.run_jev("--questions-file", os.path.join(self.tmp, "q.json"), "--repo", "r")
        (line,) = self.lines()
        self.assertEqual(len(self.requests), 2)
        self.assertIn("__JEV_Q0=0.970__", out)
        self.assertIn("__JEV_MIN=0.970__", out)
        self.assertEqual((line["input_tokens"], line["output_tokens"]), (580, 40))

    def test_repo_is_null_without_the_flag(self):
        self.run_jev()
        self.assertIsNone(self.lines()[0]["repo"])

    def test_a_response_without_usage_is_estimated_and_flagged(self):
        del self.reply["usage"]
        self.run_jev("--repo", "r")
        (line,) = self.lines()
        body = json.loads(self.requests[0][0].data)
        self.assertIs(line["estimated"], True)
        self.assertEqual(line["input_tokens"], len(json.dumps(body)) // 4)
        self.assertEqual(line["output_tokens"], len(json.dumps(self.reply)) // 4)

    def test_malformed_usage_is_treated_as_missing(self):
        self.reply["usage"] = {"input_tokens": "290", "output_tokens": -1}
        self.run_jev()
        self.assertIs(self.lines()[0]["estimated"], True)

    def test_a_failed_request_logs_zero_tokens_and_still_prints_its_error(self):
        self.reply = urllib.error.HTTPError("u", 500, "boom", {}, None)
        rc, out = self.run_jev("--repo", "r")
        (line,) = self.lines()
        self.assertEqual(out.strip(), "__JEV_P=HTTP_500__")
        self.assertEqual((line["input_tokens"], line["output_tokens"]), (0, 0))
        self.reply = TimeoutError()
        self.assertEqual(self.run_jev()[1].strip(), "__JEV_P=ERROR_TimeoutError__")

    def test_no_key_means_no_call_and_no_line(self):
        os.remove(self.key)
        rc, out = self.run_jev("--repo", "r")
        self.assertEqual((rc, out.strip(), self.requests, self.lines()), (0, "__JEV_P=NO_KEY__", [], []))

    def test_the_log_holds_no_text_and_the_key_is_never_printed(self):
        rc, out = self.run_jev("--repo", "r", "--questions-file", os.path.join(self.tmp, "q.json"))
        raw = Path(self.ledger).read_text()
        for text in ("SECRET-DIFF-MARKER", "ACCEPT-MARKER", "jane.doe", "prints hello", "state", "noul", "answers"):
            self.assertNotIn(text, raw)
        self.assertNotIn(KEY, out + raw)
        self.assertEqual(self.requests[0][0].get_header("Authorization"), "Bearer " + KEY)   # header only


class JevGateTests(JevTests):
    """The 2026-10-01 audit: Jev is gated in-process like codex_review; client repos never reach it."""

    def sent(self):
        return json.loads(self.requests[0][0].data)["state"]

    def test_client_class_is_skipped_and_sends_nothing(self):
        Path(jp.CLASSES_PATH).write_text(json.dumps({"repos": {"Acme": "client"}}))
        rc, out = self.run_jev("--repo", "acme")
        self.assertEqual((rc, out.strip()), (0, "__JEV_SKIPPED=class__"))
        self.assertEqual((self.requests, self.lines(), self.scanned), ([], [], []))

    def test_client_class_flag_is_skipped_too(self):
        _, out = self.run_jev("--class", "client")
        self.assertEqual(out.strip(), "__JEV_SKIPPED=class__")
        self.assertEqual(self.requests, [])

    def test_public_and_own_repos_still_run(self):
        Path(jp.CLASSES_PATH).write_text(json.dumps({"repos": {"pub": "public", "mine": "own"}}))
        Path(self.terms).write_text("Acme Corp\n")
        for repo in ("pub", "mine"):
            _, out = self.run_jev("--repo", repo, "--terms-file", self.terms)
            self.assertEqual(out.strip(), "__JEV_P=0.970__", repo)
        _, out = self.run_jev("--repo", "unlisted", "--terms-file", self.terms)   # unlisted = client
        self.assertEqual(out.strip(), "__JEV_SKIPPED=class__")

    def test_a_secret_stops_the_call(self):
        self.scan_result = "found"
        _, out = self.run_jev()
        self.assertEqual(out.strip(), "__JEV_SKIPPED=gate-skip__")
        self.assertEqual(self.requests, [])

    def test_scan_covers_acceptance_questions_and_diff(self):
        self.run_jev("--questions-file", os.path.join(self.tmp, "q.json"))
        (text,) = self.scanned
        for part in (ACCEPT, "Does it print hello?", "Is owner set?", "SECRET-DIFF-MARKER"):
            self.assertIn(part, text)

    def test_never_send_match_is_skipped(self):
        Path(os.path.join(self.tmp, "diff.txt")).write_text("diff --git a/data/x.csv b/data/x.csv\n+++ b/data/x.csv\n+1\n")
        _, out = self.run_jev("--never-send", "**/*.csv")
        self.assertEqual(out.strip(), "__JEV_SKIPPED=gate-local__")
        self.assertEqual(self.requests, [])

    def test_only_redacted_text_is_sent(self):
        Path(os.path.join(self.tmp, "acc.txt")).write_text("- mail bob@corp.io")
        self.run_jev("--questions-file", os.path.join(self.tmp, "q.json"))
        body = json.dumps([json.loads(r.data) for r, _ in self.requests])
        for original in ("jane.doe@planted-mail.org", "bob@corp.io"):
            self.assertNotIn(original, body)
        self.assertIn("[EMAIL_", body)

    def test_question_text_is_redacted_and_gated(self):
        Path(os.path.join(self.tmp, "q.json")).write_text(json.dumps(["Is carol@corp.io the owner?"]))
        self.run_jev("--questions-file", os.path.join(self.tmp, "q.json"))
        body = json.dumps([json.loads(r.data) for r, _ in self.requests])
        self.assertNotIn("carol@corp.io", body)
        self.assertIn("carol@corp.io", self.scanned[0])   # the scan sees the original, the wire does not

    def test_opener_does_not_follow_redirects_or_proxies(self):
        kinds = {type(h).__name__ for h in jp._OPENER.handlers}
        self.assertIn("_NoRedirect", kinds)
        self.assertNotIn("HTTPRedirectHandler", kinds)
        self.assertFalse([h for h in jp._OPENER.handlers if isinstance(h, urllib.request.ProxyHandler)])


if __name__ == "__main__":
    unittest.main()
