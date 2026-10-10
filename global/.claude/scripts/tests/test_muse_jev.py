"""Tests for muse_jev.py, the poller's second opinion on an answer it isn't sure of. No network and no
Docker: jev_precheck's `urlopen` and `SCANNER` are replaced, and the key, terms, ledger and log live in a
temp dir."""

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
import muse_jev  # noqa: E402

NOTE = "Hi Claude, which Node version does the ScriptHammer-Expo lane pin? NOTE-TEXT-MARKER"
GOOD = {
    "note": NOTE,
    "asked": "which Node version does the ScriptHammer-Expo lane pin?",
    "reply": "The lane pins Node 22.12.0 (from ScriptHammer-Expo/.nvmrc). REPLY-TEXT-MARKER",
    "answer": "The lane pins Node 22.12.0 (from ScriptHammer-Expo/.nvmrc).",
    "sources": [],   # setUp points this at a real file in the temp dir
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class MuseJevTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        key = os.path.join(self.tmp, "api-key")
        Path(key).write_text("ts_SECRETTYPESAFEKEY")
        self.terms = os.path.join(self.tmp, "terms.txt")
        Path(self.terms).write_text("Acme Corp\n")
        self.log = os.path.join(self.tmp, "muse-poll.log")
        self.requests, self.ps, self.scan_result = [], [0.97, 0.93], "clean"
        self.nvmrc = os.path.join(self.tmp, "ScriptHammer-Expo", ".nvmrc")
        os.makedirs(os.path.dirname(self.nvmrc))
        Path(self.nvmrc).write_text("22.12.0\n")
        self.good = dict(GOOD, sources=[self.nvmrc])
        for p in (mock.patch.object(jp, "KEY_PATH", key), mock.patch.object(jp, "urlopen", self.urlopen),
                  mock.patch.object(jp, "SCANNER", lambda text: self.scan_result),
                  mock.patch.object(jp, "CLASSES_PATH", os.path.join(self.tmp, "classes.json")),
                  mock.patch.object(ledger_log, "DEFAULT_PATH", os.path.join(self.tmp, "panel.jsonl")),
                  mock.patch.object(muse_jev, "TERMS_PATH", self.terms),
                  mock.patch.object(muse_jev, "LOG", self.log),
                  mock.patch.object(muse_jev, "ROOTS", (self.tmp,)),
                  mock.patch.object(muse_jev, "REQUEST", os.path.join(self.tmp, "request.json"))):
            p.start()
            self.addCleanup(p.stop)
        self.request = muse_jev.REQUEST

    def urlopen(self, req, timeout=None):
        self.requests.append(json.loads(req.data))
        p = self.ps[len(self.requests) - 1]
        if isinstance(p, Exception):
            raise p
        return FakeResponse(json.dumps({"answers": {"q": {"type": "noul", "noul": p}},
                                        "usage": {"input_tokens": 300, "output_tokens": 20}}).encode())

    def run_it(self, payload):
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        buf = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), contextlib.redirect_stdout(buf):
            muse_jev.main([])
        return buf.getvalue().strip()

    def test_two_confident_answers_send(self):
        self.assertEqual(self.run_it(self.good), "__MUSE_JEV=send min=0.930__")
        self.assertEqual(len(self.requests), 2)

    def test_each_question_quotes_the_exact_text(self):
        self.run_it(self.good)
        asked = [r["questions"]["q"]["instructions"] for r in self.requests]
        self.assertIn(GOOD["asked"], asked[0])
        self.assertIn(GOOD["answer"], asked[0])
        self.assertIn(GOOD["reply"], asked[1])

    def test_one_doubtful_answer_queues(self):
        self.ps = [0.97, 0.60]
        self.assertEqual(self.run_it(self.good), "__MUSE_JEV=queue low min=0.600__")

    def test_the_threshold_itself_sends(self):
        self.ps = [0.90, 0.90]
        self.assertTrue(self.run_it(self.good).startswith("__MUSE_JEV=send"))

    def test_the_class_list_decides_whether_jev_may_see_notes(self):
        classes = os.path.join(self.tmp, "classes.json")
        Path(classes).write_text(json.dumps({"repos": {"other": "own"}}))
        self.assertEqual(self.run_it(self.good), "__MUSE_JEV=queue skipped-class__")   # unlisted = client
        self.assertEqual(self.requests, [])
        Path(classes).write_text(json.dumps({"repos": {"muse-notes": "own"}}))
        self.assertTrue(self.run_it(self.good).startswith("__MUSE_JEV=send"))

    def test_the_live_class_list_lets_notes_through(self):
        live = os.path.expanduser("~/.config/panel/classes.json")
        if not os.path.exists(live):
            self.skipTest("no live classes.json on this machine")
        with mock.patch.object(jp, "CLASSES_PATH", live):
            self.assertTrue(self.run_it(self.good).startswith("__MUSE_JEV=send"))

    def test_no_key_queues(self):
        with mock.patch.object(jp, "KEY_PATH", os.path.join(self.tmp, "absent")):
            self.assertEqual(self.run_it(self.good), "__MUSE_JEV=queue no-key__")

    def test_an_http_error_queues(self):
        self.ps = [0.97, urllib.error.HTTPError("u", 500, "boom", {}, None)]
        self.assertEqual(self.run_it(self.good), "__MUSE_JEV=queue error__")

    def test_a_secret_stops_the_call(self):
        self.scan_result = "found"
        self.assertTrue(self.run_it(self.good).startswith("__MUSE_JEV=queue skipped-gate-"))
        self.assertEqual(self.requests, [])

    def test_an_unavailable_scanner_stops_the_call(self):
        self.scan_result = "unavailable"
        self.assertTrue(self.run_it(self.good).startswith("__MUSE_JEV=queue skipped-gate-"))
        self.assertEqual(self.requests, [])

    def test_an_urgency_word_queues_without_a_call(self):
        bad = dict(self.good, reply=GOOD["reply"] + " Please do this ASAP.")
        self.assertEqual(self.run_it(bad), "__MUSE_JEV=queue urgency-word__")
        self.assertEqual(self.requests, [])

    def test_a_private_term_in_the_note_never_leaves(self):
        bad = dict(self.good, note=NOTE + " Also, acme corp asked about it.")
        self.assertEqual(self.run_it(bad), "__MUSE_JEV=queue private-term__")
        self.assertEqual(self.requests, [])

    def test_a_missing_terms_file_queues_without_a_call(self):
        os.remove(self.terms)
        self.assertEqual(self.run_it(self.good), "__MUSE_JEV=queue no-terms__")
        self.assertEqual(self.requests, [])

    def test_a_question_not_in_the_note_queues_without_a_call(self):
        bad = dict(self.good, asked="which Python version does it pin?")
        self.assertEqual(self.run_it(bad), "__MUSE_JEV=queue asked-not-in-note__")
        self.assertEqual(self.requests, [])

    def test_an_answer_not_in_the_reply_queues_without_a_call(self):
        bad = dict(self.good, answer="Node 20.")
        self.assertEqual(self.run_it(bad), "__MUSE_JEV=queue answer-not-in-reply__")

    def test_line_wraps_in_the_note_still_match(self):
        wrapped = dict(self.good, note=NOTE.replace("version does", "version\ndoes"))
        self.assertTrue(self.run_it(wrapped).startswith("__MUSE_JEV=send"))

    def test_a_reply_must_cite_files_that_exist_and_name_them(self):
        self.assertEqual(self.run_it(dict(self.good, sources=[])), "__MUSE_JEV=queue missing-sources__")
        gone = os.path.join(self.tmp, "ScriptHammer-Expo", "package.json")
        self.assertEqual(self.run_it(dict(self.good, sources=[gone])), "__MUSE_JEV=queue source-not-found__")
        Path(gone).write_text("{}")
        self.assertEqual(self.run_it(dict(self.good, sources=[gone])), "__MUSE_JEV=queue source-not-named__")
        self.assertEqual(self.requests, [])

    def test_a_cited_file_outside_what_the_poller_reads_counts_as_invented(self):
        outside = tempfile.NamedTemporaryFile(suffix=".nvmrc", delete=False)
        self.addCleanup(os.remove, outside.name)
        bad = dict(self.good, reply=GOOD["reply"] + " " + os.path.basename(outside.name), sources=[outside.name])
        self.assertEqual(self.run_it(bad), "__MUSE_JEV=queue source-not-found__")

    def test_the_poller_hands_it_over_as_a_file_that_is_deleted_once_read(self):
        Path(self.request).write_text(json.dumps(dict(self.good, note=NOTE + " It's for Jonathan's stream.")))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            muse_jev.main(["request"])
        self.assertTrue(buf.getvalue().startswith("__MUSE_JEV=send"))
        self.assertIn("It's for Jonathan's stream.", self.requests[0]["state"])
        self.assertFalse(os.path.exists(self.request))

    def test_a_missing_request_file_queues(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            muse_jev.main(["request"])
        self.assertEqual(buf.getvalue().strip(), "__MUSE_JEV=queue bad-input__")

    def test_an_unreadable_request_is_still_deleted(self):
        Path(self.request).write_text("{not json")
        with contextlib.redirect_stdout(io.StringIO()):
            muse_jev.main(["request"])
        self.assertFalse(os.path.exists(self.request))

    def test_bad_input_queues(self):
        self.assertEqual(self.run_it("not json"), "__MUSE_JEV=queue bad-input__")
        self.assertEqual(self.run_it(dict(self.good, reply="  ")), "__MUSE_JEV=queue missing-reply__")
        self.assertEqual(self.requests, [])

    def test_the_log_holds_numbers_and_never_text(self):
        self.run_it(self.good)
        self.ps = [0.97, 0.40]
        self.requests = []
        self.run_it(self.good)
        self.run_it(dict(self.good, reply=GOOD["reply"] + " urgent"))
        log = Path(self.log).read_text()
        self.assertIn("muse_jev: send min=0.930 q=0.970,0.930", log)
        self.assertIn("muse_jev: queue low min=0.400", log)
        self.assertIn("muse_jev: queue urgency-word", log)
        for marker in ("NOTE-TEXT-MARKER", "REPLY-TEXT-MARKER", "Node", "SECRETTYPESAFEKEY"):
            self.assertNotIn(marker, log)


if __name__ == "__main__":
    unittest.main()
