#!/usr/bin/env python3
"""Mutation probe for openclaw_status_digest.py: break one safety rule at a time in a temp copy, run the
test file against it, and count how many tests go red. A mutant that leaves the suite green is a gap.

  python3 mutate_openclaw_status_digest.py
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "openclaw_status_digest.py"
TESTS = HERE / "test_openclaw_status_digest.py"

MUTANTS = [
    ("email scrub off", 'EMAIL.sub("[email]", t)', "t"),
    ("gmail/draft id scrub off", "for rx in IDS:\n            t = rx.sub(\"[id]\", t)", "for rx in []:\n            pass"),
    ("phone scrub off", 'PHONE.sub("[phone]", t)', "t"),
    ("long number scrub off", 'LONG_NUM.sub("[num]", t)', "t"),
    ("dollar scrub off", 'MONEY.sub("[amount]", t)', "t"),
    ("money words no longer drop", "or MONEY_WORDS.search(t)", "or False"),
    ("street address no longer drops", "any(a.search(t) for a in ADDRESS)", "False"),
    ("double quotes no longer drop", "if QUOTE_CHARS.search(t) or SINGLE_QUOTED.search(t):", "if False:"),
    ("single-quoted text no longer drops", "or SINGLE_QUOTED.search(t)", ""),
    ("mail header lines pass", "if MAIL_MARKERS.match(t) or", "if"),
    ("name masking off", "t = rx.sub(\"[person]\", t)", "pass"),
    ("names not loaded from people files", "names.add(w.lower())\n            m = re.match(r\"\\s*(?:-\\s*)?name:", "pass\n            m = re.match(r\"\\s*(?:-\\s*)?name:"),
    ("residual check off", "or any(r.search(t) for r in RESIDUAL)", ""),
    ("character allowlist off", "if not ALLOWED.match(t)", "if False"),
    ("marker injection allowed", '"OCSTATUS" in t.upper() or "openclaw-status:" in t', "False"),
    ("cap ignored", "while len(s) > cap:", "while False:"),
    ("old reports included", "if now - mt > timedelta(days=days):", "if False:"),
    ("mail lanes' report text passes", "None if MAIL_LANE.search(name) else report_gist(path, flt)", "report_gist(path, flt)"),
    ("nested items counted as open", 'im = re.match(r"- \\[ \\] (.*)", lines[i])', 'im = re.match(r"\\s*- \\[ \\] (.*)", lines[i])'),
    ("noise sites listed", "and not NOISE_SITE.match(s.name)", ""),
    ("dead lanes not counted", "dead += 1", "pass"),
    ("struck-through items kept", ' and "~~" not in s', ""),
]


def main():
    red = 0
    rows = []
    for name, old, new in MUTANTS:
        src = SRC.read_text()
        if old not in src:
            rows.append((name, "PATTERN NOT FOUND", "-"))
            continue
        d = Path(tempfile.mkdtemp())
        (d / "tests").mkdir()
        (d / "openclaw_status_digest.py").write_text(src.replace(old, new, 1))
        shutil.copy(TESTS, d / "tests" / TESTS.name)
        r = subprocess.run([sys.executable, "-m", "unittest", "test_openclaw_status_digest"], cwd=d / "tests",
                           capture_output=True, text=True)
        m = re.search(r"FAILED \((.*)\)", r.stderr)
        if r.returncode == 0:
            rows.append((name, "SURVIVED (suite green)", "0"))
        else:
            n = sum(int(x) for x in re.findall(r"=(\d+)", m.group(1))) if m else "?"
            rows.append((name, "killed", str(n)))
            red += 1
        shutil.rmtree(d, ignore_errors=True)
    for name, verdict, n in rows:
        print(f"{verdict:24} red={n:>3}  {name}")
    print(f"\n{red} of {len(MUTANTS)} mutants killed")
    return 0 if red == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
