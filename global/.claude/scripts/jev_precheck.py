#!/usr/bin/env python3
"""Ask Jev (TypeSafe's decision model) whether a diff meets its acceptance criteria.

Shadow use in the director workflow: prints __JEV_P=<probability>__ and decides nothing.
  jev_precheck.py --acceptance FILE [--diff FILE] [--repo NAME]   (diff on stdin if --diff is omitted)
Key: ~/.config/typesafe/api-key (mode 600). Stdlib only. Never prints the key.
Privacy: the acceptance, the questions and the diff pass panel_gate in-process (secret scan, redaction,
never_send) and only the redacted text is sent. A repo whose class (classes.json) is `client`, or whose
gate is not `send`, prints __JEV_SKIPPED=class__ / __JEV_SKIPPED=gate-<gate>__ and sends nothing.
Usage: one line per run goes to ~/.local/share/ledger/panel.jsonl (expert "jev"), numbers only. TypeSafe
reports `usage.input_tokens` and `usage.output_tokens` on every answer; those are logged as-is, and
chars/4 (flagged `estimated`) stands in only if a response ever arrives without them."""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger_log  # noqa: E402
import panel_gate  # noqa: E402

API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEY_PATH = "~/.config/typesafe/api-key"   # tests point this at a temp file
MAX_DIFF_CHARS = 60_000   # keeps the request well under Jev's input limit


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None   # a 3xx is an error: the Authorization header must not follow it elsewhere


# No redirects, and no HTTP(S)_PROXY from the environment.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
urlopen = _OPENER.open   # tests replace this; nothing else here touches the network
SCANNER = None   # gate secret scanner; None = real gitleaks via Docker (tests replace this)
CLASSES_PATH = None   # per-repo class list; None = ~/.config/panel/classes.json


def read(path):
    with open(path) as f:
        return f.read()


def jev_usage(body, out):
    """-> (input_tokens, output_tokens, estimated) for one answered request."""
    u = out.get("usage") if isinstance(out, dict) else None
    if isinstance(u, dict) and all(type(u.get(k)) is int and u[k] >= 0 for k in ("input_tokens", "output_tokens")):
        return u["input_tokens"], u["output_tokens"], False
    return len(json.dumps(body)) // 4, len(json.dumps(out)) // 4, True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--acceptance", required=True)
    ap.add_argument("--diff")
    ap.add_argument("--question", help="ask this one yes/no question instead of the whole-criteria one")
    ap.add_argument("--questions-file", help="JSON list of narrow yes/no checks; asked in one request, one __JEV_Q<i>=p__ line each")
    ap.add_argument("--repo", default=None, metavar="NAME", help="repo name recorded in the usage log")
    ap.add_argument("--class", dest="cls", default="public", choices=("public", "own", "client"))
    ap.add_argument("--never-send", action="append", default=[], metavar="GLOB", nargs="+")
    ap.add_argument("--terms-file", default=panel_gate.DEFAULT_TERMS)
    ap.add_argument("--max-redactions", type=int, default=panel_gate.DEFAULT_MAX)
    a = ap.parse_args(argv)
    a.never_send = [g for group in a.never_send for g in group]
    cls = panel_gate.effective_class(a.repo, a.cls, CLASSES_PATH)
    if cls == "client":   # Jev is an external service: a client repo never reaches it
        print("__JEV_SKIPPED=class__")
        return 0
    key_path = os.path.expanduser(KEY_PATH)
    if not os.path.exists(key_path):
        print("__JEV_P=NO_KEY__")
        return 0
    key = read(key_path).strip()
    criteria = read(a.acceptance).strip()
    diff = read(a.diff) if a.diff else sys.stdin.read()
    questions = [str(q) for q in json.loads(read(a.questions_file))] if a.questions_file else []
    try:
        terms = panel_gate.load_terms(a.terms_file)
        missing = not panel_gate.terms_file_present(a.terms_file)
    except (OSError, UnicodeDecodeError):
        print("__JEV_SKIPPED=gate-skip__")
        return 0
    gate = panel_gate.run_gate("\n".join([criteria, a.question or "", *questions, diff]), cls, a.never_send,
                               terms, a.max_redactions, SCANNER, missing)
    if gate.gate != "send":
        print(f"__JEV_SKIPPED=gate-{gate.gate}__")
        return 0
    red = panel_gate.Redactor(terms)   # one redactor: the same value keeps one placeholder across the texts
    criteria, diff = red.redact(criteria), red.redact(diff)
    questions = [red.redact(q) for q in questions]
    a.question = red.redact(a.question) if a.question else a.question
    truncated = len(diff) > MAX_DIFF_CHARS
    state = f"ACCEPTANCE CRITERIA:\n{criteria}\n\nCHANGE (unified diff{', truncated' if truncated else ''}):\n{diff[:MAX_DIFF_CHARS]}"
    spent = {"in": 0, "out": 0, "ms": 0, "estimated": False, "calls": 0}   # counts only, never text

    def ask(instructions, crit=None):
        q = {"type": "noul", "instructions": instructions}
        if crit:
            q["criteria"] = crit
        body = {"state": state, "model": MODEL, "questions": {"q": q}}
        req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        t0 = time.monotonic()
        spent["calls"] += 1
        try:
            with urlopen(req, timeout=30) as r:
                out = json.load(r)
            tin, tout, est = jev_usage(body, out)
            spent["in"] += tin
            spent["out"] += tout
            spent["estimated"] = spent["estimated"] or est
            return f"{out['answers']['q']['noul']:.3f}"
        except urllib.error.HTTPError as e:
            return f"HTTP_{e.code}"
        except Exception as e:  # network, timeout
            return f"ERROR_{type(e).__name__}"
        finally:
            spent["ms"] += int((time.monotonic() - t0) * 1000)

    try:
        if a.questions_file:
            # One request per check: batching several questions blunts Jev (0.72 vs 0.32 on the same planted fault).
            ps = [ask(q) for q in questions]
            for i, p in enumerate(ps):
                print(f"__JEV_Q{i}={p}__")
            nums = [float(p) for p in ps if p[0].isdigit()]
            print(f"__JEV_MIN={min(nums):.3f}__" if nums else "__JEV_MIN=NONE__", "truncated" if truncated else "")
        else:
            print(f"__JEV_P={ask(a.question or 'Does the change satisfy every acceptance criterion exactly, with every value matching the criteria?', None if a.question else {'true': 'Every criterion is met and every stated value in the change matches the criteria.', 'false': 'At least one criterion is unmet, or a value in the change differs from the criteria.'})}__")
    finally:
        if spent["calls"]:   # Jev has no privacy gate and no class, so those two fields are null
            ledger_log.append(ledger_log.record(a.repo, "jev", MODEL, spent["in"], spent["out"], spent["ms"],
                                                None, None, estimated=spent["estimated"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
