"""Notes from the tower to Hatch use the subject "[CC>MUSE] NCC-74656: <topic>" (Hatch's watcher wakes only on [CC>MUSE]).

On 5 and 6 Nov 2026, 21 notes written as "[NCC-74656>MUSE]" sat unread. The old tag may still be READ (older notes carry it), so the pollers and
their tests name it; but nothing under global/.claude may tell a session to WRITE it. These tests fail if one does, and if the shared lane prompt
loses its rule.
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
ROOT = SCRIPTS.parent  # global/.claude
sys.path.insert(0, str(SCRIPTS))

WRONG = "[NCC-74656>MUSE]"
# Files that read the old tag on purpose (the pollers and sweep classify old notes by it). Every other file under global/.claude must not hold it.
READERS = {"scripts/muse_poll.handle.prompt", "scripts/notes_check.py"}
TEXT_SUFFIXES = {".md", ".prompt", ".py", ".sh", ".txt", ".json", ".js", ".cjs", ".tsv", ""}
# A mention that is an instruction to write it: a subject, a draft to create, a reply to send, with the tag right after.
WRITING = re.compile(r"(?:subject|titled|title|create_draft|draft-create|write|wrote|reply|send|sign)\W[^\n]{0,40}" + re.escape(WRONG), re.I)


def outgoing_uses(root):
    """Return 'relative/path:line' for each place under root (a global/.claude folder) that holds the wrong tag as an outgoing one."""
    root = Path(root)
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES or "tests" in path.relative_to(root).parts:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(lines, 1):
            if WRONG not in line:
                continue
            if rel not in READERS or WRITING.search(line):
                hits.append("%s:%d" % (rel, n))
    return hits


class WrongTowerTagTests(unittest.TestCase):
    def test_nothing_in_global_claude_tells_a_session_to_write_the_old_tower_tag(self):
        self.assertEqual(outgoing_uses(ROOT), [], "the tower writes to Hatch as '[CC>MUSE] NCC-74656: <topic>', never '%s'" % WRONG)

    def test_the_check_can_go_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "scripts").mkdir()
            (Path(tmp) / "scripts" / "peer_poll.handle.prompt").write_text('Reply to Hatch with the subject "%s <topic>".\n' % WRONG)
            (Path(tmp) / "scripts" / "muse_poll.handle.prompt").write_text('Subject for Hatch: "%s <topic>".\n' % WRONG)
            (Path(tmp) / "scripts" / "notes_check.py").write_text('# classify old notes: "%s"\n' % WRONG)
            self.assertEqual(outgoing_uses(tmp), ["scripts/muse_poll.handle.prompt:1", "scripts/peer_poll.handle.prompt:1"])

    def test_a_reader_may_name_the_old_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "scripts").mkdir()
            (Path(tmp) / "scripts" / "muse_poll.handle.prompt").write_text('older ones were "%s", so also search for them\n' % WRONG)
            self.assertEqual(outgoing_uses(tmp), [])

    def test_the_shared_lane_prompt_names_the_right_tag_for_notes_to_hatch(self):
        import openclaw_dispatch as dp
        prompt = dp.TEMPLATE.format(repo="r", dir="/x", task="t", report="/x/report.md", work_rule="w", extra_rules="")
        self.assertIn("[CC>MUSE] <machine>: <topic>", prompt)
        self.assertIn("never '[<machine>>MUSE]'", prompt)


if __name__ == "__main__":
    unittest.main()
