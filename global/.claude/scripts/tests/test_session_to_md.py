"""Invented JSONL fixtures for the session-to-Markdown exporter."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import session_to_md as session_log  # noqa: E402


def record(kind, timestamp, content, **extra):
    row = {
        "type": kind,
        "timestamp": timestamp,
        "sessionId": "fixture-session",
        "cwd": "/tmp/work/demo",
        "gitBranch": "fixture-branch",
        "message": {"role": "user" if kind == "user" else "assistant", "content": content},
    }
    row.update(extra)
    return row


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
    return path


class SessionToMarkdown(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project_dir = self.root / "projects" / "tmp-work-demo"
        self.source = self.project_dir / "fixture-session.jsonl"

    def fixture_records(self):
        return [
            record("user", "2025-01-02T13:17:00Z", "Second invented prompt"),
            record("assistant", "2025-01-02T13:17:10Z", [{"type": "text", "text": "Short answer"}]),
            record("user", "2025-01-02T13:15:00Z", "First invented prompt"),
            record("assistant", "2025-01-02T13:15:10Z", [{"type": "text", "text": "First answer"}]),
        ]

    def test_prompts_are_oldest_first_with_twelve_hour_times_and_session_header(self):
        source = write_jsonl(self.source, self.fixture_records())
        records = session_log.read_records(source)
        try:
            with mock.patch.dict(os.environ, {"TZ": "UTC"}):
                time.tzset()
                rendered = session_log.render_markdown(source, records)
        finally:
            time.tzset()
        self.assertLess(rendered.index("First invented prompt"), rendered.index("Second invented prompt"))
        self.assertIn("## 1:15 PM — Prompt 1", rendered)
        self.assertIn("## 1:17 PM — Prompt 2", rendered)
        self.assertIn("- Session: `fixture-session`", rendered)
        self.assertIn("- Folder: `demo`", rendered)
        self.assertIn("- Branch: `fixture-branch`", rendered)

    def test_tool_targets_are_one_line_and_replies_are_trimmed_without_tool_output(self):
        large_reply = "R" * 510
        source = write_jsonl(self.source, [
            record("user", "2025-01-02T13:15:00Z", "Make a fixture change"),
            record("assistant", "2025-01-02T13:15:01Z", [
                {"type": "tool_use", "id": "read-1", "name": "Read",
                 "input": {"file_path": "src/example.py"}},
                {"type": "tool_use", "id": "bash-1", "name": "Bash",
                 "input": {"command": "pytest -q tests\necho hidden"}},
                {"type": "text", "text": large_reply},
            ]),
            {"type": "user", "timestamp": "2025-01-02T13:15:02Z", "toolUseID": "read-1",
             "toolUseResult": {"content": "PRIVATE-TOOL-OUTPUT"},
             "message": {"role": "user", "content": "PRIVATE-TOOL-OUTPUT"}},
        ])
        rendered = session_log.render_markdown(source, session_log.read_records(source))
        self.assertIn("- Read — src/example.py", rendered)
        self.assertIn("- Bash — pytest -q tests", rendered)
        self.assertIn("(510 characters; showing 400)", rendered)
        self.assertIn("R" * 400, rendered)
        self.assertNotIn("R" * 401, rendered)
        self.assertNotIn("PRIVATE-TOOL-OUTPUT", rendered)
        self.assertNotIn("echo hidden", rendered)

    def test_subagent_is_linked_under_its_turn_and_optional_prompt_is_included(self):
        parent = write_jsonl(self.source, [
            record("user", "2025-01-02T13:15:00Z", "Inspect this made-up fixture"),
            record("assistant", "2025-01-02T13:15:10Z", [
                {"type": "tool_use", "id": "task-1", "name": "Task",
                 "input": {"description": "Check the sample", "prompt": "Review the invented file\nReturn a short result"}},
            ]),
            {"type": "user", "timestamp": "2025-01-02T13:18:00Z", "toolUseID": "task-1",
             "toolUseResult": {"agentId": "abc123"},
             "message": {"role": "user", "content": "Done"}},
        ])
        child = write_jsonl(self.project_dir / self.source.stem / "subagents" / "agent-abc123.jsonl", [
            {"type": "user", "timestamp": "2025-01-02T13:15:20Z", "agentId": "abc123",
             "message": {"role": "user", "content": "Review the invented file\nReturn a short result"}},
            {"type": "assistant", "timestamp": "2025-01-02T13:17:20Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Reviewed it."}]}},
        ])
        records = session_log.read_records(parent)
        default = session_log.render_markdown(parent, records)
        expanded = session_log.render_markdown(parent, records, with_subagents=True)
        self.assertIn("[Review the invented file](#subagent-task-1)", default)
        self.assertIn("2m", default)
        self.assertNotIn("Return a short result", default)
        expected_time = session_log.local_time(
            session_log.timestamp({"timestamp": "2025-01-02T13:15:20Z"})
        )
        self.assertIn(f"Prompt time: {expected_time}", expanded)
        self.assertIn("Review the invented file\nReturn a short result", expanded)
        self.assertIn("Reviewed it.", expanded)
        self.assertTrue(child.is_file())

    def test_system_reminders_hooks_and_attachments_are_skipped_unless_all_is_set(self):
        source = write_jsonl(self.source, [
            record("user", "2025-01-02T13:14:59Z",
                   "<system-reminder>Invented early reminder</system-reminder>"),
            record("user", "2025-01-02T13:15:00Z", "Visible prompt"),
            record("user", "2025-01-02T13:15:01Z",
                   "<system-reminder>Invented private reminder</system-reminder>"),
            record("user", "2025-01-02T13:15:02Z",
                   "Keep this part <system-reminder>and skip this part</system-reminder>"),
            {"type": "hook", "timestamp": "2025-01-02T13:15:02Z",
             "message": {"role": "user", "content": "Invented hook output " + "H" * 500}},
            record("user", "2025-01-02T13:15:03Z", [
                {"type": "text", "text": "Prompt with image"},
                {"type": "image", "source": {"type": "base64", "data": "not-real-image-data"}},
            ]),
        ])
        records = session_log.read_records(source)
        default = session_log.render_markdown(source, records)
        all_content = session_log.render_markdown(source, records, include_all=True)
        self.assertNotIn("Invented private reminder", default)
        self.assertNotIn("Invented early reminder", default)
        self.assertNotIn("Invented hook output", default)
        self.assertNotIn("Attachment: image", default)
        self.assertIn("Keep this part", default)
        self.assertNotIn("skip this part", default)
        self.assertIn("Invented private reminder", all_content)
        self.assertIn("Invented early reminder", all_content)
        self.assertIn("Invented hook output", all_content)
        self.assertIn("H" * 500, all_content)
        self.assertIn("[Attachment: image]", all_content)

    def test_latest_uses_newest_main_session_in_the_cwd_project_folder(self):
        with tempfile.TemporaryDirectory() as cwd:
            project = Path(cwd)
            slug = session_log.slugify(project.resolve())
            projects = self.root / "latest-projects"
            folder = projects / slug
            older = write_jsonl(folder / "older.jsonl", self.fixture_records())
            newer = write_jsonl(folder / "newer.jsonl", self.fixture_records())
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            write_jsonl(folder / "agent-sidecar.jsonl", self.fixture_records())
            with mock.patch.object(session_log, "PROJECTS_DIR", projects):
                self.assertEqual(session_log.resolve_latest(str(project)), newer.resolve())

    def test_session_id_resolves_only_to_a_matching_transcript(self):
        source = write_jsonl(self.source, self.fixture_records())
        with mock.patch.object(session_log, "PROJECTS_DIR", self.project_dir.parent):
            self.assertEqual(session_log.resolve_session("fixture-session"), source.resolve())
            with self.assertRaisesRegex(session_log.ExportError, "unsupported characters"):
                session_log.resolve_session("*")

    def test_scrub_failure_refuses_to_write_or_print_the_log(self):
        source = write_jsonl(self.source, self.fixture_records())
        output = self.root / "private-output.md"
        output.write_text("existing output stays intact", encoding="utf-8")
        failing_scrubber = self.root / "scrubber.py"
        failing_scrubber.write_text("import sys\nsys.exit(9)\n", encoding="utf-8")
        stdout = mock.Mock()
        with mock.patch.object(session_log, "SCRUBBER", failing_scrubber):
            with self.assertRaisesRegex(session_log.ExportError, "scrub failed"):
                session_log.write_scrubbed(
                    session_log.render_markdown(source, session_log.read_records(source)),
                    source, str(output), stdout=stdout,
                )
        self.assertEqual(output.read_text(encoding="utf-8"), "existing output stays intact")
        stdout.write.assert_not_called()

    def test_successful_scrub_runs_before_writing(self):
        source = write_jsonl(self.source, [
            record("user", "2025-01-02T13:15:00Z", "Fixture secret marker SAFE-DEMO-SECRET"),
        ])
        output = self.root / "clean.md"
        scrubber = self.root / "scrubber.py"
        scrubber.write_text(
            "import pathlib, sys\n"
            "p = pathlib.Path(sys.argv[-1])\n"
            "p.write_text(p.read_text().replace('SAFE-DEMO-SECRET', '[scrubbed]'))\n",
            encoding="utf-8",
        )
        text = session_log.render_markdown(source, session_log.read_records(source))
        with mock.patch.object(session_log, "SCRUBBER", scrubber):
            session_log.write_scrubbed(text, source, str(output))
        self.assertIn("[scrubbed]", output.read_text(encoding="utf-8"))
        self.assertNotIn("SAFE-DEMO-SECRET", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
