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

DIFF = '+++ b/app.py\n+owner = "jane.doe@gmail.com"  # SECRET-DIFF-MARKER\n+print("hello")\n'
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
        self.requests, self.reply = [], {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.97}},
                                         "usage": {"input_tokens": 290, "output_tokens": 20}}
        for p in (mock.patch.object(jp, "KEY_PATH", self.key), mock.patch.object(jp, "urlopen", self.urlopen),
                  mock.patch.object(ledger_log, "DEFAULT_PATH", self.ledger)):
            p.start()
            self.addCleanup(p.stop)
        Path(os.path.join(self.tmp, "acc.txt")).write_text(ACCEPT)
        Path(os.path.join(self.tmp, "diff.txt")).write_text(DIFF)
        Path(os.path.join(self.tmp, "q.json")).write_text(json.dumps(["Does it print hello?", "Is owner set?"]))

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


if __name__ == "__main__":
    unittest.main()
