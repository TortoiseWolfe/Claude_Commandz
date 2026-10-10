#!/usr/bin/env python3
"""Decide whether the unattended Muse poller may send Hatch an answer it isn't sure of.

The poller (muse_poll.sh) answers a Hatch note itself when it is confident, and queues it for the ~/repos
terminal when it needs work or Jonathan's say. In between, an "iffy" answer (a fact inferred rather than
read, a question with two readings, an opinion) comes here first. Jonathan, 2026-10-10: "if it's
confident of a simple note that's fine, can run it through JEV if iffy?"

  ~/.local/state/muse-jev/request.json, written by the poller with its Write tool:
  {"note": "<the note>", "asked": "<its question, verbatim>", "reply": "<the draft>",
   "answer": "<the sentence that answers, verbatim>", "sources": ["<absolute path of each file the facts came from>"]}
  python3 muse_jev.py request      (reads that file, then deletes it; by hand, pipe the JSON to stdin instead)

Why a file: headless Claude Code denies a heredoc even when the command's prefix is allowed, and a model
writing the JSON as a quoted argument turned an escaped apostrophe back into a real one, which broke the
quoting (both found 2026-10-10). The Write tool takes any text, and only this one path is allowed to it.

Prints one line: __MUSE_JEV=send min=<p>__ or __MUSE_JEV=queue <reason>__. Fails closed: anything short of
both Jev answers at THRESHOLD or above is queue. The checks that need no model run first and send nothing:
every cited file must exist and be named in the reply, and a note naming anything on the private-terms list
never leaves the machine. Sourcing is checked here, not by Jev: it can't open the files, and on the first live
run it scored a correctly sourced reply 0.59 on "does every fact name its source".
Jev separates good from bad only on narrow checks that quote the exact text (0.99 vs 0.03 quoted, 0.72 vs
0.70 whole-criteria; memory project_director_tiering.md), so each question quotes it. Injected text can
nudge Jev, which is why it only ever lets an iffy answer through on top of the poller's own rules and never
decides a note that needs work or approval. One numbers-only line per run goes to muse-poll.log."""

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_precheck  # noqa: E402
import panel_gate  # noqa: E402

THRESHOLD = 0.90
# ~/.config/panel/classes.json lists this as `own`. A name it doesn't list is client, and Jev is skipped.
REPO = "muse-notes"
LOG = "~/.claude/state/muse-poll.log"   # tests point these at temp files
REQUEST = "~/.local/state/muse-jev/request.json"   # outside ~/.claude, which Claude Code won't let a tool write
TERMS_PATH = panel_gate.DEFAULT_TERMS
FIELDS = ("note", "asked", "reply", "answer")
# The handler reads only these (muse_poll.sh ALLOW: Read, Grep, Glob), so a cited file outside them is invented.
ROOTS = ("~/repos", "~/.claude")
# agent-notes content rule: Hatch reads urgency from Claude as Jonathan's directive (2026-10-02).
URGENT_RE = re.compile(r"\b(?:urgent(?:ly)?|asap|right now|immediately)\b", re.IGNORECASE)
ACCEPTANCE = """A reply from Claude Code to a note from Hatch (Jonathan's personal agent) may go out only if:
- it directly answers the question the note asks;
- it agrees to, schedules or promises nothing on Jonathan's behalf, and asks Hatch to send, post, spend, delete or contact no one."""


def flat(s):
    return " ".join(s.split())


def questions(d):
    return [
        f'Hatch\'s note asks: "{d["asked"]}". Claude\'s reply says: "{d["answer"]}". '
        "Does that reply text directly answer the question asked?",
        f'Claude\'s reply: "{d["reply"]}". Does it avoid agreeing to, scheduling or promising anything on '
        "Jonathan's behalf, and avoid asking Hatch to send, post, spend, delete or contact anyone?",
    ]


