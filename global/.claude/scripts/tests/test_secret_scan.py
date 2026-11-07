"""secret_scan.py: runs both checks, prints names only, and never calls a check that did not run "clean".

A fake gitleaks and a fake known_secrets_scan stand in for the real programs; every secret here is an invented string."""

import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
from tmpdirs import fresh_dir  # noqa: E402

TOOL = str(SCRIPTS / "secret_scan.py")
SECRET = "invented-secret-value-0001"

FAKE_GITLEAKS = """#!{py}
import json, os, sys
a = sys.argv[1:]
open(os.environ["FAKE_GL_ARGS"], "w").write(" ".join(a) + "\\n" + os.getcwd())
if os.environ.get("FAKE_GL_EXIT", "0") not in ("0", "1"):
    sys.stderr.write("fatal: could not parse near {secret}\\n")
    sys.exit(int(os.environ["FAKE_GL_EXIT"]))
report = json.loads(os.environ.get("FAKE_GL_FINDINGS", "[]"))
json.dump(report, open(a[a.index("--report-path") + 1], "w"))
sys.exit(1 if report else 0)
"""

FAKE_KNOWN = """#!{py}
import os, sys
code = int(os.environ.get("FAKE_KNOWN_EXIT", "0"))
if os.environ.get("FAKE_KNOWN_ARGS"):
    open(os.environ["FAKE_KNOWN_ARGS"], "w").write(" ".join(sys.argv[1:]))
if code == 1:
    print("example.env API_KEY: " + sys.argv[-1] + "/notes.txt")
sys.exit(code)
"""


