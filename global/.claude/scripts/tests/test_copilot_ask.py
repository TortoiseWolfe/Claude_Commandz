"""Tests for copilot_ask.sh with a fake `copilot` and a fake `gh` first on PATH. No network, no real login."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "copilot_ask.sh")
FAKE_COPILOT = """#!/usr/bin/env bash
python3 - "$@" <<'PY'
import json, os, sys
home = os.environ.get("COPILOT_HOME")
json.dump({"argv": sys.argv[1:], "env": sorted(os.environ), "copilot_home": home,
           "home_is_dir": bool(home) and os.path.isdir(home), "token": os.environ.get("GH_TOKEN"),
           "mode": oct(os.stat(home).st_mode & 0o777)},
          open("@OUT@", "w"))
PY
echo "reply"
"""


class CopilotAskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.bin = os.path.join(self.tmp, "bin")
        os.mkdir(self.bin)
        self.out = os.path.join(self.tmp, "seen.json")
        self.write("copilot", FAKE_COPILOT.replace("@OUT@", self.out))

    def write(self, name, text):
        p = os.path.join(self.bin, name)
        Path(p).write_text(text)
        os.chmod(p, 0o755)

    def gh(self, token, rc=0):
        self.write("gh", f"#!/usr/bin/env bash\necho -n '{token}'\nexit {rc}\n")

    def run_script(self, *args):
        env = {"PATH": self.bin + ":" + os.environ["PATH"], "HOME": self.tmp, "TMPDIR": self.tmp, "SECRET_TOKEN": "leak", "AWS_SECRET_ACCESS_KEY": "leak"}
        return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, env=env, timeout=30)

    def test_empty_token_exits_3_and_never_runs_copilot(self):
        self.gh("")
        p = self.run_script("hello")
        self.assertEqual(p.returncode, 3)
        self.assertFalse(os.path.exists(self.out))

    def test_signed_out_gh_exits_3(self):
        self.gh("", rc=1)
        self.assertEqual(self.run_script("hello").returncode, 3)

    def test_leading_dash_prompt_exits_2(self):
        self.gh("gho_fake")
        for prompt in ("-p x", "--yolo"):
            p = self.run_script(prompt)
            self.assertEqual(p.returncode, 2, prompt)
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(self.run_script().returncode, 2)

    def test_read_only_flags_and_prompt_reach_copilot(self):
        self.gh("gho_fake")
        p = self.run_script("review this")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "reply"))
        seen = json.loads(Path(self.out).read_text())
        self.assertEqual(seen["argv"], ["-s", "--disable-builtin-mcps", "--deny-tool", "shell",
                                        "--deny-tool", "write", "-p", "review this"])

    def test_environment_is_minimal(self):
        self.gh("gho_fake")
        self.run_script("hello")
        seen = json.loads(Path(self.out).read_text())
        self.assertEqual(seen["token"], "gho_fake")
        self.assertNotIn("SECRET_TOKEN", seen["env"])
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", seen["env"])
        allowed = {"PATH", "HOME", "LANG", "TERM", "GH_TOKEN", "COPILOT_HOME", "_", "PWD", "SHLVL", "OLDPWD"}
        self.assertLessEqual(set(seen["env"]), allowed)

    def test_copilot_home_is_a_private_temp_dir_gone_afterwards(self):
        self.gh("gho_fake")
        self.run_script("hello")
        seen = json.loads(Path(self.out).read_text())
        home = seen["copilot_home"]
        self.assertTrue(seen["home_is_dir"])
        self.assertTrue(home.startswith(self.tmp))
        self.assertNotEqual(home, os.path.join(self.tmp, ".copilot"))
        self.assertEqual(seen["mode"], "0o700")
        self.assertFalse(os.path.exists(home))

    def test_temp_dir_is_removed_even_when_copilot_fails(self):
        self.gh("gho_fake")
        self.write("copilot", '#!/usr/bin/env bash\necho "$COPILOT_HOME" > ' + self.out + '\nexit 7\n')
        p = self.run_script("hello")
        self.assertEqual(p.returncode, 7)
        self.assertFalse(os.path.exists(Path(self.out).read_text().strip()))


if __name__ == "__main__":
    unittest.main()
