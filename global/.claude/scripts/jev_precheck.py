#!/usr/bin/env python3
"""Ask Jev (TypeSafe's decision model) whether a diff meets its acceptance criteria.

Shadow use in the director workflow: prints __JEV_P=<probability>__ and decides nothing.
  jev_precheck.py --acceptance FILE [--diff FILE]   (diff on stdin if --diff is omitted)
Key: ~/.config/typesafe/api-key (mode 600). Stdlib only. Never prints the key."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.typesafe.ai/v1/systemone"
MAX_DIFF_CHARS = 60_000   # keeps the request well under Jev's input limit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--acceptance", required=True)
    ap.add_argument("--diff")
    ap.add_argument("--question", help="ask this one yes/no question instead of the whole-criteria one")
    ap.add_argument("--questions-file", help="JSON list of narrow yes/no checks; asked in one request, one __JEV_Q<i>=p__ line each")
    a = ap.parse_args()
    key_path = os.path.expanduser("~/.config/typesafe/api-key")
    if not os.path.exists(key_path):
        print("__JEV_P=NO_KEY__")
        return 0
    key = open(key_path).read().strip()
    criteria = open(a.acceptance).read().strip()
    diff = open(a.diff).read() if a.diff else sys.stdin.read()
    truncated = len(diff) > MAX_DIFF_CHARS
    state = f"ACCEPTANCE CRITERIA:\n{criteria}\n\nCHANGE (unified diff{', truncated' if truncated else ''}):\n{diff[:MAX_DIFF_CHARS]}"
    def ask(instructions, crit=None):
        q = {"type": "noul", "instructions": instructions}
        if crit:
            q["criteria"] = crit
        body = {"state": state, "model": "jev-latest", "questions": {"q": q}}
        req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.load(r)
            return f"{out['answers']['q']['noul']:.3f}"
        except urllib.error.HTTPError as e:
            return f"HTTP_{e.code}"
        except Exception as e:  # network, timeout
            return f"ERROR_{type(e).__name__}"

    if a.questions_file:
        # One request per check: batching several questions blunts Jev (0.72 vs 0.32 on the same planted fault).
        ps = [ask(q) for q in json.load(open(a.questions_file))]
        for i, p in enumerate(ps):
            print(f"__JEV_Q{i}={p}__")
        nums = [float(p) for p in ps if p[0].isdigit()]
        print(f"__JEV_MIN={min(nums):.3f}__" if nums else "__JEV_MIN=NONE__", "truncated" if truncated else "")
    else:
        print(f"__JEV_P={ask(a.question or 'Does the change satisfy every acceptance criterion exactly, with every value matching the criteria?', None if a.question else {'true': 'Every criterion is met and every stated value in the change matches the criteria.', 'false': 'At least one criterion is unmet, or a value in the change differs from the criteria.'})}__")
    return 0

if __name__ == "__main__":
    sys.exit(main())
