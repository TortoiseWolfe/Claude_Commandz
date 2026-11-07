"""console_probe.py: reads the key from the environment (then a file), prints only the status and the message, never the key.

A small local server stands in for the API; the key is an invented string."""

import json
import os
import socket
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
from tmpdirs import fresh_dir  # noqa: E402

TOOL = str(SCRIPTS / "console_probe.py")
FAKE = "invented-console-key-0001"


class Api(BaseHTTPRequestHandler):
    status = 200
    message = ""
    seen = []

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        Api.seen.append(self.headers.get("x-api-key"))
        body = json.dumps({"error": {"message": Api.message}} if Api.status != 200 else {"type": "message"}).encode()
        self.send_response(Api.status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class ConsoleProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Api)
        cls.url = "http://127.0.0.1:%d/v1/messages" % cls.srv.server_port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.srv.server_close)
        cls.addClassCleanup(cls.srv.shutdown)

    def setUp(self):
        self.d = Path(fresh_dir(self))
        Api.status, Api.message, Api.seen = 200, "", []

    def probe(self, env_key=FAKE, key_file=None, url=None):
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_WORKER_KEY", "CONSOLE_PROBE_KEY_FILE")}
        env["CONSOLE_PROBE_URL"] = url or self.url
        env["HOME"] = str(self.d)
        env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1"
        if env_key is not None:
            env["ANTHROPIC_WORKER_KEY"] = env_key
        if key_file is not None:
            env["CONSOLE_PROBE_KEY_FILE"] = str(key_file)
        return subprocess.run([sys.executable, "-I", TOOL], capture_output=True, text=True, env=env)

    def closed_url(self):
        """A local address nothing listens on (a port just freed), so a refusal is instant."""
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return "http://127.0.0.1:%d/none" % port

    def test_200_prints_ok_exits_0_and_sends_the_key_to_the_api_only(self):
        r = self.probe()
        self.assertEqual((r.returncode, r.stdout), (0, "HTTP 200 OK: the Console key answers\n"))
        self.assertEqual(Api.seen, [FAKE])

    def test_an_error_status_prints_status_and_message_and_exits_1(self):
        Api.status, Api.message = 400, "Your credit balance is too low"
        r = self.probe()
        self.assertEqual((r.returncode, r.stdout), (1, "HTTP 400 Your credit balance is too low\n"))

    def test_the_message_is_cut_to_160_characters(self):
        Api.status, Api.message = 429, "x" * 500
        self.assertEqual(self.probe().stdout, "HTTP 429 " + "x" * 160 + "\n")

    def test_the_key_never_appears_even_if_the_api_message_contains_it(self):
        Api.status, Api.message = 401, "bad key " + FAKE + " given"
        r = self.probe()
        self.assertNotIn(FAKE, r.stdout + r.stderr)
        self.assertIn("[key]", r.stdout)

    def test_the_key_never_appears_on_success_or_when_nothing_answers(self):
        for r in (self.probe(), self.probe(url=self.closed_url())):
            self.assertNotIn(FAKE, r.stdout + r.stderr)

    def test_no_answer_exits_3(self):
        r = self.probe(url=self.closed_url())
        self.assertEqual(r.returncode, 3)
        self.assertIn("no answer", r.stdout)

    def test_the_key_file_is_the_fallback(self):
        f = self.d / "key"
        f.write_text("  file-key-0002\n")
        r = self.probe(env_key=None, key_file=f)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(Api.seen, ["file-key-0002"])

    def test_the_environment_wins_over_the_file(self):
        f = self.d / "key"
        f.write_text("file-key-0002")
        self.probe(key_file=f)
        self.assertEqual(Api.seen, [FAKE])

    def test_no_key_anywhere_exits_2_and_calls_nothing(self):
        r = self.probe(env_key=None, key_file=self.d / "missing")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no Console key", r.stdout)
        self.assertEqual(Api.seen, [])

    def test_a_non_local_url_override_is_ignored_so_the_key_cannot_be_redirected(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("console_probe_mod", TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        os.environ["CONSOLE_PROBE_URL"] = "https://evil.example.test/steal"
        self.addCleanup(os.environ.pop, "CONSOLE_PROBE_URL", None)
        self.assertEqual(mod.api_url(), mod.DEFAULT_URL)


if __name__ == "__main__":
    unittest.main()
