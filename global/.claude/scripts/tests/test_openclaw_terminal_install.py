"""openclaw_terminal_install.py: the Receptionist profile goes into Windows Terminal's settings once, keeps everything else,
opens the existing chat by key (never a new session), and comes out again on rollback."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_terminal_install as ti  # noqa: E402

SETTINGS = json.dumps({"defaultProfile": "{x}", "copyOnSelect": True,
                       "profiles": {"defaults": {}, "list": [{"guid": "{a}", "name": "Windows PowerShell"}]}}, indent=4)


class Profile(unittest.TestCase):
    def test_added_once_and_the_rest_kept(self):
        once = ti.add_profile(SETTINGS)
        twice = ti.add_profile(once)
        self.assertEqual(once, twice)
        d = json.loads(twice)
        self.assertEqual([p["name"] for p in d["profiles"]["list"]], ["Windows PowerShell", "Receptionist"])
        self.assertEqual(d["defaultProfile"], "{x}")
        self.assertTrue(d["copyOnSelect"])

    def test_opens_the_existing_main_chat_in_the_gateway_distro(self):
        c = json.loads(ti.add_profile(SETTINGS))["profiles"]["list"][-1]["commandline"]
        self.assertEqual(c, "wsl.exe -d OpenClawGateway -u openclaw -- openclaw tui --session agent:main:main")  # tests force the wsl route
        self.assertIn("openclaw tui --session agent:main:main", c)
        for word in ("--local", "/new", "--deliver"):
            self.assertNotIn(word, c)

    def test_on_docker_it_opens_the_container_with_a_terminal(self):
        with mock.patch.dict(os.environ, {"AFA_OC_GATEWAY": "docker"}):
            c = json.loads(ti.add_profile(SETTINGS))["profiles"]["list"][-1]["commandline"]
        self.assertEqual(c, "docker exec -it -w /home/node openclaw-openclaw-gateway-1 openclaw tui --session agent:main:main")
        self.assertNotIn("wsl.exe", c)

    def test_rollback_removes_only_ours(self):
        back = json.loads(ti.remove_profile(ti.add_profile(SETTINGS)))
        self.assertEqual([p["name"] for p in back["profiles"]["list"]], ["Windows PowerShell"])

    def test_has_profile(self):
        self.assertFalse(ti.has_profile(SETTINGS))
        self.assertTrue(ti.has_profile(ti.add_profile(SETTINGS)))

    def test_bom_and_indent_are_tolerated(self):
        out = ti.add_profile("\ufeff" + SETTINGS)
        self.assertIn('\n    "profiles"', out)

    def test_shortcut_runs_wt_with_the_profile(self):
        s = ti.shortcut_script(r"C:\x\Receptionist.lnk", r"C:\w\wt.exe")
        self.assertIn(r"TargetPath='C:\w\wt.exe'", s)
        self.assertIn("-p \"Receptionist\"", s)


if __name__ == "__main__":
    unittest.main()
