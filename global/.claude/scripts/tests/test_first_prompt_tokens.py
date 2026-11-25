"""first_prompt_tokens.py: the first answer's input-side tokens, from made-up transcripts."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import first_prompt_tokens as fp  # noqa: E402


def write(path, rows):
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))


def answer(inp, create, read):
    return {"type": "assistant", "message": {"usage": {"input_tokens": inp, "cache_creation_input_tokens": create,
                                                         "cache_read_input_tokens": read, "output_tokens": 9}}}


class FirstPrompt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_sums_the_first_answer_only(self):
        p = Path(self.tmp) / "a.jsonl"
        write(p, [{"type": "user"}, answer(2, 1000, 500), answer(3, 1, 9000)])
        self.assertEqual(fp.first_prompt_tokens(p), 1502)

    def test_none_without_an_answer_or_file(self):
        p = Path(self.tmp) / "b.jsonl"
        write(p, [{"type": "user"}])
        self.assertIsNone(fp.first_prompt_tokens(p))
        self.assertIsNone(fp.first_prompt_tokens(Path(self.tmp) / "missing.jsonl"))

    def test_bad_lines_are_skipped(self):
        p = Path(self.tmp) / "c.jsonl"
        p.write_text("not json\n" + json.dumps(answer(1, 2, 3)) + "\n")
        self.assertEqual(fp.first_prompt_tokens(p), 6)

    def test_folder_takes_the_newest_n_with_an_answer(self):
        for i, n in enumerate((100, 200, 300)):
            p = Path(self.tmp) / f"s{i}.jsonl"
            write(p, [answer(n, 0, 0)])
            os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
        write(Path(self.tmp) / "empty.jsonl", [{"type": "user"}])
        os.utime(Path(self.tmp) / "empty.jsonl", (1_700_000_100, 1_700_000_100))
        rows = fp.folder_numbers(self.tmp, last=2)
        self.assertEqual([n for _, n in rows], [300, 200])


if __name__ == "__main__":
    unittest.main()
