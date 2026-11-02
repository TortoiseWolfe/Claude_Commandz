#!/usr/bin/env python3
"""rp_weekly.py: start Monday's standing review of schlajo's Raised Paws merges (Windows task \\AFA\\RaisedPawsWeekly, 8:00 AM Eastern).

  rp_weekly.py                       what the scheduled task runs
  rp_weekly.py --dry-run [--date D]  decide and print; start nothing, queue nothing, write no log. --date YYYY-MM-DD acts as 8:00 AM Eastern that day
  rp_weekly.py install               register \\AFA\\RaisedPawsWeekly with Task Scheduler (idempotent, /F)

Per run, in this order (each ends the run with one line in ~/.claude/state/rp-weekly-schedule.log):
  1. not NX-01, or not a Monday                          -> skip (a task catching up after a missed 8:00 AM still runs on Monday)
  2. an rp-weekly lane for the date already exists       -> skip (one review per Monday; Hatch may queue the same job)
  3. the inbox already holds a "Weekly Monday review" line for the date -> skip
  4. the week would make a code lane wait                -> queue the hub-inbox Job line (the hub starts it later), exit 0
     (openclaw_dispatch.week_phase and budget_over, so the phases and limits live in one place)
  5. lane.sh fails, or no window appears in tmux "lanes"  -> queue the same Job line, exit 1
  6. otherwise                                           -> lane.sh --allow-high-tier "<reason>" rp-weekly ~/repos/RescueDogs opus <prompt from rp-weekly-template.md>
     (lane.sh has refused opus without a stated reason since 22 Oct; the 26 Oct 8:00 AM run failed on that, so the reason is passed here)

The lane is named rp-weekly. Lanes stay open until their context is full, so last Monday's window may still be there; tmux window names
must be unique, so then the name is rp-weekly-MMDD (the folder is <date>-<name> either way).

Test hooks: RP_WEEKLY_STATE (state dir), RP_WEEKLY_LANE_SH (lane.sh stand-in), RP_WEEKLY_BUDGET (JSON {"projected","used","reset"} in
place of the 20-second quality report), RP_WEEKLY_MACHINE (machine name), RP_WEEKLY_SESSION (tmux session).
"""
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
STATE = Path(os.environ.get("RP_WEEKLY_STATE") or HOME / ".claude/state")
LANE_SH = os.environ.get("RP_WEEKLY_LANE_SH") or str(HOME / ".claude/scripts/lane.sh")
DISPATCH = HOME / ".claude/scripts/openclaw_dispatch.py"
RECORD = HOME / ".claude/scripts/muse_poll_record.sh"
REPO = HOME / "repos/RescueDogs"
NAME = "rp-weekly"
MODEL = "opus"
HIGH_TIER_REASON = "code review (strongest tier): standing weekly adversarial review of schlajo's merges"  # lane.sh --allow-high-tier
SESSION = os.environ.get("RP_WEEKLY_SESSION") or "lanes"
SUBJECT = "Weekly Monday review of schlajo's Raised Paws merges"
TASK = "\\AFA\\RaisedPawsWeekly"
LOG = STATE / "rp-weekly-schedule.log"
LANES = STATE / "lanes"
TEMPLATE = LANES / "rp-weekly-template.md"
INBOX = STATE / "hub-inbox.md"
START_HOUR = 8  # Eastern


