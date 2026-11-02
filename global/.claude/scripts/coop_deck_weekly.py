#!/usr/bin/env python3
"""coop_deck_weekly.py: Tuesday's check of Jonathan's cards on the co-op Deck boards 2 (Operations) and 8 (Support) (Windows task \\AFA\\CoopDeckWeekly, 9:00 AM Eastern, before the co-op's 4:00 PM ops meeting).

  coop_deck_weekly.py                       what the scheduled task runs
  coop_deck_weekly.py --dry-run [--force] [--date D]   decide and print; pull nothing, start nothing, queue nothing, write no log.
                                            --date YYYY-MM-DD acts as 9:00 AM Eastern that day
  coop_deck_weekly.py --force               ignore the weekday (the first run, or a catch-up); every other guard still holds
  coop_deck_weekly.py install               register \\AFA\\CoopDeckWeekly with Task Scheduler (idempotent, /F)

Built on rp_weekly.py (same folder): the guards, the budget check, the log line, the inbox writer and the Task Scheduler installer are its,
reached by import, not copied. Per run, in this order (each ends the run with one line in ~/.claude/state/coop-deck-weekly-schedule.log):
  1. not NX-01, or not a Tuesday (unless --force)        -> skip
  2. a coop-deck-weekly lane for the date already exists -> skip
  3. the inbox already holds this date's job or note     -> skip
  4. read-only pull of Talk, Deck and GitHub (digital-clones bin/pull_channels.py, 300 s); a dead Deck pull -> "Hub:" line, exit 1
  5. bin/deck_weekly.py diffs boards 2 and 8 against last week's snapshot and writes reports/deck-weekly-<date>.md (no model, no network)
  6. nothing changed and no routine card is open in Active -> one "Hub: Co-op Deck weekly: no change" inbox line, no lane
  7. the week would make a code lane wait                -> queue a Job line naming the digest (the hub starts the lane later), exit 0
  8. lane.sh fails, or no window appears in tmux "lanes" -> queue the same Job line, exit 1
  9. otherwise (changed > 0 or a routine card is open)   -> lane.sh coop-deck-weekly ~/repos/CD sonnet <prompt from coop-deck-weekly-template.md>

Test hooks (rp_weekly's, shared): RP_WEEKLY_STATE, RP_WEEKLY_LANE_SH, RP_WEEKLY_BUDGET, RP_WEEKLY_MACHINE, RP_WEEKLY_SESSION.
Its own: COOP_DECK_WEEKLY_CLONES (the digital-clones checkout, for a fake one in tests).
"""
import datetime
import importlib.util
import os
import re
import subprocess
import sys
import time
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("rp_weekly", HERE / "rp_weekly.py")
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)

HOME = rp.HOME
STATE = rp.STATE
LANES = rp.LANES
REPO = HOME / "repos/CD"
CLONES = Path(os.environ.get("COOP_DECK_WEEKLY_CLONES") or HOME / "repos/digital-clones")
NAME = "coop-deck-weekly"
MODEL = "sonnet"
TASK = "\\AFA\\CoopDeckWeekly"
# The lane reads Deck cards that members and anyone with board access wrote, so it starts without the tools a planted card would aim at
# (lane.sh --deny, kept in the lane folder's deny-tools.txt): Deck writes, ssh and docker. Card replies are proposed in cards.md; the hub posts them.
DENY = ("Bash(*deck_write*)", "Bash(*deck_board*)", "Bash(*ssh*)", "Bash(ssh:*)", "Bash(*docker*)", "Bash(docker:*)")
SUBJECT = "Co-op Deck weekly check of Jonathan's cards on boards 2 and 8"
PHRASE = "Co-op Deck weekly"
LOG = STATE / "coop-deck-weekly-schedule.log"
TEMPLATE = LANES / "coop-deck-weekly-template.md"
START_HOUR = 9  # Eastern
TUESDAY = 1
ROUTINE_CARDS = {"173": {"board": 8, "stack": "Active"}}


def check_routine_cards(date):
    """-> (open_routine_ids, log_note)"""
    snapshot_path = CLONES / "state/deck-weekly" / ("%s.json" % date)
    if not snapshot_path.exists():
        return [], "snapshot missing"
    try:
        data = json.loads(snapshot_path.read_text())
        cards = data.get("cards", {})
        found = []
        for cid, info in ROUTINE_CARDS.items():
            card = cards.get(cid)
            if card and card.get("board") == info["board"] and \
               card.get("lane") == info["stack"] and card.get("state") == "OPEN":
                found.append(cid)
        return found, ""
    except Exception as e:
        return [], "snapshot unreadable: %s" % e


