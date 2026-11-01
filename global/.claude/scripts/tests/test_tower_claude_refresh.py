import os
import stat
import subprocess
import tempfile
import time
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "tower_claude_refresh.sh")

# A stand-in for hub/bootstrap.sh: --dry-run lists what it would install; a real run installs it.
STUB = """#!/usr/bin/env bash
t="$HOME/.claude/scripts/changed.sh"
if [ "$2" = --dry-run ]; then echo "would: install $t"; echo "would: install $HOME/.claude/scripts/new.sh"; exit 0; fi
echo upstream >"$t"; echo upstream >"$HOME/.claude/scripts/new.sh"; echo "refresh done: 2 installed, 0 already matched, 0 skipped"
"""


class Refresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.hub = os.path.join(self.tmp.name, "hub")
        os.makedirs(os.path.join(self.home, ".config", "afa"))
        os.makedirs(os.path.join(self.home, ".claude", "scripts"))
        os.makedirs(os.path.join(self.hub, "hub"))
        with open(os.path.join(self.home, ".claude", "scripts", "changed.sh"), "w") as fh:
            fh.write("tower edit\n")
        boot = os.path.join(self.hub, "hub", "bootstrap.sh")
        with open(boot, "w") as fh:
            fh.write(STUB)
        os.chmod(boot, os.stat(boot).st_mode | stat.S_IXUSR)

    def machine(self, name):
        with open(os.path.join(self.home, ".config", "afa", "machine"), "w") as fh:
            fh.write(name + "\n")

    def run_it(self, *args):
        env = dict(os.environ, HOME=self.home, HUB_DIR=self.hub)
        return subprocess.run(["bash", SCRIPT, *args], env=env, capture_output=True, text=True, timeout=60)

    def path(self, *p):
        return os.path.join(self.home, *p)

    def test_the_tower_is_refreshed_and_the_replaced_file_is_backed_up(self):
        self.machine("NCC-74656")
        self.assertEqual(self.run_it().returncode, 0)
        self.assertEqual(open(self.path(".claude", "scripts", "changed.sh")).read(), "upstream\n")
        self.assertTrue(os.path.exists(self.path(".claude", "scripts", "new.sh")))
        backups = [os.path.join(d, f) for d, _, fs in os.walk(self.path(".claude", "state", "tower-refresh-backup")) for f in fs]
        self.assertEqual([os.path.basename(b) for b in backups], ["changed.sh"])
        self.assertEqual(open(backups[0]).read(), "tower edit\n")
        self.assertIn("refresh done", open(self.path(".claude", "state", "tower-refresh.log")).read())

    def test_it_runs_at_most_once_an_hour_unless_forced(self):
        self.machine("NCC-74656")
        self.run_it()
        with open(self.path(".claude", "scripts", "changed.sh"), "w") as fh:
            fh.write("second tower edit\n")
        self.run_it()
        self.assertEqual(open(self.path(".claude", "scripts", "changed.sh")).read(), "second tower edit\n")
        self.run_it("--force")
        self.assertEqual(open(self.path(".claude", "scripts", "changed.sh")).read(), "upstream\n")

    def test_nx01_never_pulls_itself(self):
        self.machine("NX-01")
        self.assertEqual(self.run_it("--force").returncode, 0)
        self.assertEqual(open(self.path(".claude", "scripts", "changed.sh")).read(), "tower edit\n")

    def test_a_failed_dry_run_changes_nothing_and_is_logged(self):
        self.machine("NCC-74656")
        with open(os.path.join(self.hub, "hub", "bootstrap.sh"), "w") as fh:
            fh.write("#!/usr/bin/env bash\necho broken >&2\nexit 1\n")
        self.assertEqual(self.run_it("--force").returncode, 1)
        self.assertEqual(open(self.path(".claude", "scripts", "changed.sh")).read(), "tower edit\n")
        self.assertIn("dry run failed", open(self.path(".claude", "state", "tower-refresh.log")).read())


if __name__ == "__main__":
    unittest.main()