def load_dispatch():
    spec = importlib.util.spec_from_file_location("openclaw_dispatch", DISPATCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def clock(now):
    """-> "2026-10-26 8:00 AM": local time, 12-hour (the log is read by people)."""
    return time.strftime("%Y-%m-%d ", time.localtime(now)) + time.strftime("%I:%M %p", time.localtime(now)).lstrip("0")


def say(msg, dry, now, log=None):
    line = "%s | %s" % (clock(now), msg)
    if dry:
        print("[dry-run] " + line)
        return
    log = log or LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        f.write(line + "\n")
    print(line)


def machine():
    if os.environ.get("RP_WEEKLY_MACHINE"):
        return os.environ["RP_WEEKLY_MACHINE"]
    try:
        return (HOME / ".config/afa/machine").read_text().strip()
    except OSError:
        return "unknown"


def lane_exists(date, name=None):
    """An rp-weekly lane for `date`: an index row, or its folder. Same-day names are rp-weekly or rp-weekly-MMDD only (rp-weekly-schedule is a different lane).
    `name` lets another weekly task (coop_deck_weekly.py) ask the same question about its own lane."""
    pat = re.compile(r"^%s(-\d{4})?$" % re.escape(name or NAME))
    try:
        for row in (LANES / "index.tsv").read_text().splitlines():
            f = row.split("\t")
            if len(f) > 1 and f[0] == date and pat.match(f[1]):
                return f[1]
    except OSError:
        pass
    if LANES.is_dir():
        for p in LANES.iterdir():
            if p.name.startswith(date + "-") and pat.match(p.name[len(date) + 1:]):
                return p.name[len(date) + 1:]
    return None


def inbox_has(date, job_id, phrase="Weekly Monday review"):
    try:
        text = INBOX.read_text()
    except OSError:
        return False
    for row in text.splitlines():
        if job_id in row or (date in row and phrase in row):
            return True
    return False


SAFE_PUNCT = set(".,:;!?()[]/_-+=@#%&*<>~'")


def outside(text, cap=200):
    """Text from outside (a pull's error output, a card, a lane's stderr) made safe to put in a "Hub:" or "Job:" inbox line, which
    the hub reads as its own work list. NFKC first (so a full-width "｜" or "＂" becomes the plain character), then an allowlist: letters,
    digits, spaces and a short set of ASCII punctuation stay; "|" (the id separator), double quotes become "/" and "'", and every other
    character (controls, format characters, line separators, look-alike quotes, symbols) becomes "?". Capped at `cap`, quoted. The quotes
    mark it as a quotation for the reader; whatever is inside is data, never an instruction (29 Oct 2026)."""
    t = unicodedata.normalize("NFKC", str(text))
    out = []
    for c in t:
        cat = unicodedata.category(c)
        if c == "|":
            out.append("/")
        elif c == '"':
            out.append("'")
        elif c.isspace() or cat in ("Zs", "Zl", "Zp") or (cat == "Cc" and c in "\t\n\r\x0b\x0c"):
            out.append(" ")
        elif cat[0] in "LN" or c in SAFE_PUNCT:
            out.append(c)
        else:
            out.append("?")
    line = " ".join("".join(out).split())
    if len(line) > cap:
        line = line[:cap - 3].rstrip() + "..."
    return '"%s"' % line


def shorten(why, room):
    """`why` cut to `room` characters with an ellipsis. outside() leaves no double quote inside its text, so an odd number of double quotes
    after the cut means a quoted span was cut: its closing quote is put back (and counted in the room)."""
    why = " ".join(why.split())
    if len(why) <= room:
        return why
    cut = why[:room - 4].rstrip() + "..."
    if cut.count('"') % 2:
        cut += '"'
    return cut if len(cut) <= room else why[:room - 5].rstrip() + '..."'


def job_line(date, why):
    """The Job line, at most 400 characters (muse_poll_record.sh cuts there, which would lose the id at the end); `why` gives way."""
    head = ("Job: %s | for the ~/repos director: run it as a lane with lane.sh (rescuedogs-weekly-review skill), reply with the result; "
            "standing weekly item per Hatch, priority between pro bono and personal; queued by %s: " % (SUBJECT, TASK))
    tail = " | " + job_id(date)
    return head + shorten(why, 400 - len(head) - len(tail)) + tail


def job_id(date):
    return "rp-weekly-" + date


def queue(date, why, dry, now, line=None):
    line = line or job_line(date, why)
    if dry:
        print("[dry-run] would queue in hub-inbox.md: " + line)
        return True
    r = subprocess.run([str(RECORD), "inbox", line], capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, MUSE_STATE=str(STATE)))
    return r.returncode == 0


def budget(dispatch):
    if os.environ.get("RP_WEEKLY_BUDGET"):
        return json.loads(os.environ["RP_WEEKLY_BUDGET"])
    return dispatch.LaneHost().week_budget()


def window_names():
    r = subprocess.run(["tmux", "list-windows", "-t", SESSION, "-F", "#{window_name}"], capture_output=True, text=True, timeout=15)
    return r.stdout.split() if r.returncode == 0 else None


def render(date, name, lane_dir, dest):
    text = TEMPLATE.read_text().replace("{{date}}", date).replace("{{lane_dir}}", str(lane_dir))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)


