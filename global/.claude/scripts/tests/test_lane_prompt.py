"""lane_prompt.py: fills a template once, refuses typos, missing values and overwrites, and never fills a value twice.

Every template here is invented text in a throwaway folder."""

import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
from tmpdirs import fresh_dir  # noqa: E402

TOOL = str(SCRIPTS / "lane_prompt.py")
TEMPLATE = "Lane for {{client}} in {{lane_dir}} on {{date}}.\nIds: {{ids}}\nAgain {{client}}.\n"


class LanePrompt(unittest.TestCase):
    def setUp(self):
        self.d = Path(fresh_dir(self))
        (self.d / "absorb-template.md").write_text(TEMPLATE)
        (self.d / "plain-template.md").write_text("no slots here\n")

    def run_tool(self, *args):
        return subprocess.run([sys.executable, "-I", TOOL, "--lanes-dir", str(self.d), *args], capture_output=True, text=True)

    def fill(self, *extra, slug="acme-mail"):
        return self.run_tool("fill", "absorb", "--slug", slug, "--date", "2026-11-07", "--var", "client=Acme", "--var", "ids=a1 b2", *extra)

    def test_fill_writes_prompt_md_and_prints_the_folder(self):
        r = self.fill()
        folder = self.d / "2026-11-07-acme-mail"
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(folder)))
        self.assertEqual((folder / "prompt.md").read_text(),
                         "Lane for Acme in %s on 2026-11-07.\nIds: a1 b2\nAgain Acme.\n" % folder)

    def test_a_slot_with_no_value_is_refused_and_nothing_is_written(self):
        r = self.run_tool("fill", "absorb", "--slug", "acme-mail", "--var", "client=Acme")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no value for: ids", r.stderr)
        self.assertEqual([p.name for p in self.d.iterdir() if p.is_dir()], [])

    def test_a_var_the_template_has_no_slot_for_is_refused(self):
        r = self.fill("--var", "idz=typo")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no slot for: idz", r.stderr)

    def test_an_existing_prompt_is_never_overwritten(self):
        self.fill()
        folder = self.d / "2026-11-07-acme-mail"
        (folder / "prompt.md").write_text("edited by hand\n")
        r = self.fill()
        self.assertEqual(r.returncode, 2)
        self.assertEqual((folder / "prompt.md").read_text(), "edited by hand\n")

    def test_check_validates_and_writes_nothing(self):
        r = self.fill("--check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("would write", r.stdout)
        self.assertFalse((self.d / "2026-11-07-acme-mail").exists())

    def test_a_value_holding_a_slot_is_not_filled_again(self):
        r = self.run_tool("fill", "absorb", "--slug", "acme-mail", "--date", "2026-11-07", "--var", "client={{ids}}", "--var", "ids=real")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Lane for {{ids}} in", (self.d / "2026-11-07-acme-mail/prompt.md").read_text())

    def test_a_value_can_come_from_a_file(self):
        (self.d / "long.txt").write_text("line one\nline two\n")
        r = self.run_tool("fill", "absorb", "--slug", "acme-mail", "--date", "2026-11-07", "--var", "client=Acme", "--var", "ids=@%s" % (self.d / "long.txt"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("Ids: line one\nline two\n\n", (self.d / "2026-11-07-acme-mail/prompt.md").read_text())

    def test_the_date_defaults_to_today(self):
        r = self.run_tool("fill", "plain", "--slug", "no-slots")
        self.assertEqual(r.returncode, 0)
        self.assertRegex(Path(r.stdout.strip()).name, r"^\d{4}-\d{2}-\d{2}-no-slots$")

    def test_a_bad_slug_or_unknown_template_is_refused(self):
        self.assertEqual(self.run_tool("fill", "plain", "--slug", "../escape").returncode, 2)
        self.assertEqual(self.run_tool("fill", "plain", "--slug", "Has Space").returncode, 2)
        r = self.run_tool("fill", "nothing", "--slug", "ok-slug")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no template", r.stderr)

    def test_a_template_can_be_named_by_path(self):
        other = self.d / "elsewhere.md"
        other.write_text("only {{date}}\n")
        r = self.run_tool("fill", str(other), "--slug", "by-path", "--date", "2026-11-07")
        self.assertEqual(r.returncode, 0)
        self.assertEqual((self.d / "2026-11-07-by-path/prompt.md").read_text(), "only 2026-11-07\n")

    def test_list_shows_each_template_with_its_slots_but_not_the_automatic_ones(self):
        r = self.run_tool("list")
        self.assertIn("absorb: client, ids", r.stdout.splitlines())
        self.assertIn("plain: (no slots)", r.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
