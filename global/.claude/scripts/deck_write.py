#!/usr/bin/env python3
"""Edit cards on a Nextcloud Deck board without ever showing credentials.

    deck_write.py --env <env file> --prefix NEXTCLOUD_PROD --board <id> <action> ...

Actions:
    show     --card N                          print one card's title, stack, due date (Eastern) and full description
    replace  --card N --old TEXT --new TEXT    swap TEXT in the description (must match exactly once)
    append   --card N --text LINE              add a line to the end of the description
    move     --card N --stack "Stack title"    move the card to the end of another stack
    title    --card N --text "New title"       change the card's title
    due      --card N --date YYYY-MM-DDTHH:MM  set the due date (Eastern time; sent to the Deck as UTC)
    create   --stack "Stack title" --title T [--description D] [--assign UID ...]
    comment  --card N --text MESSAGE           add a comment after a strict chat voice check

Pacing (a person does not post a dozen comments in one second, and the co-op watches this board):
    - Every write records its time in ~/.claude/state/deck-last-write with a random wait of 6 to 18 minutes.
      A second write inside that wait is refused with exit code 75 and the time it is allowed.
    - deck_write.py queue [--env F] [--prefix P] <board> <action args>   store one write in deck-write-queue.jsonl
      (the exact text is kept; a comment is voice-checked now and again when posted).
    - deck_write.py drain [--dry-run] [--verbose]    post at most one due queued write, only 9:00 AM to 9:00 PM Eastern
      and only when the wait allows. muse_poll.sh calls it every minute; it prints nothing when there is nothing to do.
      A queued write is NOT posted: queue prints a line beginning "NOT POSTED YET" and exits 3. It posts when the drain
      reaches it. The drain marks an entry "posting" before it calls the server and never posts one entry twice: a write the
      server may have taken but that could not be read back is parked as "unproven" for a person to check.
    - deck_write.py log [--date YYYY-MM-DD] [--all]    the write log (deck-write-log.tsv: time, result, board, card, actions,
      queue id, reason; never any text) for one Eastern date, then "N queued, N posted, N failed, N unproven".
    - Exit codes: 0 done and read back; 3 queued only; 4 the server took the write, or may have, and the read-back failed or
      does not show it (check the card by hand, never retry); 75 wait (queue it). Exit 4 also covers a visit of two actions
      where the first one posted and the second failed, a connection lost after a write request left, and an HTTP 500, 502
      or 504 answer to a write. A failure before any write request left is an ordinary failure, and the drain retries it.
    - deck_write.py queue-status    how many are queued, when the next is due, and when the last write was.
    - A lone + between actions makes one visit of them, e.g. comment --card 9 --text T + move --card 9 --stack S.
      All must name the same card; it counts as one write for the wait.
    - --dry-run (before the action) checks everything and the wait, and makes no request and no record.
    DECK_STATE_DIR overrides ~/.claude/state (tests use it).

Credentials come from <PREFIX>_URL, <PREFIX>_USER and <PREFIX>_PASS in the env file, read inside
this process by deck_board.py's loader. They are never printed or put on a command line.
Uses the Deck REST API (index.php/apps/deck/api/v1.0); moving between stacks uses the web UI's
route, because the REST reorder call ignores the target stack. Every write reads the card back and
prints what changed, so the output is the confirmation.
"""
import argparse
import base64
import fcntl
import json
import os
import random
import subprocess
import sys
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deck_board import NoRedirect, read_env  # noqa: E402

API = "/index.php/apps/deck/api/v1.0"
EASTERN = ZoneInfo("America/New_York")

GAP_MIN_S, GAP_MAX_S = 6 * 60, 18 * 60          # the wait after any write, drawn at random each time
OPEN_HOUR, CLOSE_HOUR = 9, 21                    # drain posts from 9:00 AM up to (not including) 9:00 PM Eastern
RETRY_S, MAX_ATTEMPTS = 10 * 60, 3               # a failed queued write waits 10 minutes, and is parked after 3 tries
DEFAULT_ENV = Path.home() / "repos" / "Chattanooga-Digital" / ".env"   # a path, never read here; read_env opens it
DEFAULT_PREFIX = "NEXTCLOUD_PROD"
EXIT_WAIT = 75                                   # sysexits EX_TEMPFAIL: not now, queue it
EXIT_QUEUED = 3                                  # the write is stored, NOT posted yet
EXIT_UNPROVEN = 4                                # the server accepted the write, but the read-back failed or does not show it
OCS = "/ocs/v2.php/apps/deck/api/v1.0"           # comments exist only on the OCS route; the REST route answers 405
STUCK_S = 120                                    # a write "posting" for longer than this is reported as stuck
LOG_REASON_MAX = 120
_queue_id = "-"                                  # set by cmd_drain around its main() call so the log line carries the queue id
_ctx = {}                                        # per write: "card" (a new card's id), "took" (a write request left), "reason" (for the log)
MAYBE_TAKEN = (500, 502, 504)                    # answers to a write that do not say it was refused: the app or a gateway gave up part-way