def job_id(date):
    return "%s-%s" % (NAME, date)


def say(msg, dry, now):
    rp.say(msg, dry, now, log=LOG)


def hub_line(date, text):
    return "Hub: %s | %s" % (text, job_id(date))


def job_line(date, digest, why):
    """At most 400 characters (muse_poll_record.sh cuts there, which would lose the id at the end); `why` gives way."""
    head = ("Job: %s | for the ~/repos director: start lane %s in ~/repos/CD from hub/claude-private/state/lanes/%s-template.md, "
            "digest %s; queued by %s: " % (SUBJECT, NAME, NAME, digest, TASK))
    tail = " | " + job_id(date)
    return head + rp.shorten(why, 400 - len(head) - len(tail)) + tail


def pull_cmd():
    return [sys.executable, str(CLONES / "bin/pull_channels.py")]


def diff_cmd(date_arg):
    return [sys.executable, str(CLONES / "bin/deck_weekly.py")] + (["--date", date_arg] if date_arg else [])


def pull():
    """-> None when the Deck pull worked, else the reason. Talk or GitHub failing alone is not fatal: only Deck is read here."""
    try:
        r = subprocess.run(pull_cmd(), cwd=CLONES, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "pull_channels.py did not finish: %s" % e
    out = (r.stdout or "") + (r.stderr or "")
    if "!! deck FAILED" in out or (r.returncode not in (0, 1)):
        m = re.search(r"!! deck FAILED: (.*)", out)
        return "Deck pull failed: %s" % ((m.group(1) if m else out.strip()[-200:]) or "exit %d" % r.returncode)
    return None


def digest(date_arg):
    """-> (result dict, None) or (None, reason). deck_weekly.py's last line is `RESULT {json}`."""
    try:
        r = subprocess.run(diff_cmd(date_arg), cwd=CLONES, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "deck_weekly.py did not finish: %s" % e
    line = next((ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("RESULT ")), None)
    if r.returncode != 0 or not line:
        return None, "deck_weekly.py failed (exit %d): %s" % (r.returncode, (r.stderr or r.stdout).strip()[-200:])
    return json.loads(line[len("RESULT "):]), None


def run(dry, force, date_arg):
    if date_arg:
        day = datetime.date.fromisoformat(date_arg)
        from zoneinfo import ZoneInfo
        now = datetime.datetime(day.year, day.month, day.day, START_HOUR, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
    else:
        now = time.time()
    date = time.strftime("%F", time.localtime(now))

    m = rp.machine()
    if m != "NX-01":
        say("skip | this machine is %s; the check runs on NX-01" % m, dry, now)
        return 0
    if not force and datetime.date.fromisoformat(date).weekday() != TUESDAY:
        say("skip | %s is not a Tuesday" % date, dry, now)
        return 0
    have = rp.lane_exists(date, NAME)
    if have:
        say("skip | lane %s-%s already exists; one check per Tuesday" % (date, have), dry, now)
        return 0
    if rp.inbox_has(date, job_id(date), PHRASE):
        say("skip | the inbox already holds this date's \"%s\" line" % PHRASE, dry, now)
        return 0

    dispatch = rp.load_dispatch()
    b = rp.budget(dispatch)
    reason = dispatch.budget_over(b, now, held=True)
    name = NAME
    live = rp.window_names()
    if live and name in live:
        name = "%s-%s" % (NAME, date[5:7] + date[8:10])
    lane_dir = LANES / ("%s-%s" % (date, name))
    prompt = lane_dir / "prompt.md"
    cmd = [rp.LANE_SH] + [a for rule in DENY for a in ("--deny", rule)] + [name, str(REPO), MODEL, str(prompt)]
    report = CLONES / "reports" / ("deck-weekly-%s.md" % date)

    if dry:
        open_routine_dry, note_dry = check_routine_cards(date)
        routine_desc_dry = ("routine card %s open" % ", ".join(open_routine_dry)) if open_routine_dry else "no routine card open"
        if note_dry:
            routine_desc_dry += " (%s)" % note_dry
        else:
            routine_desc_dry += " (snapshot as of the last real run)"
        print("[dry-run] guards passed: NX-01, %s%s, no lane or inbox line for %s" % (
            date, " (forced)" if force else " is a Tuesday", date))
        print("[dry-run] would pull: %s (cwd %s, 300 s limit)" % (" ".join(pull_cmd()), CLONES))
        print("[dry-run] would diff: %s" % " ".join(diff_cmd(date_arg)))
        print("[dry-run] digest would be at %s" % report)
        print("[dry-run] snapshot: %s" % routine_desc_dry)
        print("[dry-run] no change and no routine card: would queue one \"Hub: %s: no change\" line; no lane" % PHRASE)
        if reason:
            print("[dry-run] (changes or routine card) + week full (%s): would queue the Job line instead of a lane" % reason)
        else:
            print("[dry-run] changes or routine card: would render %s into %s" % (TEMPLATE, prompt))
            print("[dry-run] changes or routine card: would run: " + " ".join(cmd))
        say("decide | budget %s; %s; nothing pulled or started" % (
            reason or "allows a code lane (phase %d)" % dispatch.week_phase(now, b.get("reset"))[0],
            routine_desc_dry), dry, now)
        return 0

    err = pull()
    if err is None:
        result, err = digest(date_arg)
    if err:
        ok = rp.queue(date, err, dry, now, line=hub_line(date, "%s failed: %s" % (PHRASE, rp.outside(err))))
        say("fail | %s; the Hub line %s" % (err.replace("\n", " "), "is in hub-inbox.md" if ok else "COULD NOT BE WRITTEN"), dry, now)
        return 1

    open_routine, note = check_routine_cards(date)
    routine_desc = ("routine card %s open" % ", ".join(open_routine)) if open_routine else "no routine card open"
    if note:
        routine_desc += " (%s)" % note

    if result["changed"] == 0 and not open_routine:
        ok = rp.queue(date, "", dry, now, line=hub_line(date, "%s: no change; %s (%d open cards of his)" % (PHRASE, routine_desc, result["open_mine"])))
        say("no change | %d open cards of his; %s; digest %s; the Hub line %s" % (
            result["open_mine"], routine_desc, result["report"], "is in hub-inbox.md" if ok else "COULD NOT BE WRITTEN"), dry, now)
        return 0 if ok else 2

    log_reason = ("%s; %d changes" % (routine_desc, result["changed"])) if open_routine else ("%d changes" % result["changed"])
    if reason:
        ok = rp.queue(date, "", dry, now, line=job_line(date, result["report"], reason))
        say("queue | %s; %s; the Job line %s" % (reason, log_reason, "is in hub-inbox.md for the hub" if ok else "COULD NOT BE WRITTEN"), dry, now)
        return 0 if ok else 2

    try:
        text = TEMPLATE.read_text().replace("{{date}}", date).replace("{{lane_dir}}", str(lane_dir)) \
            .replace("{{digest}}", result["report"]).replace("{{counts}}", json.dumps(result["counts"]))
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text(text)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        failure = None if r.returncode == 0 else "lane.sh exited %d: %s" % (r.returncode, (r.stderr or r.stdout).strip()[-200:])
        if failure is None and name not in (rp.window_names() or []):
            failure = "lane.sh exit 0 but no window %s in tmux %s" % (name, rp.SESSION)
    except (OSError, subprocess.TimeoutExpired) as e:
        failure = "could not start lane.sh: %s" % e
    if failure is None:
        say("start | lane %s started in CD on %s; %s; digest %s" % (name, MODEL, log_reason, result["report"]), dry, now)
        return 0
    ok = rp.queue(date, "", dry, now, line=job_line(date, result["report"], "starting the lane failed (%s)" % rp.outside(failure, 120)))
    say("fail | %s; %s; the Job line %s" % (failure.replace("\n", " "), log_reason, "is in hub-inbox.md for the hub" if ok else "COULD NOT BE WRITTEN"), dry, now)
    return 1


def main(argv):
    if argv[:1] == ["install"]:
        return rp.install(task=TASK, script=__file__, xml_name="afa-coopdeckweekly.xml",
                          start="2026-10-27T09:00:00", day="Tuesday")
    date_arg = argv[argv.index("--date") + 1] if "--date" in argv else None
    return run("--dry-run" in argv, "--force" in argv, date_arg)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
