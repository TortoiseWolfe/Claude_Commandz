"""openclaw_ask_install.sh: the command must reach the gateway intact. wsl.exe re-parses everything after "--"
through a shell (so "$d" came through empty); after -e the arguments are passed as they are. The fake wsl.exe
here does both, so the script only passes when it uses -e."""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "openclaw_ask_install.sh"

FAKE_WSL = r"""#!/usr/bin/env bash
# args: -d DISTRO -u USER (-e CMD... | -- CMD...)
shift 4
home="$FAKE_HOME"
if [ "$1" = "-e" ]; then
  shift
  args=()
  for a in "$@"; do args+=("${a//\/home\/openclaw/$home}"); done
  exec "${args[@]}"
elif [ "$1" = "--" ]; then
  shift
  # the real wsl.exe's re-parse: the script is wrapped in double quotes for a second shell, which expands $d first
  rest="${*:3}"; rest="${rest//\/home\/openclaw/$home}"
  exec sh -c "$1 $2 \"$rest\""
fi
exit 9
"""
FAKE_OPENCLAW = "#!/usr/bin/env bash\necho \"Hooks (1/1 ready)\"; echo \"hub-ask ready\"\n"


class Install(unittest.TestCase):
    def run_apply(self, wsl_text=FAKE_WSL):
        d = tempfile.mkdtemp()
        bindir, home = Path(d, "bin"), Path(d, "home")
        bindir.mkdir()
        home.mkdir()
        for name, text in (("wsl.exe", wsl_text), ("openclaw", FAKE_OPENCLAW)):
            p = bindir / name
            p.write_text(text)
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_HOME=str(home))
        r = subprocess.run(["bash", str(SCRIPT), "apply"], capture_output=True, text=True, env=env, timeout=60)
        return r, home / ".openclaw/hooks/hub-ask"

    def test_apply_puts_both_files_in_the_hook_folder(self):
        r, dest = self.run_apply()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((dest / "handler.js").is_file() and (dest / "HOOK.md").is_file())
        self.assertIn("hub-ask", r.stdout)

    def test_the_fake_reproduces_the_bug_with_the_old_separator(self):
        old = SCRIPT.read_text().replace("-u openclaw -e", "-u openclaw --")
        with tempfile.TemporaryDirectory() as d:
            alt = Path(d, "install_old.sh")
            alt.write_text(old)
            before = globals()["SCRIPT"]
            globals()["SCRIPT"] = alt
            try:
                # copy the hook folder beside the altered script so $src resolves
                os.symlink(before.parent / "openclaw_ask_hook", Path(d, "openclaw_ask_hook"))
                r, dest = self.run_apply()
            finally:
                globals()["SCRIPT"] = before
        self.assertFalse((dest / "handler.js").exists(), "the old separator must lose the folder name")


if __name__ == "__main__":
    unittest.main()