class Mismatch(Exception):
    """The read-back does not show the change."""


class MaybeTaken(Exception):
    """A write got an answer that does not say whether the server took it."""


def change(step):
    """Runs one request that changes the board and notes in _ctx["took"] that the server took it or may have. An HTTP refusal
    (call's SystemExit: 4xx, 503) leaves it unset, since nothing was written. Anything else, a good answer, a timeout, a dropped
    connection or MaybeTaken, sets it, and from then on main ends the visit as EXIT_UNPROVEN, never as a failure to retry."""
    try:
        out = step()
    except SystemExit:
        raise
    except BaseException:
        _ctx["took"] = True
        raise
    _ctx["took"] = True
    return out


def short(text, n=LOG_REASON_MAX):
    return "".join(" " if ch in "\t\r\n" else ch for ch in str(text))[:n]


def readback(action, card, step):
    """Runs the read-back step of a write the server already accepted. Any failure here ends as EXIT_UNPROVEN, never as an
    ordinary failure: the drain retries ordinary failures, and a retry would post the same thing twice."""
    try:
        return step()
    except Mismatch as e:
        reason = str(e)
    except SystemExit as e:
        reason = e.code if isinstance(e.code, str) else "the read-back stopped"
    except Exception as e:   # a network error or timeout
        reason = f"{type(e).__name__}: {e}"
    _ctx["reason"] = short(reason)
    print(f"deck_write: {action} on card {card} was accepted but is NOT PROVEN: {short(reason)}", file=sys.stderr)
    raise SystemExit(EXIT_UNPROVEN)


def when_of(value):
    """Deck due text -> an aware datetime (naive text is read as Eastern, as show_due does), or None."""
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=EASTERN)


def log_path():
    return state_dir() / "deck-write-log.tsv"


def log_write(result, board, card, actions, qid="-", reason=""):
    """One line per write. Never holds comment text, titles or descriptions. A failure to log never stops the write."""
    try:
        line = "\t".join([now().isoformat(timespec="seconds"), result, str(board), str(card if card not in (None, "") else "-"),
                          "+".join(actions), qid or "-", short(reason) if result in ("failed", "unproven") else ""]) + "\n"
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
    except Exception:
        pass


def eastern_due(text):
    """'YYYY-MM-DDTHH:MM' read as Eastern time -> the same moment in UTC, ISO 8601. The Deck API drops any offset it is sent and reads the
    clock time as UTC (found live: 9 PM Eastern sent with -05:00 came back as 4 PM), so the offset is applied here, not left to it."""
    try:
        when = datetime.strptime(text, "%Y-%m-%dT%H:%M")
    except ValueError:
        sys.exit(f"deck_write: --date must look like 2026-11-10T21:00, got {text!r}")
    return when.replace(tzinfo=EASTERN).astimezone(timezone.utc).isoformat(timespec="seconds")


def show_due(value):
    """The Deck's stored due date (ISO text or None) -> '-' or e.g. 'Tue 10 Nov 2026 9:00 PM' in Eastern."""
    if not value:
        return "-"
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if when.tzinfo is None:
        when = when.replace(tzinfo=EASTERN)
    return when.astimezone(EASTERN).strftime("%a %-d %b %Y %-I:%M %p")


def call(c, method, path, body=None, api=API):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(c["URL"].rstrip("/") + api + path, data=data, method=method, headers={
        "OCS-APIRequest": "true", "Accept": "application/json", "Content-Type": "application/json",
        "Authorization": "Basic " + base64.b64encode(f"{c['USER']}:{c['PASS']}".encode()).decode()})
    opener = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if method != "GET" and e.code in MAYBE_TAKEN:
            raise MaybeTaken(f"{method} {path}: HTTP {e.code}") from None
        sys.exit(f"deck_write: {method} {path}: HTTP {e.code}")


