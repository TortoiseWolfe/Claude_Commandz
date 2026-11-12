"""lane_rules_compose.py: a lane gets core plus the parts its name, folder, prompt or connectors might need."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "lane_rules_compose.py"
PARTS = ["core", "mail", "browser", "streams", "coop", "fleet"]


def rules_dir(tmp):
    d = Path(tmp) / "lane-rules"
    d.mkdir(exist_ok=True)
    for p in PARTS:
        (d / f"{p}.md").write_text(f"## {p}\n\n- rule text of {p}\n")
    (d / "key-rules-lane.txt").write_text("## Three rules\n\n- keep them in view\n")
    return d


ROUTES = """# Routes

## Cloudflare dashboard (Harmonic Egg, example.org)

- 2026-01-01 | a visible window passed the check | WORKED | note

## Portainer production (example co-op)

- 2026-01-02 | the API with a cached token | WORKED | note

## GitHub (orgs and accounts)

- 2026-01-03 | gh with the CLI token | WORKED | note
"""


def run(prompt, connectors="off", folder="/home/u/repos/ScriptHammer", missing_prompt=False, routes=None, keywords=None):
    with tempfile.TemporaryDirectory() as tmp:
        d = rules_dir(tmp)
        rf = Path(tmp) / "routes.md"
        if routes is not None:
            rf.write_text(routes)
        kf = Path(tmp) / "keywords.json"  # never the live private file: a missing one adds no words
        if keywords is not None:
            kf.write_text(keywords)
        # a missing routes file means no routes section, never the live one
        env = {**os.environ, "HUB_ROUTES_FILE": str(rf), "LANE_RULES_KEYWORDS": str(kf)}
        pf = Path(tmp) / ("nope.md" if missing_prompt else "prompt.md")
        if not missing_prompt:
            pf.write_text(prompt)
        out = Path(tmp) / "rules.md"
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--name", "x", "--dir", folder, "--connectors", connectors,
                            "--prompt", str(pf), "--out", str(out), "--rules-dir", str(d)], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip().split(","), out.read_text()


class ComposeTest(unittest.TestCase):
    def test_plain_code_lane_gets_core_only(self):
        parts, text = run("Fix the failing parser test and open a pull request.")
        self.assertEqual(parts, ["core"])
        self.assertIn("rule text of core", text)
        self.assertNotIn("rule text of mail", text)
        self.assertIn("not loaded: mail, browser, streams, coop, fleet", text.splitlines()[0])
        self.assertLess(text.index("keep them in view"), text.index("rule text of core"))

    def test_connectors_on_adds_mail(self):
        self.assertEqual(run("Fix a bug.", connectors="on")[0], ["core", "mail"])

    def test_each_trigger_adds_its_part(self):
        cases = {"Draft a reply in Gmail": "mail", "Fill the Lever job form in the browser": "browser",
                 "Check the Twitch stream and the band songs": "streams", "Update the Nextcloud co-op wiki": "coop",
                 "Edit lane.sh and the dispatcher": "fleet"}
        for prompt, part in cases.items():
            parts, text = run(prompt)
            self.assertIn(part, parts, prompt)
            self.assertIn(f"rule text of {part}", text)

    def test_workspace_folder_gets_fleet(self):
        self.assertIn("fleet", run("Count the repos.", folder="/home/u/repos")[0])

    def test_missing_prompt_gets_every_part(self):
        self.assertEqual(run("", missing_prompt=True)[0], PARTS)

    def test_private_words_add_parts_and_the_public_list_holds_no_names(self):
        # Placeholder words stand in for the client names that live in ~/.config/afa/lane-rules-keywords.json.
        prompt = "Reply about the zebracorp account."
        self.assertEqual(run(prompt)[0], ["core"])
        parts, text = run(prompt, keywords='{"coop": ["zebracorp"], "mail": ["\\\\bquokka\\\\b"]}')
        self.assertIn("coop", parts)
        self.assertNotIn("mail", parts)
        self.assertIn("mail", run("The quokka rota.", keywords='{"mail": ["\\\\bquokka\\\\b"]}')[0])

    def test_a_bad_or_unknown_private_entry_is_skipped_not_fatal(self):
        for bad in ("not json", '["a"]', '{"nonsense": ["zebracorp"]}', '{"coop": ["(unclosed", 5]}', '{"coop": "zebracorp"}'):
            self.assertEqual(run("Reply about the zebracorp account.", keywords=bad)[0], ["core"], bad)

    def test_the_public_trigger_lists_name_no_client_or_person(self):
        src = SCRIPT.read_text()
        start, end = src.index("TRIGGERS = {"), src.index("WORKSPACE_DIRS")
        lists = src[start:end].lower()
        # Made-up names stand in for the real ones; the live private file (when this machine has one) is checked too,
        # so a real name pasted into the public lists fails here without being written into this test.
        words = {"client-a", "person-b"}
        live = Path(os.path.expanduser("~/.config/afa/lane-rules-keywords.json"))
        try:
            data = json.loads(live.read_text())
        except (OSError, ValueError):
            data = {}
        for entries in (data.values() if isinstance(data, dict) else ()):
            for entry in (entries if isinstance(entries, list) else ()):
                if isinstance(entry, str):
                    words.update(w for w in re.split(r"[^a-z0-9]+", re.sub(r"\\[a-z]", " ", entry.lower())) if len(w) >= 4)
        for word in sorted(words):
            self.assertNotIn(word, lists)

    def test_routes_for_a_site_the_prompt_names_ride_along(self):
        _, text = run("Add a DNS record in the Cloudflare dashboard.", routes=ROUTES)
        self.assertIn("## Routes that worked or failed before (hub/routes.md)", text)
        self.assertIn("a visible window passed the check", text)
        self.assertNotIn("the API with a cached token", text)
        self.assertNotIn("gh with the CLI token", text)

    def test_routes_match_on_a_name_in_the_section_heading_parentheses(self):
        _, text = run("Check the Harmonic Egg site.", routes=ROUTES)
        self.assertIn("a visible window passed the check", text)

    def test_no_routes_section_when_nothing_matches_or_there_is_no_file(self):
        for routes in (ROUTES, None):
            _, text = run("Fix the failing parser test.", routes=routes)
            self.assertNotIn("Routes that worked or failed before", text)
        _, text = run("Add a DNS record in the Cloudflare dashboard.", routes=None)
        self.assertNotIn("Routes that worked or failed before", text)

    def test_site_words_match_whole_words_only(self):
        _, text = run("Review the githubbing helper.", routes=ROUTES)
        self.assertNotIn("gh with the CLI token", text)
        _, text = run("Open a GitHub pull request.", routes=ROUTES)
        self.assertIn("gh with the CLI token", text)

    def test_all_flag_and_missing_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = rules_dir(tmp)
            out = Path(tmp) / "r.md"
            subprocess.run([sys.executable, "-I", str(SCRIPT), "--all", "--out", str(out), "--rules-dir", str(d)], check=True)
            t = out.read_text()
            self.assertTrue(all(f"rule text of {p}" in t for p in PARTS))
            self.assertNotIn("not loaded", t)
            (d / "core.md").unlink()
            r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--all", "--out", str(out), "--rules-dir", str(d)],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
