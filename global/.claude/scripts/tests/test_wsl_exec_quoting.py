"""Guard for one class of bug: a wsl.exe call that carries $ text after the double dash.

With "wsl.exe ... -- cmd", wsl.exe joins the words into one line and runs it through the distro's default shell, so
$1, $(...) and ${...} inside an inline script are expanded before sh -c ever sees them (the installer for the root
check failed this way: the folder argument arrived empty). "wsl.exe ... -e cmd" runs the program directly with the
arguments as they are. So every wsl.exe call that hands an inline "sh -c" script must use -e, and no scheduled-task
Arguments line may put $ text after the double dash.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)

INLINE_AFTER_DASHES = [
    re.compile(r"""wsl(?:\.exe)?\b[^\n]*\s--\s+(?:ba)?sh\s+-c\b"""),          # shell text: wsl.exe ... -- sh -c
    re.compile(r"""wsl[^\n]*["']--["']\s*,\s*["'](?:ba)?sh["']\s*,\s*["']-c["']"""),  # python list: "--", "sh", "-c"
]


def source_files():
    for root, dirs, files in os.walk(SCRIPTS):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", ".git")]
        for name in files:
            if name.endswith((".sh", ".py", ".xml", ".js", ".ps1")):
                yield os.path.join(root, name)


class WslExecQuoting(unittest.TestCase):
    def test_no_inline_shell_script_after_double_dash(self):
        bad = []
        for path in source_files():
            with open(path, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if any(p.search(line) for p in INLINE_AFTER_DASHES):
                        bad.append("%s:%d" % (os.path.relpath(path, SCRIPTS), n))
        self.assertEqual(bad, [], "use wsl.exe -e (no shell) for an inline sh -c script: " + ", ".join(bad))

    def test_scheduled_task_arguments_have_no_dollar_text_after_double_dash(self):
        bad = []
        windows = os.path.join(SCRIPTS, "windows")
        for name in sorted(os.listdir(windows)):
            if not name.endswith(".xml"):
                continue
            with open(os.path.join(windows, name), encoding="utf-8") as fh:
                text = fh.read()
            for args in re.findall(r"<Arguments>(.*?)</Arguments>", text, re.S):
                if "wsl" in args and re.search(r"\s--\s", args) and re.search(r"\$|`", args):
                    bad.append(name)
        self.assertEqual(bad, [], "a $ after the double dash is expanded by the distro's shell: " + ", ".join(bad))

    def test_the_checker_catches_the_original_bug(self):
        old = "wsl.exe -d Debian -u root -- sh -c 'lib=$1; install -d \"$lib\"' sh /x"
        new = "wsl.exe -d Debian -u root -e sh -c 'lib=$1; install -d \"$lib\"' sh /x"
        py_old = '[ctx.wsl, "-d", G, "-u", "root", "--", "sh", "-c", SCRIPT]'
        self.assertTrue(any(p.search(old) for p in INLINE_AFTER_DASHES))
        self.assertTrue(any(p.search(py_old) for p in INLINE_AFTER_DASHES))
        self.assertFalse(any(p.search(new) for p in INLINE_AFTER_DASHES))


if __name__ == "__main__":
    unittest.main()