def find_card(c, board, card_id):
    for s in call(c, "GET", f"/boards/{board}/stacks"):
        for cd in s.get("cards") or []:
            if cd["id"] == card_id:
                return s, cd
    sys.exit(f"deck_write: card {card_id} is not open on board {board}")


def find_stack(c, board, title):
    hits = [s for s in call(c, "GET", f"/boards/{board}/stacks") if s["title"] == title]
    if len(hits) != 1:
        sys.exit(f"deck_write: {len(hits)} stacks titled {title!r} on board {board}")
    return hits[0]


def save(c, board, stack_id, cd, **changes):
    owner = cd["owner"]["uid"] if isinstance(cd.get("owner"), dict) else cd["owner"]
    body = {"title": cd["title"], "description": cd.get("description") or "", "type": cd.get("type", "plain"),
            "owner": owner, "order": cd.get("order", 0), "duedate": cd.get("duedate")}
    body.update(changes)
    return call(c, "PUT", f"/boards/{board}/stacks/{stack_id}/cards/{cd['id']}", body)


def require_chat_voice(text):
    checker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_voice_check.py")
    result = subprocess.run(
        [sys.executable, checker, "--stdin", "--chat", "--strict"],
        input=text, capture_output=True, text=True, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode:
        sys.exit("deck_write: comment failed the chat voice check; no request was made")


# ---- pacing: the wait between writes, and the queue that spreads them out ----

def now():
    return datetime.now(timezone.utc)


def pick_gap():
    return random.randint(GAP_MIN_S, GAP_MAX_S)


def state_dir():
    return Path(os.environ.get("DECK_STATE_DIR") or Path.home() / ".claude" / "state")


def last_write_path():
    return state_dir() / "deck-last-write"


def queue_path():
    return state_dir() / "deck-write-queue.jsonl"


def clock(when):
    """A moment -> '9:24 AM' (Eastern, 12-hour); with the weekday and date when it is not today."""
    local = when.astimezone(EASTERN)
    if local.date() == now().astimezone(EASTERN).date():
        return local.strftime("%-I:%M %p")
    return local.strftime("%a %-d %b %-I:%M %p")


def read_last_write():
    """(when, wait seconds) of the last successful write, or None."""
    try:
        rec = json.loads(last_write_path().read_text())
        return datetime.fromisoformat(rec["at"]), int(rec["gap_s"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def record_write():
    path = last_write_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"at": now().isoformat(timespec="seconds"), "gap_s": pick_gap()}))
    os.replace(tmp, path)


def next_allowed():
    """When the next write may happen (None = any time)."""
    last = read_last_write()
    return None if last is None else last[0] + timedelta(seconds=last[1])


def gap_refusal():
    nxt = next_allowed()
    if nxt is None or now() >= nxt:
        return None
    last = read_last_write()
    return (f"deck_write: the last write was at {clock(last[0])}; the next one is allowed after {clock(nxt)} Eastern. "
            f"Queue it instead: deck_write.py queue <board> <action args> (exit {EXIT_WAIT})")


def in_window(when):
    return OPEN_HOUR <= when.astimezone(EASTERN).hour < CLOSE_HOUR


def window_at_or_after(when):
    """The first moment at or after `when` that falls inside 9:00 AM to 9:00 PM Eastern."""
    local = when.astimezone(EASTERN)
    if local.hour < OPEN_HOUR:
        return local.replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0)
    if local.hour >= CLOSE_HOUR:
        return (local + timedelta(days=1)).replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0)
    return local


