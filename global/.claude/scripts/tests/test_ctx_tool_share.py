"""ctx_tool_share.py on a small made-up transcript: one big old Bash result, one small recent one."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

import tmpdirs

SCRIPT = Path(__file__).resolve().parent.parent / "ctx_tool_share.py"


def line(role, blocks):
    return json.dumps({"message": {"role": role, "content": blocks}})


class CtxToolShare(unittest.TestCase):
    def transcript(self):
        rows = [line("user", "start")]
        for i in range(25):
            rows.append(line("assistant", [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": "x"}}]))
            size = 15000 if i == 0 else 100
            rows.append(line("user", [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "y" * size}]))
        p = Path(tmpdirs.fresh_dir(self)) / "s.jsonl"
        p.write_text("\n".join(rows) + "\n")
        return p

    def test_counts_old_output_and_the_cap(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--caps", "12000", str(self.transcript())], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sessions 1", r.stdout)
        bash = [ln for ln in r.stdout.splitlines() if ln.startswith("Bash")][0].split()
        self.assertEqual(bash[1], "25")  # calls
        self.assertEqual(bash[3], "1")  # results over 12,000 characters
        self.assertEqual(bash[4], "0.01")  # 15,000 - 2,000 preview = 13,000 characters kept out

    def test_old_share_counts_results_more_than_20_turns_back(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--old-turns", "20", str(self.transcript())], capture_output=True, text=True)
        self.assertIn("88% of all tool output", r.stdout)  # 15,300 of 17,400 characters sit in the first 4 results, more than 20 turns old at the end

    def test_no_tool_results_exits_1(self):
        p = Path(tmpdirs.fresh_dir(self)) / "e.jsonl"
        p.write_text(line("user", "hi") + "\n")
        r = subprocess.run([sys.executable, str(SCRIPT), str(p)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
