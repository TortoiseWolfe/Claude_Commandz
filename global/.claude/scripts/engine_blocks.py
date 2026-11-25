#!/usr/bin/env python3
"""The one machine-written record of which code engines are out of usage, read by the dispatcher and by lane.sh.

Usage: engine_blocks.py until ENGINE | phrase ENGINE | show | block ENGINE --until "D Mon YYYY H:MM AM" --evidence "..." --source TEXT [--note TEXT] | clear ENGINE --evidence "..." --source TEXT
Use when: ask whether an engine (agy, codex, copilot, opencode) is blocked and until when; record a usage-limit block; clear a block after a clean run.

File: $AFA_ENGINE_BLOCKS, else ~/.claude/state/engine-blocks.json: {"<engine>": {"until": epoch, "evidence": epoch, "source": text, "note": text}}.
Newest evidence wins: block() replaces a record unless the record's evidence is newer (the block may move earlier or later);
clear() removes a record only when its evidence is newer than the record's (a clean run that started after the block was recorded).
A missing, unreadable or malformed file reads as "no blocks"; a write refuses to overwrite a malformed file (exit 2). Written atomically, mode 600.
Dates and --evidence (default: now) are read as local time, the way date -d reads them. Exit 0 on success, including "nothing to do".
Standard library only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NAMES = {"agy": "agy", "codex": "Codex", "copilot": "Copilot", "opencode": "OpenCode"}


class Malformed(Exception):
    pass


def path():
    return Path(os.environ.get("AFA_ENGINE_BLOCKS") or Path.home() / ".claude" / "state" / "engine-blocks.json")


def _load(strict=False):
    """-> dict of records. A missing file is {}; an unreadable or malformed one is {} (strict: raises Malformed)."""
    p = path()
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        if strict:
            raise Malformed(str(p))
        return {}
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        if strict:
            raise Malformed(str(p))
        return {}
    return data


def _num(rec, key):
    v = rec.get(key, 0)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _save(data):
    p = path()
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def until(engine, now=None):
    """Epoch seconds the engine is blocked until; 0 when not blocked or no record."""
    now = time.time() if now is None else now
    u = _num(_load().get(engine, {}), "until")
    return u if u > now else 0


def block(engine, until_ts, evidence, source, note=""):
    """Record a block. -> True when written, False when a newer record stood. Raises Malformed on a bad file."""
    data = _load(strict=True)
    old = data.get(engine)
    if old is not None and _num(old, "evidence") > evidence:
        return False
    data[engine] = {"until": until_ts, "evidence": evidence, "source": source, "note": note}
    _save(data)
    return True


def clear(engine, evidence, source):
    """Remove the record when `evidence` is newer than the record's. -> True when removed. Raises Malformed on a bad file."""
    data = _load(strict=True)
    old = data.get(engine)
    if old is None or not evidence > _num(old, "evidence"):
        return False
    del data[engine]
    _save(data)
    return True


def _when(ts):
    t = time.localtime(ts)
    h = t.tm_hour % 12 or 12
    return "%d %s %d %d:%02d %s" % (t.tm_mday, MONTHS[t.tm_mon - 1], t.tm_year, h, t.tm_min, "AM" if t.tm_hour < 12 else "PM")


def phrase(engine, now=None):
    u = until(engine, now)
    if not u:
        return ""
    return "until %s returns on %s" % (NAMES.get(engine, engine), _when(u))


def show(now=None):
    now = time.time() if now is None else now
    lines = []
    for e, rec in sorted(_load().items()):
        u = _num(rec, "until")
        if u > now:
            lines.append("%s is blocked until %s (%s)" % (NAMES.get(e, e), _when(u), rec.get("source", "")))
        else:
            lines.append("%s is not blocked" % NAMES.get(e, e))
    return "\n".join(lines) if lines else "no engine is blocked"


def parse_date(text):
    """Local time, as date -d reads it. -> epoch seconds or None."""
    try:
        r = subprocess.run(["date", "-d", text, "+%s"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().lstrip("-").isdigit() else None


def _opts(argv, names):
    out, i = {}, 0
    while i < len(argv):
        if argv[i] in names and i + 1 < len(argv):
            out[argv[i]] = argv[i + 1]
            i += 2
        else:
            raise ValueError("unexpected argument: " + argv[i])
    return out


def main(argv):
    if len(argv) < 1:
        print(__doc__.split("\n")[2], file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "show" and not rest:
            print(show())
            return 0
        if cmd in ("until", "phrase") and len(rest) == 1:
            print(int(until(rest[0])) if cmd == "until" else phrase(rest[0]))
            return 0
        if cmd in ("block", "clear") and rest:
            engine = rest[0]
            o = _opts(rest[1:], ("--until", "--evidence", "--source", "--note") if cmd == "block" else ("--evidence", "--source"))
            if "--source" not in o or (cmd == "block" and "--until" not in o):
                raise ValueError("missing option")
            ev = parse_date(o["--evidence"]) if "--evidence" in o else int(time.time())
            if ev is None:
                raise ValueError("cannot read the evidence date: " + o["--evidence"])
            if cmd == "block":
                u = parse_date(o["--until"])
                if u is None:
                    raise ValueError("cannot read the until date: " + o["--until"])
                done = block(engine, u, ev, o["--source"], o.get("--note", ""))
                print(("recorded: " + phrase(engine, 0)) if done else "nothing to do: a record with newer evidence stands")
            else:
                done = clear(engine, ev, o["--source"])
                print("cleared" if done else "nothing to do: no record, or its evidence is not older")
            return 0
    except Malformed as e:
        print("refusing to overwrite a malformed file: %s" % e, file=sys.stderr)
        return 2
    except ValueError as e:
        print("engine_blocks.py: %s" % e, file=sys.stderr)
        return 2
    print("engine_blocks.py: unknown or incomplete command: " + cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