def source_problem(reply, sources):
    """-> a queue reason, or None when every cited file exists under ROOTS and is named in the reply."""
    if not isinstance(sources, list) or not sources or not all(isinstance(x, str) and x.strip() for x in sources):
        return "missing-sources"
    roots = [os.path.realpath(os.path.expanduser(r)) for r in ROOTS]
    for src in sources:
        path = os.path.realpath(os.path.expanduser(src.strip()))
        if not any(path == r or path.startswith(r + os.sep) for r in roots) or not os.path.exists(path):
            return "source-not-found"
        if os.path.basename(path) not in reply:
            return "source-not-named"
    return None


def precheck(d):
    """-> a queue reason, or None when the answer may go to Jev."""
    if not isinstance(d, dict):
        return "bad-input"
    for k in FIELDS:
        if not isinstance(d.get(k), str) or not d[k].strip():
            return f"missing-{k}"
    if flat(d["asked"]) not in flat(d["note"]):
        return "asked-not-in-note"
    if flat(d["answer"]) not in flat(d["reply"]):
        return "answer-not-in-reply"
    problem = source_problem(d["reply"], d.get("sources"))
    if problem:
        return problem
    if URGENT_RE.search(d["reply"]):
        return "urgency-word"
    if not panel_gate.terms_file_present(TERMS_PATH):
        return "no-terms"   # without the list nothing can catch a client name
    red = panel_gate.Redactor(panel_gate.load_terms(TERMS_PATH))
    red.redact(d["note"] + "\n" + d["reply"])
    if red.counts["TERM"]:
        return "private-term"
    return None


def ask_jev(d):
    """-> (decision line, the per-question answers as printed)."""
    with tempfile.TemporaryDirectory() as tmp:   # 0700; gone when the call returns
        paths = {}
        for name, text in (("acceptance.txt", ACCEPTANCE), ("questions.json", json.dumps(questions(d))),
                           ("note.txt", f"HATCH'S NOTE:\n{d['note']}\n\nCLAUDE'S REPLY:\n{d['reply']}\n")):
            paths[name] = os.path.join(tmp, name)
            with open(paths[name], "w", encoding="utf-8") as f:
                f.write(text)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            jev_precheck.main(["--acceptance", paths["acceptance.txt"], "--diff", paths["note.txt"],
                               "--questions-file", paths["questions.json"], "--class", "own",
                               "--repo", REPO, "--terms-file", TERMS_PATH])
    out = buf.getvalue()
    skipped = re.search(r"__JEV_SKIPPED=(\S+?)__", out)
    if skipped:
        return f"queue skipped-{skipped.group(1)}", []
    if "__JEV_P=NO_KEY__" in out:
        return "queue no-key", []
    ps = [p for _, p in sorted(re.findall(r"__JEV_Q(\d+)=(\S+?)__", out), key=lambda m: int(m[0]))]
    if len(ps) != len(questions(d)) or not all(re.fullmatch(r"\d+(?:\.\d+)?", p) for p in ps):
        return "queue error", ps
    low = min(float(p) for p in ps)
    return (f"send min={low:.3f}" if low >= THRESHOLD else f"queue low min={low:.3f}"), ps


def log(decision, ps):
    try:
        with open(os.path.expanduser(LOG), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%F %T')} muse_jev: {decision}{' q=' + ','.join(ps) if ps else ''}\n")
    except OSError:
        pass   # the decision still prints; a log failure never turns a queue into a send


def read_request():
    """The poller's request file, deleted once read so no note text stays on disk."""
    path = os.path.expanduser(REQUEST)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        d = json.loads(read_request() if argv[:1] == ["request"] else sys.stdin.read())
    except (OSError, ValueError, UnicodeDecodeError):
        d = None
    reason = precheck(d)
    decision, ps = (f"queue {reason}", []) if reason else ask_jev(d)
    log(decision, ps)
    print(f"__MUSE_JEV={decision}__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