class SecretScan(unittest.TestCase):
    def setUp(self):
        self.d = Path(fresh_dir(self))
        for name, body in (("gitleaks", FAKE_GITLEAKS), ("known", FAKE_KNOWN)):
            p = self.d / name
            p.write_text(body.format(py=sys.executable, secret=SECRET))
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
        self.target = self.d / "target"
        self.target.mkdir()
        (self.target / "notes.txt").write_text("nothing here\n")
        self.args = self.d / "gl-args.txt"

    def scan(self, *argv, findings=None, gl_exit=0, known_exit=0, known_prog=None, cwd=None, extra_env=None):
        env = dict(os.environ, GITLEAKS=str(self.d / "gitleaks"), KNOWN_SECRETS_SCAN=known_prog or str(self.d / "known"),
                   FAKE_GL_ARGS=str(self.args), FAKE_GL_EXIT=str(gl_exit), FAKE_KNOWN_EXIT=str(known_exit),
                   FAKE_GL_FINDINGS=json.dumps(findings or []), FAKE_KNOWN_ARGS=str(self.d / "known-args.txt"))
        env.pop("GITLEAKS_CONFIG", None)
        env.update(extra_env or {})
        return subprocess.run([sys.executable, TOOL, *argv], capture_output=True, text=True, env=env, cwd=cwd)

    def test_clean_path_prints_clean_and_exits_0(self):
        r = self.scan(str(self.target))
        self.assertEqual((r.returncode, r.stdout), (0, "secret_scan: clean\n"))

    def test_a_gitleaks_finding_prints_rule_file_and_line_and_exits_1(self):
        r = self.scan(str(self.target), findings=[{"RuleID": "generic-api-key", "File": "a/b.txt", "StartLine": 12,
                                                    "Secret": SECRET, "Match": "key=" + SECRET}])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines(), ["gitleaks  generic-api-key  a/b.txt:12", "secret_scan: 1 finding(s)"])

    def test_the_matched_text_is_never_printed(self):
        r = self.scan(str(self.target), findings=[{"RuleID": "r", "File": "f", "StartLine": 1, "Secret": SECRET,
                                                    "Match": SECRET, "Line": SECRET}])
        self.assertNotIn(SECRET, r.stdout + r.stderr)

    def test_a_known_secret_finding_prints_the_names_and_exits_1(self):
        r = self.scan(str(self.target), known_exit=1)
        self.assertEqual(r.returncode, 1)
        self.assertIn("known     example.env API_KEY:", r.stdout)
        self.assertTrue(r.stdout.rstrip().endswith("secret_scan: 1 finding(s)"))

    def test_both_kinds_are_counted_together(self):
        r = self.scan(str(self.target), known_exit=1, findings=[{"RuleID": "r", "File": "f", "StartLine": 3}])
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.rstrip().endswith("secret_scan: 2 finding(s)"))

    def test_gitleaks_failing_is_exit_2_not_clean_and_its_output_is_not_echoed(self):
        r = self.scan(str(self.target), gl_exit=3)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("clean", r.stdout)
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertIn("gitleaks failed", r.stderr)

    def test_known_secrets_scan_failing_or_missing_is_exit_2(self):
        r = self.scan(str(self.target), known_exit=2)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("clean", r.stdout)
        r = self.scan(str(self.target), known_prog=str(self.d / "nope"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("not found", r.stderr)

    def test_a_missing_path_is_exit_2(self):
        r = self.scan(str(self.d / "nope"))
        self.assertEqual(r.returncode, 2)

    def test_gitleaks_is_asked_for_a_redacted_json_report(self):
        self.scan(str(self.target))
        said = self.args.read_text()
        for want in ("dir", "--redact", "--report-format json", "--no-banner"):
            self.assertIn(want, said)

    def test_the_index_mode_scans_what_is_staged_not_what_is_in_the_folder(self):
        repo = self.d / "repo"
        repo.mkdir()
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.test", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@example.test")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
        (repo / "staged.txt").write_text("staged\n")
        (repo / "untracked.txt").write_text("not staged\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=repo, check=True, env=env)
        r = self.scan(cwd=repo)
        self.assertEqual((r.returncode, r.stdout), (0, "secret_scan: clean\n"))
        scanned = self.args.read_text().split("\n")[1]
        self.assertNotEqual(Path(scanned).resolve(), repo.resolve())
        self.assertTrue(scanned.endswith("/index"), scanned)

    def test_gitleaks_runs_inside_the_folder_with_a_relative_name_so_gitleaksignore_matches(self):
        """.gitleaksignore lines are "<relative file>:<rule>:<line>"; an absolute path in a finding would never match one."""
        self.scan(str(self.target))
        first, cwd = self.args.read_text().split("\n")
        self.assertTrue(first.startswith("dir . "), first)
        self.assertEqual(Path(cwd).resolve(), self.target.resolve())
        (self.target / "one.txt").write_text("x")
        self.scan(str(self.target / "one.txt"))
        first, cwd = self.args.read_text().split("\n")
        self.assertTrue(first.startswith("dir one.txt "), first)
        self.assertEqual(Path(cwd).resolve(), self.target.resolve())

    def test_known_secrets_scan_runs_strict_like_the_push_hook(self):
        """Without --strict an unreadable file is skipped silently, so the scan would call a folder clean that it never read."""
        self.scan(str(self.target))
        self.assertIn("--strict", self.d.joinpath("known-args.txt").read_text().split())

    def test_a_scanned_folder_with_its_own_gitleaks_rules_is_refused_not_trusted(self):
        """A rules file inside the scanned text could switch the rules off, so it is exit 2 and gitleaks is never run."""
        (self.target / ".gitleaks.toml").write_text("title = 'invented'\n")
        r = self.scan(str(self.target))
        self.assertEqual(r.returncode, 2)
        self.assertIn(".gitleaks.toml", r.stderr)
        self.assertFalse(self.args.exists())

    def test_a_rules_file_named_in_the_environment_is_not_passed_to_gitleaks(self):
        """GITLEAKS_CONFIG would point gitleaks at rules this command did not choose."""
        probe = self.d / "gitleaks-env"
        probe.write_text(FAKE_GITLEAKS.format(py=sys.executable, secret=SECRET).replace(
            'open(os.environ["FAKE_GL_ARGS"], "w").write(" ".join(a)',
            'open(os.environ["FAKE_GL_ARGS"], "w").write(os.environ.get("GITLEAKS_CONFIG", "unset") + " ".join(a)'))
        probe.chmod(probe.stat().st_mode | stat.S_IXUSR)
        r = self.scan(str(self.target), extra_env={"GITLEAKS": str(probe), "GITLEAKS_CONFIG": "/somewhere/else.toml"})
        self.assertEqual(r.returncode, 0)
        self.assertTrue(self.args.read_text().startswith("unsetdir . "), self.args.read_text()[:60])

    def test_the_work_folder_is_not_opened_to_other_users(self):
        """The snapshot of the index sits in the work folder; it keeps the private mode the system gave it."""
        self.assertNotIn("chmod(work", Path(TOOL).read_text())

    def test_index_mode_outside_a_git_repo_is_exit_2(self):
        r = self.scan("--index", cwd=self.d)
        self.assertEqual(r.returncode, 2)
        self.assertIn("git index", r.stderr)


if __name__ == "__main__":
    unittest.main()
