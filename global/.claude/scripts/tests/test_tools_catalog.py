"""tools_catalog.py: the catalog reads each tool's own header; the matcher picks tools for a prompt and never fails."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools_catalog.py"


def make_tree(tmp, scripts=None, hooks=None):
    root = Path(tmp)
    s, h = root / "scripts", root / "hooks"
    s.mkdir()
    h.mkdir()
    for d, files in ((s, scripts or {}), (h, hooks or {})):
        for name, body in files.items():
            p = d / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
    return s, h, root / "TOOLS.md"


def run(s, h, cat, *args):
    # the default repo list is the real one; point it at a missing file unless the test gives its own
    base = [] if "--repos-list" in args else ["--repos-list", str(Path(s).parent / "no-repos.tsv")]
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--scripts-dir", str(s), "--hooks-dir", str(h),
                           "--hub-dir", str(Path(s).parent / "hub-scripts"), "--catalog", str(cat), *base, *args],
                          capture_output=True, text=True)


PY = '"""alpha.py: count the widgets in a folder. More detail here.\n\nUse when: the widget total looks wrong.\n"""\nprint(1)\n'
SH = "#!/usr/bin/env bash\n# beta.sh: restart the gizmo service safely. It waits first.\n# Usage: beta.sh [--now]\nset -e\n"
JS = "#!/usr/bin/env node\n// gamma.js: upload a clip to the share. Extra words.\n// Use when: a video must be shared.\nconsole.log(1)\n"
JS_BLOCK = "/**\n * delta.js: render the chart image.\n */\nconsole.log(1)\n"
NONE = "import os\nprint(os.getcwd())\n"


class HeaderTest(unittest.TestCase):
    def build(self, scripts, hooks=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s, h, cat = make_tree(tmp.name, scripts, hooks)
        r = run(s, h, cat, "build")
        self.assertEqual(r.returncode, 0, r.stderr)
        return s, h, cat, cat.read_text()

    def test_python_docstring(self):
        _, _, _, text = self.build({"alpha.py": PY})
        self.assertRegex(text, r"alpha\.py \| alpha\.py: count the widgets in a folder\. \| Use when: the widget total looks wrong\.")
        self.assertNotIn("More detail", text)

    def test_shell_comment_block_after_shebang(self):
        _, _, _, text = self.build({"beta.sh": SH})
        self.assertIn("beta.sh | beta.sh: restart the gizmo service safely. | Usage: beta.sh [--now]", text)

    def test_javascript_comment_line_and_block(self):
        _, _, _, text = self.build({"gamma.js": JS, "delta.js": JS_BLOCK})
        self.assertIn("gamma.js | gamma.js: upload a clip to the share. | Use when: a video must be shared.", text)
        self.assertIn("delta.js | delta.js: render the chart image.", text)

    def test_file_with_no_header_is_flagged(self):
        s, h, cat, text = self.build({"plain.py": NONE})
        self.assertIn("(no header)", text)
        r = run(s, h, cat, "build", "--check")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("plain.py", r.stdout)

    def test_excluded_files_stay_out(self):
        _, _, _, text = self.build({
            "alpha.py": PY, "alpha.py.bak-2026": PY, "old.bak.py": PY, "notes.md": "# x\n", "data.json": "{}",
            "rows.tsv": "a\tb\n", "words.txt": "x", "run.log": "x", ".hidden.py": PY,
            "tests/test_a.py": PY, "__pycache__/c.py": PY, "sub/inner.sh": SH, "q.prompt": "# hi there friend\n"})
        lines = [ln for ln in text.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(lines), 1)
        self.assertIn("alpha.py", lines[0])

    def test_hooks_folder_is_included(self):
        _, _, _, text = self.build({"alpha.py": PY}, {"guard.sh": SH.replace("beta", "guard")})
        self.assertIn("/hooks/guard.sh", text)

    def test_check_passes_when_all_have_headers_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, h, cat = make_tree(tmp, {"alpha.py": PY})
            r = run(s, h, cat, "build", "--check")
            self.assertEqual(r.returncode, 0)
            self.assertFalse(cat.exists())
            self.assertEqual(sorted(p.name for p in s.iterdir()), ["alpha.py"])

    def test_safety_tools_come_first_under_their_own_heading(self):
        files = {"aaa.py": PY, "zzz.py": PY.replace("alpha", "zzz"),
                 "redact_view.py": '"""redact_view.py: show a file with secrets hidden."""\n',
                 "ps_redacted.sh": "#!/bin/sh\n# ps_redacted.sh: ps with secrets hidden.\n"}
        _, _, _, text = self.build(files, {"secret-guard.py": '"""secret-guard.py: block secrets in commands."""\n'})
        safety_at = text.index("## Safety tools")
        rest_at = text.index("## All other tools")
        self.assertLess(safety_at, rest_at)
        top, rest = text[safety_at:rest_at], text[rest_at:]
        for name in ("redact_view.py", "ps_redacted.sh", "secret-guard.py"):
            self.assertIn(name, top)
            self.assertNotIn(name, rest)
        self.assertLess(top.index("redact_view.py"), top.index("ps_redacted.sh"))  # fixed order, not by path
        self.assertLess(rest.index("aaa.py"), rest.index("zzz.py"))
        self.assertIn("GENERATED", text.splitlines()[0])
        self.assertIn("tools_catalog.py build", text.splitlines()[0])

    def test_output_is_stable_and_leaves_no_temp_file(self):
        s, h, cat, first = self.build({"alpha.py": PY, "beta.sh": SH})
        run(s, h, cat, "build")
        self.assertEqual(cat.read_bytes(), first.encode())
        self.assertEqual(sorted(p.name for p in cat.parent.iterdir()), ["TOOLS.md", "hooks", "scripts"])


def names_catalog(count=80):
    """Filler tools, so word rarity looks like the real catalog."""
    return {f"filler{i}.py": f'"""filler{i}.py: tidy the drawer number {i}."""\n' for i in range(count)}


class MatchTest(unittest.TestCase):
    def match(self, files, prompt, *args, hooks=None, prompt_exists=True, catalog_exists=True):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s, h, cat = make_tree(tmp.name, files, hooks)
        if catalog_exists:
            self.assertEqual(run(s, h, cat, "build").returncode, 0)
        pf = Path(tmp.name) / "prompt.md"
        if prompt_exists:
            pf.write_text(prompt)
        r = run(s, h, cat, "match", str(pf), *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, s

    SAFE = {"redact_view.py": '"""redact_view.py: show a file with secrets hidden."""\n',
            "coop_scrubber.py": '"""coop_scrubber.py: swap names for placeholders."""\n'}

    def test_section_shape_and_closing_line(self):
        out, _ = self.match({**names_catalog(), **self.SAFE}, "nothing relevant, only umbrella marmalade")
        lines = out.splitlines()
        self.assertEqual(lines[0], "## Tools you already have for this job")
        self.assertIn("~/.claude/scripts/TOOLS.md", lines[-1])
        self.assertIn("new helper", lines[-1])

    def test_safety_tools_always_present_and_first(self):
        files = {**names_catalog(), **self.SAFE,
                 "supabase_policy_dump.py": '"""supabase_policy_dump.py: list row level policies for a supabase project."""\n'}
        out, _ = self.match(files, "Audit the supabase policy dump for the project")
        body = [ln for ln in out.splitlines() if ln.startswith("- ")]
        self.assertIn("redact_view.py", body[0])
        self.assertIn("coop_scrubber.py", body[1])
        self.assertIn("supabase_policy_dump.py", body[2])

    def test_safety_present_even_when_prompt_is_unrelated(self):
        out, _ = self.match({**names_catalog(), **self.SAFE}, "umbrella marmalade")
        self.assertEqual(len([ln for ln in out.splitlines() if ln.startswith("- ")]), 2)

    def test_minimum_score_keeps_unrelated_tool_out(self):
        files = {**names_catalog(), **self.SAFE, "tidy_cabinet.py": '"""tidy_cabinet.py: tidy the cabinet."""\n'}
        out, _ = self.match(files, "write a poem about trains and rivers")
        self.assertNotIn("tidy_cabinet.py", out)

    def test_name_word_beats_description_word(self):
        words = "supabase policy dump river stone cloud forest"
        files = {**names_catalog(), **self.SAFE,
                 "supabase_policy_dump.py": '"""supabase_policy_dump.py: do a thing."""\n',
                 "other_tool.py": f'"""other_tool.py: talks about {words}."""\n'}
        out, _ = self.match(files, words)
        body = [ln.split(" | ")[0] for ln in out.splitlines() if ln.startswith("- ")]
        self.assertTrue(body[2].endswith("supabase_policy_dump.py"), body)
        self.assertTrue(body[3].endswith("other_tool.py"), body)

    def test_one_stray_description_word_is_not_enough(self):
        files = {**names_catalog(), **self.SAFE, "other_tool.py": '"""other_tool.py: talks about supabase."""\n'}
        out, _ = self.match(files, "supabase")
        self.assertNotIn("other_tool.py", out)

    def test_cap_of_twelve_and_max_flag(self):
        files = {**names_catalog(), **self.SAFE}
        for i in range(20):
            files[f"widget_spin_gear_cog_wheel_axle_rod_{i}.py"] = f'"""widget_spin_gear_cog_wheel_axle_rod_{i}.py: spin it."""\n'
        prompt = "widget spin gear cog wheel axle rod"
        out, _ = self.match(files, prompt)
        extra = [ln for ln in out.splitlines() if ln.startswith("- ") and "widget_spin" in ln]
        self.assertEqual(len(extra), 12)
        out, _ = self.match(files, prompt, "--max", "3")
        extra = [ln for ln in out.splitlines() if ln.startswith("- ") and "widget_spin" in ln]
        self.assertEqual(len(extra), 3)
        self.assertEqual([ln.split(" | ")[0] for ln in extra], sorted(ln.split(" | ")[0] for ln in extra))  # ties by path

    GMAIL = {"gmail_drafts.py": '"""gmail_drafts.py: create and list Gmail drafts for the inbox."""\n'}

    def test_gmail_drafts_prompt_finds_the_gmail_tool(self):
        out, _ = self.match({**names_catalog(), **self.SAFE, **self.GMAIL},
                            "Write three Gmail drafts for the inbox replies and list the drafts")
        self.assertIn("gmail_drafts.py", out)

    def test_unrelated_long_prompt_gets_safety_tools_only(self):
        files = {**names_catalog(), **self.SAFE, **self.GMAIL}
        prompt = ("Please paint the garden fence green, then count the apples in the orchard, "
                  "feed the horses, mend the barn roof and tidy the drawer number three. " * 5)
        out, _ = self.match(files, prompt)
        body = [ln for ln in out.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(body), 2)
        self.assertIn("redact_view.py", body[0])
        self.assertIn("coop_scrubber.py", body[1])

    def test_hub_scripts_folder_is_a_third_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, h, cat = make_tree(tmp, self.SAFE)
            hub = Path(tmp) / "hub-scripts"
            hub.mkdir()
            (hub / "hub-ask.py").write_text('"""hub-ask.py: ask the hub graph a question."""\n')
            r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--scripts-dir", str(s), "--hooks-dir", str(h),
                                "--hub-dir", str(hub), "--catalog", str(cat), "build"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(str(hub / "hub-ask.py"), cat.read_text())

    def test_builds_in_memory_when_catalog_missing(self):
        files = {**names_catalog(), **self.SAFE, "gizmo_restart.sh": "#!/bin/sh\n# gizmo_restart.sh: restart the gizmo service.\n"}
        out, s = self.match(files, "restart the gizmo service", catalog_exists=False)
        self.assertIn("redact_view.py", out)
        self.assertIn("gizmo_restart.sh", out)
        self.assertFalse((s.parent / "TOOLS.md").exists())

    def test_never_fails_on_missing_prompt_file(self):
        out, _ = self.match({**names_catalog(), **self.SAFE}, "", prompt_exists=False)
        self.assertIn("redact_view.py", out)
        self.assertIn("TOOLS.md", out.splitlines()[-1])

    def test_never_fails_on_unreadable_catalog_and_bad_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, h, cat = make_tree(tmp, self.SAFE)
            cat.mkdir()  # a folder where the catalog should be
            pf = Path(tmp) / "p.md"
            pf.write_text("redact")
            r = run(s, h, cat, "match", str(pf))
            self.assertEqual(r.returncode, 0)
            self.assertIn("## Tools you already have for this job", r.stdout)
            self.assertIn("redact_view.py", r.stdout)
            r = subprocess.run([sys.executable, "-I", str(SCRIPT), "match", str(pf), "--max", "notanumber"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("## Tools you already have for this job", r.stdout)


class RepoToolsTest(unittest.TestCase):
    """Repos named in the repo list whose docs name an MCP server, tool or slash command get a catalog line."""

    def build(self, repos, extra_rows=()):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        s, h, cat = make_tree(root, {"alpha.py": PY})
        rd = root / "repos"
        rows = ["name\tremote\tdefault_branch\tlast_commit\tkind\tcurrent_branch"]
        for name, files in repos.items():
            for rel, body in files.items():
                f = rd / name / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(body)
            rows.append(f"{name}\tgit@example.invalid:x/{name}.git\tmain\t2026-01-01\trepo\tmain")
        rows += list(extra_rows)
        lst = root / "repos.tsv"
        lst.write_text("\n".join(rows) + "\n")
        r = run(s, h, cat, "--repos-dir", str(rd), "--repos-list", str(lst), "build")
        self.assertEqual(r.returncode, 0, r.stderr)
        return cat.read_text(), rd

    WIDGET = {"CLAUDE.md": "# Widget\n\nWidget clips from video playlists, sorted for the gizmo team.\n\n"
              "## MCP Server Quick Reference\n\n**Clip Fetcher:** Pull clips directly.\n\n"
              "Use mcp__clip_fetcher__get_timed_clip for timestamps.\n"}

    def test_repo_with_mcp_tool_name_gets_a_line(self):
        text, rd = self.build({"widget": self.WIDGET})
        self.assertIn("## Repo tools and MCP servers", text)
        line = next(ln for ln in text.splitlines() if ln.startswith(f"- {rd}/widget |"))
        self.assertIn("Widget clips from video playlists", line)
        self.assertIn("MCP server clip-fetcher (mcp__clip_fetcher__get_timed_clip)", line)
        self.assertIn("MCP: Clip Fetcher", line)
        self.assertIn("widget/CLAUDE.md first", line)

    def test_repo_with_nothing_to_list_is_left_out(self):
        text, rd = self.build({"plain": {"CLAUDE.md": "# Plain\n\nJust notes about a garden plan for the team.\n"}})
        self.assertNotIn(f"{rd}/plain", text)

    def test_repo_missing_on_disk_and_blank_rows_do_not_break_the_build(self):
        text, _ = self.build({"widget": self.WIDGET}, extra_rows=["ghost\tx\tmain\t2026\trepo\tmain", ""])
        self.assertNotIn("ghost", text)

    def test_mcp_json_lists_server_names_only_and_skips_playwright(self):
        body = '{"mcpServers": {"playwright": {"command": "SECRET_VALUE_ONE"}, "gizmo-link": {"command": "SECRET_VALUE_TWO"}}}'
        text, rd = self.build({"gizmo": {"README.md": "# Gizmo\n\nThe gizmo controller for the shop floor machines.\n", ".mcp.json": body}})
        line = next(ln for ln in text.splitlines() if ln.startswith(f"- {rd}/gizmo |"))
        self.assertIn("MCP server gizmo-link", line)
        self.assertNotIn("playwright", text)
        self.assertNotIn("SECRET_VALUE", text)

    def test_slash_commands_listed_but_shared_boilerplate_is_not(self):
        repos = {n: {"CLAUDE.md": "# R\n\nA repository for the " + n + " project work.\n", ".claude/commands/analyze.md": "x",
                     ".claude/commands/speckit.plan.md": "x"} for n in ("one", "two", "three")}
        repos["one"][".claude/commands/clip-sorter.md"] = "x"
        text, rd = self.build(repos)
        line = next(ln for ln in text.splitlines() if ln.startswith(f"- {rd}/one |"))
        self.assertIn("/clip-sorter", line)
        self.assertNotIn("/analyze", text)
        self.assertNotIn("speckit", text)
        self.assertNotIn(f"{rd}/two", text)

    def test_repo_lines_reach_the_lane_matcher(self):
        text, rd = self.build({"widget": self.WIDGET})
        with tempfile.TemporaryDirectory() as t:
            cat, pf = Path(t) / "TOOLS.md", Path(t) / "p.md"
            filler = "".join(f"- /x/tool{i}.py | Tool number {i} does thing{i} for area{i}.\n" for i in range(200))
            cat.write_text(text + filler)
            pf.write_text("Fetch the timed clip transcript for the widget playlist video clips")
            r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--catalog", str(cat), "match", str(pf)],
                               capture_output=True, text=True)
        self.assertIn(f"- {rd}/widget |", r.stdout)


if __name__ == "__main__":
    unittest.main()
