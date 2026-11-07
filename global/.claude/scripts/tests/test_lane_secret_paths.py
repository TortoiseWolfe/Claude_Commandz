"""The one list of settings files a lane must never see (issue 332): lane_secret_paths.py, and that the per-engine denies still follow it."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import lane_secret_paths as lsp  # noqa: E402
import agy_deny  # noqa: E402


def build_tree(root):
    """Fixture folders with empty files: names matter here, contents never do."""
    for rel in [
        "app/.env", "app/.env.local", "app/.env.production", "app/prod.env", "app/.envrc", "app/.env.example", "app/.env.sample",
        "app/config.defaults", "app/deploy.pem", "app/id_rsa", "app/id_ed25519.pub", "app/.netrc", "app/service-api-key.txt", "app/readme.md",
        "app/sealed/deploy.key", "app/.sealed-keys/k", "app/node_modules/pkg/.env", "app/.git/hooks/x.env", "app/nested/deeper/.env",
    ]:
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")


class SharedListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        build_tree(self.root)
        self.home = os.path.join(self.root, "home")
        for d in lsp.HOME_DIRS:
            os.makedirs(os.path.join(self.home, d))

    def tearDown(self):
        self.tmp.cleanup()

    def listed(self):
        files, dirs = lsp.hide_list([os.path.join(self.root, "app")], self.home)
        rel = lambda paths: sorted(os.path.relpath(p, self.root) for p in paths)  # noqa: E731
        return rel(files), rel(dirs)

    def test_env_files_key_files_and_sealed_folders_are_listed(self):
        files, dirs = self.listed()
        for want in ["app/.env", "app/.env.local", "app/.env.production", "app/prod.env", "app/.envrc", "app/deploy.pem", "app/id_rsa",
                     "app/id_ed25519.pub", "app/.netrc", "app/service-api-key.txt", "app/nested/deeper/.env"]:
            self.assertIn(want, files)
        self.assertIn("app/sealed", dirs)
        self.assertIn("app/.sealed-keys", dirs)

    def test_the_fixed_home_folders_are_listed(self):
        _, dirs = self.listed()
        for d in lsp.HOME_DIRS:
            self.assertIn(os.path.join("home", d), dirs)

    def test_placeholders_ordinary_files_and_vendored_folders_are_not_listed(self):
        files, dirs = self.listed()
        for not_wanted in ["app/.env.example", "app/.env.sample", "app/config.defaults", "app/readme.md", "app/node_modules/pkg/.env", "app/.git/hooks/x.env"]:
            self.assertNotIn(not_wanted, files)
        # A file inside a sealed folder is hidden with its folder, not listed on its own.
        self.assertNotIn("app/sealed/deploy.key", files)

    def test_a_symlink_is_never_listed(self):
        os.symlink(os.path.join(self.root, "app/readme.md"), os.path.join(self.root, "app/link.env"))
        files, _ = self.listed()
        self.assertNotIn("app/link.env", files)

    def test_a_runaway_walk_fails_closed(self):
        old = lsp.MAX_PATHS
        lsp.MAX_PATHS = 3
        try:
            with self.assertRaises(OverflowError):
                lsp.hide_list([os.path.join(self.root, "app")], self.home)
        finally:
            lsp.MAX_PATHS = old

    def test_the_command_line_prints_nul_ended_tagged_paths_and_needs_a_root(self):
        env = dict(os.environ, HOME=self.home)
        out = subprocess.run([sys.executable, "-I", str(SCRIPTS / "lane_secret_paths.py"), "hide", os.path.join(self.root, "app")],
                             capture_output=True, env=env, check=True).stdout
        entries = [e for e in out.split(b"\0") if e]
        self.assertIn(b"F" + os.fsencode(os.path.join(self.root, "app/.env")), entries)
        self.assertIn(b"D" + os.fsencode(os.path.join(self.root, "app/sealed")), entries)
        self.assertEqual(subprocess.run([sys.executable, "-I", str(SCRIPTS / "lane_secret_paths.py"), "hide"], capture_output=True, env=env).returncode, 2)


class SecondLayerFollowsTheListTests(unittest.TestCase):
    def test_opencode_lane_denies_every_read_glob_of_the_shared_list(self):
        text = (SCRIPTS / "opencode_lane.sh").read_text()
        read_line = next(line for line in text.splitlines() if line.lstrip().startswith('"read":'))
        for glob in lsp.OPENCODE_READ_GLOBS:
            self.assertIn('"%s":"deny"' % glob, read_line, "opencode_lane.sh no longer denies reads of %s" % glob)

    def test_agy_deny_lists_what_the_shared_list_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.realpath(tmp)
            build_tree(root)
            listed = set(agy_deny.secret_files(os.path.join(root, "app")))
            joined = "\n".join(listed)
            for name in ["app/.env", "app/prod.env", "app/deploy.pem", "app/id_rsa", "app/service-api-key.txt", "app/sealed/"]:
                self.assertIn(os.path.join(root, name), joined, "agy_deny no longer covers " + name)


if __name__ == "__main__":
    unittest.main()
