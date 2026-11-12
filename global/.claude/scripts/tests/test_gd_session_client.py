"""gd_session.js reads the client's name at run time from a private file; the public copy holds only a placeholder."""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tmpdirs import fresh_dir

SCRIPT = Path(__file__).resolve().parent.parent / "gd_session.js"
NODE = shutil.which("node")


def who(env_file):
    env = {k: v for k, v in os.environ.items() if k != "GD_CLIENT_FILE"}
    env["GD_CLIENT_FILE"] = str(env_file)
    r = subprocess.run([NODE, str(SCRIPT), "--print-who"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class ClientNameTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(NODE, "node is required to run this test")
        self.dir = Path(fresh_dir(self))

    def test_name_comes_from_the_private_file(self):
        f = self.dir / "gd-client.json"
        f.write_text(json.dumps({"name": "Sam Example"}))
        self.assertEqual(who(f), r"logged in as:?\s*Sam Example")

    def test_regex_characters_in_the_name_are_escaped(self):
        f = self.dir / "gd-client.json"
        f.write_text(json.dumps({"name": "A.B (Test) Example"}))
        self.assertEqual(who(f), r"logged in as:?\s*A\.B \(Test\) Example")

    def test_missing_file_falls_back_to_the_placeholder(self):
        self.assertEqual(who(self.dir / "absent.json"), r"logged in as:?\s*Pat Client")

    def test_bad_json_or_empty_name_falls_back_to_the_placeholder(self):
        for body in ("not json", json.dumps({"name": "  "}), json.dumps({"name": 7}), json.dumps(["x"])):
            f = self.dir / "gd-client.json"
            f.write_text(body)
            self.assertEqual(who(f), r"logged in as:?\s*Pat Client", body)

    def test_placeholder_is_the_only_fixed_name_in_the_script(self):
        self.assertEqual(SCRIPT.read_text().count("Pat Client"), 1)  # the fallback in clientName()


if __name__ == "__main__":
    unittest.main()
