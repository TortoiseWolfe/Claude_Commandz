"""/extension-status section 5 (hygiene) must compute branch protection from a real call and tell the reader what to do
about an unprotected main, instead of leaving the warning to be made up at render time (issue 6).
The command is a prompt, so these tests read its text: the check is there, the remediation is there, nothing mutates."""

import re
import unittest
from pathlib import Path

CMD = Path(__file__).resolve().parents[2] / "commands" / "extension-status.md"


def hygiene_section():
    text = CMD.read_text()
    m = re.search(r"### \d+\. Maintenance hygiene.*?(?=\n### \d+\. )", text, re.S)
    assert m, "hygiene section not found"
    return m.group(0)


class BranchProtection(unittest.TestCase):
    def test_section_fetches_main_protection_state(self):
        self.assertIn("branches/main -q '.protected'", hygiene_section())

    def test_unprotected_main_is_flagged_with_a_remediation_command(self):
        s = hygiene_section()
        self.assertIn("main is unprotected", s)
        self.assertRegex(s, r"gh api -X PUT repos/<REPO>/branches/main/protection")
        # the remediation carries its own config: the file the issue pointed at does not exist in the extension repo
        self.assertNotIn("branch-protection-recommended.json", s)
        self.assertIn('"allow_force_pushes": false', s)
        self.assertIn('"allow_deletions": false', s)

    def test_protected_main_renders_a_check_line(self):
        self.assertIn("main is protected", hygiene_section())

    def test_required_checks_and_reviewers_are_looked_at_when_protected(self):
        s = hygiene_section()
        self.assertIn("required_status_checks", s)
        self.assertIn("required_pull_request_reviews", s)

    def test_the_command_never_applies_protection_itself(self):
        s = hygiene_section()
        # the PUT may appear only as text the user runs, never as a step in a bash block we execute
        for block in re.findall(r"```bash\n(.*?)```", s, re.S):
            self.assertNotIn("-X PUT", block)
        self.assertRegex(s, r"(?i)never auto-appl|do not apply|you run")

    def test_override_repo_without_admin_degrades_instead_of_failing(self):
        self.assertIn("(no protection access)", hygiene_section())


if __name__ == "__main__":
    unittest.main()