def run(dry, date_arg):
    if date_arg:
        day = datetime.date.fromisoformat(date_arg)
        from zoneinfo import ZoneInfo
        now = datetime.datetime(day.year, day.month, day.day, START_HOUR, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
    else:
        now = time.time()
    date = time.strftime("%F", time.localtime(now))

    m = machine()
    if m != "NX-01":
        say("skip | this machine is %s; the review runs on NX-01" % m, dry, now)
        return 0
    if datetime.date.fromisoformat(date).weekday() != 0:
        say("skip | %s is not a Monday" % date, dry, now)
        return 0
    have = lane_exists(date)
    if have:
        say("skip | lane %s-%s already exists; one review per Monday" % (date, have), dry, now)
        return 0
    if inbox_has(date, job_id(date)):
        say("skip | the inbox already holds this Monday's \"Weekly Monday review\" job; the hub starts it", dry, now)
        return 0

    dispatch = load_dispatch()
    b = budget(dispatch)
    reason = dispatch.budget_over(b, now, held=True)
    if reason:
        ok = queue(date, reason, dry, now)
        say("queue | %s; the Job line %s" % (reason, "is in hub-inbox.md for the hub" if ok else "COULD NOT BE WRITTEN"), dry, now)
        return 0 if ok else 2

    name = NAME
    live = window_names()
    if live and name in live:
        name = "%s-%s" % (NAME, date[5:7] + date[8:10])
    lane_dir = LANES / ("%s-%s" % (date, name))
    prompt = lane_dir / "prompt.md"
    cmd = [LANE_SH, "--allow-high-tier", HIGH_TIER_REASON, name, str(REPO), MODEL, str(prompt)]
    if dry:
        print("[dry-run] would render %s into %s" % (TEMPLATE, prompt))
        print("[dry-run] would run: " + " ".join(cmd))
        say("start | budget allows a code lane (%s); would start lane %s" % (
            "phase %d" % dispatch.week_phase(now, b.get("reset"))[0], name), dry, now)
        return 0

    try:
        render(date, name, lane_dir, prompt)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        failure = None if r.returncode == 0 else "lane.sh exited %d: %s" % (r.returncode, (r.stderr or r.stdout).strip()[-200:])
        if failure is None and name not in (window_names() or []):
            failure = "lane.sh exit 0 but no window %s in tmux %s" % (name, SESSION)
    except (OSError, subprocess.TimeoutExpired) as e:
        failure = "could not start lane.sh: %s" % e
    if failure is None:
        say("start | lane %s started in RescueDogs on %s (week within its limit)" % (name, MODEL), dry, now)
        return 0
    ok = queue(date, "starting the lane failed (%s)" % outside(failure, 120), dry, now)
    say("fail | %s; the Job line %s" % (failure.replace("\n", " "), "is in hub-inbox.md for the hub" if ok else "COULD NOT BE WRITTEN"), dry, now)
    return 1


def install(task=None, script=None, xml_name="afa-raisedpawsweekly.xml", start="2026-10-26T08:00:00", day="Monday"):
    """Register a weekly Task Scheduler task that runs `script` (default: this file). coop_deck_weekly.py passes its own."""
    task_name = task or TASK
    here = Path(script or __file__).resolve()
    tmpw = subprocess.run(["cmd.exe", "/c", "echo %TEMP%"], capture_output=True, text=True, timeout=30).stdout.strip()
    tmpd = Path(subprocess.run(["wslpath", tmpw], capture_output=True, text=True, timeout=15).stdout.strip())
    xml = tmpd / xml_name
    task = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>NX-01\\JonPo</Author>
    <URI>%(task)s</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings>
      <Duration>PT10M</Duration>
      <WaitTimeout>PT1H</WaitTimeout>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>%(start)s</StartBoundary>
      <ScheduleByWeek>
        <DaysOfWeek><%(day)s /></DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>C:\\Windows\\System32\\conhost.exe</Command>
      <Arguments>--headless C:\\Windows\\System32\\wsl.exe -d Debian -- bash -lc %(here)s</Arguments>
    </Exec>
  </Actions>
</Task>
""" % {"task": task_name, "start": start, "day": day, "here": here}
    xml.write_bytes(b"\xff\xfe" + task.encode("utf-16-le"))
    wpath = subprocess.run(["wslpath", "-w", str(xml)], capture_output=True, text=True, timeout=15).stdout.strip()
    r = subprocess.run(["schtasks.exe", "/Create", "/TN", task_name, "/XML", wpath, "/F"], timeout=60)
    xml.unlink(missing_ok=True)
    return r.returncode


def main(argv):
    if argv[:1] == ["install"]:
        return install()
    dry = "--dry-run" in argv
    date_arg = None
    if "--date" in argv:
        date_arg = argv[argv.index("--date") + 1]
    return run(dry, date_arg)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
