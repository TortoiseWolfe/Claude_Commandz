"""user_model.py: read and set the user default model without printing or disturbing anything else in the settings file.
The settings here are invented; the live file is never opened (USER_MODEL_SETTINGS points at a temp one)."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tmpdirs import fresh_dir  # noqa: E402

SCRIPT = str(HERE.parent / "user_model.py")
BODY = '{\n  "env": {\n    "KEY": "invented-secret-value"\n  },\n  "model": "opus",\n  "hooks": []\n}\n'


class UserModel(unittest.TestCase):
    def setUp(self):
        self.f = Path(fresh_dir(self)) / "settings.json"
        self.f.write_text(BODY)

    def run_um(self, *args, path=None):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True,
                              env=dict(os.environ, USER_MODEL_SETTINGS=str(path or self.f)))

    def test_get_prints_the_model_and_nothing_else(self):
        r = self.run_um("get")
        self.assertEqual((r.returncode, r.stdout), (0, "opus\n"))
        self.assertNotIn("invented-secret-value", r.stdout + r.stderr)

    def test_get_says_dash_when_there_is_no_model_key(self):
        self.f.write_text('{\n  "env": {}\n}\n')
        self.assertEqual(self.run_um("get").stdout, "-\n")

    def test_set_changes_only_the_model_value(self):
        self.assertEqual(self.run_um("set", "sonnet").returncode, 0)
        self.assertEqual(self.f.read_text(), BODY.replace('"opus"', '"sonnet"'))

    def test_unset_removes_only_the_model_key(self):
        self.assertEqual(self.run_um("unset").returncode, 0)
        self.assertEqual(self.f.read_text(), '{\n  "env": {\n    "KEY": "invented-secret-value"\n  },\n  "hooks": []\n}\n')

    def test_set_adds_the_key_when_it_was_absent(self):
        self.f.write_text('{\n  "env": {}\n}\n')
        self.run_um("set", "fable")
        self.assertEqual(self.run_um("get").stdout, "fable\n")

    def test_set_keeps_the_file_mode_and_leaves_no_temp_file(self):
        self.f.chmod(0o600)
        self.run_um("set", "haiku")
        self.assertEqual(oct(self.f.stat().st_mode & 0o777), "0o600")
        self.assertEqual([p.name for p in self.f.parent.iterdir()], ["settings.json"])

    def test_a_file_that_would_not_round_trip_is_refused_and_untouched(self):
        odd = '{"model":"opus",   "env": {}}\n'
        self.f.write_text(odd)
        r = self.run_um("set", "sonnet")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.f.read_text(), odd)
        self.assertEqual(self.run_um("check").stdout, "differs\n")
        self.assertEqual(self.run_um("get").stdout, "opus\n")   # reading still works

    def test_check_says_ok_for_a_clean_file(self):
        self.assertEqual(self.run_um("check").stdout, "ok\n")

    def test_bad_model_name_and_bad_command_are_refused(self):
        self.assertEqual(self.run_um("set", "so net;rm").returncode, 2)
        self.assertEqual(self.run_um("set").returncode, 2)
        self.assertEqual(self.run_um("frobnicate").returncode, 2)
        self.assertEqual(self.f.read_text(), BODY)

    def test_an_unreadable_or_non_object_file_fails_without_echoing_its_content(self):
        self.f.write_text("[1, 2]\n")
        r = self.run_um("get")
        self.assertEqual(r.returncode, 1)
        r = self.run_um("get", path=self.f.parent / "missing.json")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("invented", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
