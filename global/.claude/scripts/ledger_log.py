"""Append-only usage log shared by panel_review.py and jev_precheck.py.

One JSON line per expert per run in ~/.local/share/ledger/panel.jsonl, for the usage ledger to ingest.
`record()` accepts numbers and short names only. It has no parameter that could carry a prompt or a
reply, so text cannot reach the log by accident. Stdlib only."""

import json
import os
import threading
from datetime import datetime, timezone

DEFAULT_PATH = "~/.local/share/ledger/panel.jsonl"   # read at call time so tests can point it elsewhere
FIELDS = ("ts", "repo", "expert", "model", "input_tokens", "output_tokens", "ms", "class", "gate")
OPTIONAL = ("estimated", "neurons")   # present only when true / reported
_APPEND_LOCK = threading.Lock()   # the panel asks its experts on threads; one line per write, never interleaved


def utc_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count(n):
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0


def record(repo, expert, model, input_tokens, output_tokens, ms, cls, gate, estimated=False, ts=None,
           neurons=None):
    """The exact ledger schema. `estimated` adds `"estimated": true` and is omitted when false.
    `neurons` (Cloudflare Workers AI meters in them; the free budget is 10,000 a day) adds a
    `"neurons": <float>` field and is omitted when the provider reported none."""
    rec = {"ts": ts or utc_iso(), "repo": (str(repo)[:200] if repo else None),
           "expert": str(expert), "model": str(model),
           "input_tokens": _count(input_tokens), "output_tokens": _count(output_tokens),
           "ms": _count(ms), "class": cls, "gate": gate}
    if estimated:
        rec["estimated"] = True
    if isinstance(neurons, (int, float)) and not isinstance(neurons, bool) and neurons >= 0:
        rec["neurons"] = round(float(neurons), 4)
    return rec


def append(rec, path=None):
    """Append one line; the directory is created mode 700 and the file mode 600. Never raises:
    bookkeeping must not be a reason for a review to fail. -> True if the line was written.

    Safe to call from several threads at once: the whole line goes out in ONE locked os.write on an
    O_APPEND descriptor, so concurrent lines can never interleave."""
    try:
        p = os.path.expanduser(path or DEFAULT_PATH)
        line = (json.dumps(rec, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
        with _APPEND_LOCK:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
        return True
    except OSError:
        return False