class QueueLock:
    """Keeps two drains (or a drain and a queue call) from rewriting the queue file at once."""
    def __enter__(self):
        state_dir().mkdir(parents=True, exist_ok=True)
        self.fh = open(state_dir() / "deck-write-queue.lock", "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        self.fh.close()


def load_queue():
    try:
        lines = queue_path().read_text().splitlines()
    except OSError:
        return []
    return [json.loads(ln) for ln in lines if ln.strip()]


def save_queue(entries):
    path = queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
    os.replace(tmp, path)


def due_entries(entries, when):
    """Queued writes whose retry time has passed, oldest first."""
    return [e for e in entries if e.get("status") == "queued"
            and (not e.get("not_before") or datetime.fromisoformat(e["not_before"]) <= when)]


def describe(entry):
    kinds = []
    for seg in split_plus(entry["argv"]):
        kinds.append(seg[0] if seg else "?")
    card = ""
    for seg in split_plus(entry["argv"]):
        if "--card" in seg and seg.index("--card") + 1 < len(seg):
            card = " on card " + seg[seg.index("--card") + 1]
            break
    return "+".join(kinds) + card + f" (board {entry['board']})"


def split_plus(argv):
    """A lone '+' token joins actions into one visit; split on it."""
    segs = [[]]
    for tok in argv:
        if tok == "+":
            segs.append([])
        else:
            segs[-1].append(tok)
    return segs


def cmd_queue(argv):
    ap = argparse.ArgumentParser(prog="deck_write.py queue")
    ap.add_argument("--env", default=str(DEFAULT_ENV))
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    ap.add_argument("board", type=int)
    ap.add_argument("action_args", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    rest = a.action_args
    if not rest:
        sys.exit("deck_write: queue needs an action, e.g. queue 4 comment --card 9 --text T")
    ops = parse_ops(["--env", a.env, "--prefix", a.prefix, "--board", str(a.board), *rest])
    for op in ops:
        if op.action == "show":
            sys.exit("deck_write: show reads only; there is nothing to queue")
        prepare(op)   # the same checks as posting now (voice check on a comment, bad date, empty title), so a bad write never waits in the queue
    entry = {"id": uuid.uuid4().hex[:8], "queued_at": now().isoformat(timespec="seconds"), "board": a.board,
             "env": a.env, "prefix": a.prefix, "argv": rest, "status": "queued", "attempts": 0, "not_before": None}
    with QueueLock():
        entries = load_queue()
        entries.append(entry)
        save_queue(entries)
    waiting = sum(1 for e in entries if e.get("status") == "queued")
    segs = split_plus(rest)
    log_write("queued", a.board, card_of(rest), [s[0] if s else "?" for s in segs], entry["id"])
    print(f"NOT POSTED YET: queued {entry['id']}: {describe(entry)}; {waiting} waiting. "
          f"It posts when the drain reaches it (9:00 AM to 9:00 PM Eastern, one write every 6 to 18 minutes); "
          f"deck_write.py log counts what has posted.")
    return EXIT_QUEUED


def card_of(argv):
    for seg in split_plus(argv):
        if "--card" in seg and seg.index("--card") + 1 < len(seg):
            return seg[seg.index("--card") + 1]
    return "-"


def cmd_log(argv):
    ap = argparse.ArgumentParser(prog="deck_write.py log")
    ap.add_argument("--date", help="YYYY-MM-DD, Eastern (default today)")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    day = a.date or now().astimezone(EASTERN).date().isoformat()
    try:
        lines = [ln for ln in log_path().read_text().splitlines() if ln.strip()]
    except OSError:
        lines = []
    counts = {"queued": 0, "posted": 0, "failed": 0, "unproven": 0}
    for ln in lines:
        fields = ln.split("\t")
        when = when_of(fields[0])
        if not a.all and (when is None or when.astimezone(EASTERN).date().isoformat() != day):
            continue
        print(ln)
        if len(fields) > 1 and fields[1] in counts:
            counts[fields[1]] += 1
    print(", ".join(f"{n} {k}" for k, n in counts.items()))
    return 0


def cmd_queue_status(argv):
    entries = load_queue()
    queued = [e for e in entries if e.get("status") == "queued"]
    parked = [e for e in entries if e.get("status") == "failed"]
    unproven = [e for e in entries if e.get("status") == "unproven"]
    last = read_last_write()
    t = now()
    stuck = [e for e in entries if e.get("status") == "posting"
             and ((when_of(e.get("posting_since")) is None) or (t - when_of(e["posting_since"])).total_seconds() > STUCK_S)]
    line = f"{len(queued)} queued"
    if parked:
        line += f", {len(parked)} failed after {MAX_ATTEMPTS} tries"
    if unproven:
        line += f", {len(unproven)} not proven (check by hand)"
    if stuck:
        line += f", {len(stuck)} stuck mid-post (check by hand)"
    if queued:
        first = queued[0]
        ready = max(t, next_allowed() or t, datetime.fromisoformat(first["not_before"]) if first.get("not_before") else t)
        line += f"; next due {clock(window_at_or_after(ready))} Eastern ({describe(first)})"
    line += "; last write " + (f"{clock(last[0])}, the next write is allowed after {clock(last[0] + timedelta(seconds=last[1]))}"
                               if last else "never")
    print(line)
    return 0


def cmd_drain(argv):
    ap = argparse.ArgumentParser(prog="deck_write.py drain")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    say = print if (a.verbose or a.dry_run) else (lambda *x, **k: None)
    with QueueLock():
        entries = load_queue()
        t = now()
        ready = due_entries(entries, t)
        if not ready:
            say("drain: nothing due")
            return 0
        if not in_window(t):
            say(f"drain: outside 9:00 AM to 9:00 PM Eastern; {len(ready)} waiting")
            return 0
        wait = gap_refusal()
        if wait:
            say("drain: " + wait.split(" Queue it")[0])
            return 0
        entry = ready[0]
        if a.dry_run:
            print(f"drain (dry run): would post {entry['id']}: {describe(entry)}; nothing changed")
            return 0
        # Marked before the request: if this process dies mid-post the entry stays "posting" and is never picked again.
        entry["status"] = "posting"
        entry["posting_since"] = t.isoformat(timespec="seconds")
        save_queue(entries)
        global _queue_id
        error = unproven = None
        _queue_id = entry["id"]
        try:
            main(["--env", entry["env"], "--prefix", entry["prefix"], "--board", str(entry["board"]), *entry["argv"]])
        except SystemExit as stop:
            if stop.code == EXIT_WAIT:   # lost a race with a direct write; try again later
                entry["status"] = "queued"
                entry.pop("posting_since", None)
                save_queue(entries)
                return 0
            if stop.code == EXIT_UNPROVEN:
                unproven = "it reached the board, or may have, and the read-back did not prove it"
            else:
                error = str(stop.code)
        except Exception as exc:   # main turns every failure into a SystemExit; anything else is unknown ground, so it is never retried
            unproven = short(f"{type(exc).__name__}: {exc}", 200)
        finally:
            _queue_id = "-"
        if unproven is not None:
            entry["status"] = "unproven"
            entry["last_error"] = unproven[:200]
            save_queue(entries)
            print(f"drain: {entry['id']} may have posted but is NOT PROVEN ({unproven}); "
                  f"check the card by hand, it will not be retried", file=sys.stderr)
            return 1
        if error is None:
            save_queue([e for e in entries if e["id"] != entry["id"]])
            print(f"drain: posted {entry['id']}: {describe(entry)}; {len(ready) - 1} still waiting")
            return 0
        entry["attempts"] = entry.get("attempts", 0) + 1
        entry["last_error"] = error[:200]
        entry.pop("posting_since", None)
        entry["status"] = "queued"
        if entry["attempts"] >= MAX_ATTEMPTS:
            entry["status"] = "failed"
        else:
            entry["not_before"] = (t + timedelta(seconds=RETRY_S)).isoformat(timespec="seconds")
        save_queue(entries)
        print(f"drain: {entry['id']} failed ({error}); attempt {entry['attempts']} of {MAX_ATTEMPTS}", file=sys.stderr)
        return 1


SUBCOMMANDS = {"queue": cmd_queue, "queue-status": cmd_queue_status, "drain": cmd_drain, "log": cmd_log}


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    ap.add_argument("--board", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true", help="check everything and the wait; make no request and no record")
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("show")
    p.add_argument("--card", type=int, required=True)
    p = sub.add_parser("replace")
    p.add_argument("--card", type=int, required=True)
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p = sub.add_parser("append")
    p.add_argument("--card", type=int, required=True)
    p.add_argument("--text", required=True)
    p = sub.add_parser("move")
    p.add_argument("--card", type=int, required=True)
    p.add_argument("--stack", required=True)
    p = sub.add_parser("title")
    p.add_argument("--card", type=int, required=True)
    p.add_argument("--text", required=True)
    p = sub.add_parser("due")
    p.add_argument("--card", type=int, required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DDTHH:MM, Eastern time")
    p = sub.add_parser("create")
    p.add_argument("--stack", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--assign", action="append", default=[])
    p = sub.add_parser("comment")
    p.add_argument("--card", type=int, required=True)
    p.add_argument("--text", required=True)
    return ap


def parse_ops(argv):
    """Global options + one action, or several actions joined by a lone + (one visit to one card)."""
    ap = build_parser()
    segs = split_plus(argv)
    first = ap.parse_args(segs[0])
    ops = [first]
    for seg in segs[1:]:
        ops.append(ap.parse_args(["--env", first.env, "--prefix", first.prefix, "--board", str(first.board), *seg]))
    if len(ops) > 1:
        cards = {getattr(op, "card", None) for op in ops}
        if None in cards or len(cards) != 1 or any(op.action == "show" for op in ops):
            sys.exit("deck_write: actions joined with + must all name the same --card (show and create cannot be joined)")
    return ops


def prepare(a):
    """Checks that need no credentials and no network. Returns the due date in UTC for a due action."""
    if a.action == "comment":
        require_chat_voice(a.text)
    if a.action == "title" and not a.text.strip():
        sys.exit("deck_write: --text is empty; a card needs a title")
    return eastern_due(a.date) if a.action == "due" else None   # a bad date stops here, before credentials or requests


def run_one(c, b, a, new_due):
    if a.action == "show":
        s, cd = find_card(c, b, a.card)
        print(f"#{cd['id']} {cd['title']} [{s['title']}]\ndue: {show_due(cd.get('duedate'))}\n---\n{cd.get('description') or ''}")
    elif a.action == "comment":
        find_card(c, b, a.card)
        before = comment_ids(c, a.card, a.text)   # read first, so an older comment with the same text can never stand in as the proof
        answer = change(lambda: call(c, "POST", f"/cards/{a.card}/comments", {"message": a.text}, api=OCS))
        readback("comment", a.card, lambda: check_comment(c, a.card, a.text, answer, before))
    elif a.action in ("replace", "append"):
        s, cd = find_card(c, b, a.card)
        desc = cd.get("description") or ""
        if a.action == "replace":
            if desc.count(a.old) != 1:
                sys.exit(f"deck_write: --old matches {desc.count(a.old)} times in card {a.card}, need exactly 1")
            new = desc.replace(a.old, a.new)
        else:
            new = (desc.rstrip("\n") + "\n\n" if desc.strip() else "") + a.text
        change(lambda: save(c, b, s["id"], cd, description=new))

        def check():
            _, after = find_card(c, b, a.card)
            print(f"#{a.card} description now:\n{after.get('description')}")
            if (after.get("description") or "").rstrip() != new.rstrip():
                raise Mismatch("the description read back is not the one sent")
        readback(a.action, a.card, check)
    elif a.action == "move":
        s, cd = find_card(c, b, a.card)
        dest = find_stack(c, b, a.stack)
        # The REST reorder route ignores stackId (200, card stays put); the web UI's own route moves it.
        change(lambda: call(c, "PUT", f"/cards/{cd['id']}/reorder", {"stackId": dest["id"], "order": 0}, api="/index.php/apps/deck"))

        def check():
            s2, _ = find_card(c, b, a.card)
            print(f"#{a.card} {cd['title']}: {s['title']} -> {s2['title']}")
            if s2["id"] != dest["id"]:
                raise Mismatch("the move reported success but the card is still in the old stack")
        readback("move", a.card, check)
    elif a.action in ("title", "due"):
        s, cd = find_card(c, b, a.card)
        change(lambda: save(c, b, s["id"], cd, **({"title": a.text} if a.action == "title" else {"duedate": new_due})))

        def check():
            s2, after = find_card(c, b, a.card)
            if a.action == "title":
                print(f"#{a.card} title: {cd['title']} -> {after['title']} [{s2['title']}]")
                if after["title"] != a.text:
                    raise Mismatch("the title read back is not the one sent")
            else:
                print(f"#{a.card} {after['title']} [{s2['title']}] due: {show_due(cd.get('duedate'))} -> {show_due(after.get('duedate'))}")
                got = when_of(after.get("duedate"))
                if got is None or got != when_of(new_due):
                    raise Mismatch("the due date read back is not the one sent")
        readback(a.action, a.card, check)
    elif a.action == "create":
        dest = find_stack(c, b, a.stack)
        cd = change(lambda: call(c, "POST", f"/boards/{b}/stacks/{dest['id']}/cards",
                                 {"title": a.title, "type": "plain", "order": len(dest.get("cards") or []),
                                  "description": a.description}))
        new_id = cd.get("id") if isinstance(cd, dict) else None
        if new_id is not None:
            _ctx["card"] = new_id

        def check():
            if new_id is None:
                raise Mismatch("the server's answer had no card id")
            for uid in a.assign:
                call(c, "PUT", f"/boards/{b}/stacks/{dest['id']}/cards/{new_id}/assignUser", {"userId": uid})
            s2, after = find_card(c, b, new_id)
            uids = [(u.get("participant") or {}).get("uid", "?") for u in after.get("assignedUsers") or []]
            print(f"created #{after['id']} {after['title']} [{s2['title']}] to {','.join(uids) or '-'}")
            if after["title"] != a.title:
                raise Mismatch("the new card's title is not the one sent")
            if s2["id"] != dest["id"]:
                raise Mismatch("the new card is not in the destination stack")
            if not set(a.assign) <= set(uids):
                raise Mismatch("the new card is missing an assigned user")
        readback("create", new_id if new_id is not None else "?", check)


def comment_ids(c, card, text):
    """The ids (as text: the list and the post's answer need not agree on number or string) of the comments in the card's
    comment list, newest page, whose text is the text given."""
    sent = text.strip()
    data = call(c, "GET", f"/cards/{card}/comments", api=OCS)
    items = ((data or {}).get("ocs") or {}).get("data") if isinstance(data, dict) else None
    return {str(item.get("id")) for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict) and item.get("id") is not None and str(item.get("message") or "").strip() == sent}


def check_comment(c, card, text, answer, before=frozenset()):
    """The comment list must hold the comment just posted, with the text sent. With an id in the post's answer, that id must be
    there with this text. Without one, a comment with this text must be there that was not there before the post (before:
    comment_ids read ahead of it), so an older comment with the same words is never taken as the proof."""
    pid = None
    try:
        pid = answer["ocs"]["data"]["id"]
    except (KeyError, TypeError, IndexError):
        pass
    after = comment_ids(c, card, text)
    if pid is not None:
        found = str(pid) if str(pid) in after else None
    else:
        new = sorted(after - set(before))
        found = new[0] if new else None
    if found is None:
        raise Mismatch("the comment is not in the card's comment list")
    print(f"commented on #{card} (read back: comment {found})")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SUBCOMMANDS:
        return SUBCOMMANDS[argv[0]](argv[1:])
    ops = parse_ops(argv)
    dues = [prepare(a) for a in ops]
    first = ops[0]
    writes = any(a.action != "show" for a in ops)
    if writes:
        wait = gap_refusal()
        if wait and first.dry_run:
            print("dry run: " + wait)
        elif wait:
            print(wait, file=sys.stderr)
            raise SystemExit(EXIT_WAIT)
    if first.dry_run:
        print("dry run: would run " + " + ".join(a.action for a in ops) + (f" on card {ops[0].card}" if hasattr(ops[0], "card") else "")
              + f" (board {first.board}); no request made, nothing recorded")
        return 0
    _ctx.clear()
    result, reason = "posted", ""
    try:
        c = read_env(first.env, first.prefix)
        wrote = False
        try:
            for a, due in zip(ops, dues):
                wrote = wrote or a.action != "show"   # counted before the request: a half-done write still happened
                run_one(c, first.board, a, due)
        finally:
            if wrote:
                record_write()   # one visit = one write for the wait, even if a later part of it failed
    except SystemExit as stop:
        if stop.code == EXIT_UNPROVEN:
            result, reason = "unproven", _ctx.get("reason") or "accepted, read-back did not prove it"
            raise
        if _ctx.get("took"):   # an earlier part of this visit reached the board, so a retry of the whole visit would post it twice
            result, reason = "unproven", f"part of it posted, then: {stop.code}"
            print(f"deck_write: part of this write reached the board and then it stopped ({short(stop.code)}). NOT PROVEN: "
                  f"check the card by hand and do not post it again", file=sys.stderr)
            raise SystemExit(EXIT_UNPROVEN) from None
        result, reason = "failed", stop.code
        raise
    except Exception as exc:
        what = f"{type(exc).__name__}: {exc}"
        if _ctx.get("took"):   # a write request left and no answer says how it ended: the server may have taken it
            result, reason = "unproven", what
            print(f"deck_write: a write request left and its answer never said how it ended ({short(what)}). NOT PROVEN: "
                  f"check the card by hand and do not post it again", file=sys.stderr)
            raise SystemExit(EXIT_UNPROVEN) from None
        result, reason = "failed", what   # nothing was sent (the lookup before the write failed): an ordinary failure, safe to retry
        raise SystemExit(f"deck_write: {short(what)}") from None
    finally:
        if writes:
            card = _ctx.get("card") or (getattr(ops[0], "card", None))
            log_write(result, first.board, card, [a.action for a in ops], _queue_id, reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
