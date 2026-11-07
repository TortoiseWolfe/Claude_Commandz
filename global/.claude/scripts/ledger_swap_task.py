#!/usr/bin/env python3
"""Register \\AFA\\LedgerSwap with Windows Task Scheduler: ledger_swap_daily.py run, twice a day (8:20 AM and 4:20 PM), the same way
\\AFA\\UsageCalibrate runs. Each run stages this machine's ledger note as a Gmail draft (the second run of a day rewrites that
day's draft in place) and reads the other machine's newest note. Nothing is sent.

    ledger_swap_task.py install     idempotent (schtasks /F); prints the task afterward
    ledger_swap_task.py xml         print the task XML, register nothing

The action is wscript running a one-line launcher (ledger_swap.vbs under %LOCALAPPDATA%\\afa, written by install) that runs wsl.exe
hidden, waits, and quits with the script's exit code, so Task Scheduler's Last Result shows a failed run (exit 1) instead of 0.
Each run adds one line to ~/.claude/state/ledger-swap.log.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = "\\AFA\\LedgerSwap"
SCRIPT = HERE / "ledger_swap_daily.py"
VBS_NAME = "ledger_swap.vbs"
VBS_DEFAULT = "%LOCALAPPDATA%\\afa\\" + VBS_NAME
TIMES = ("08:20:00", "16:20:00")

XML_HEAD = """<?xml version="1.0" encoding="UTF-16"?>
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
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
"""
XML_TRIGGER = """    <CalendarTrigger>
      <StartBoundary>2026-11-08T%(at)s</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
"""
XML_TAIL = """  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>C:\\Windows\\System32\\wscript.exe</Command>
      <Arguments>//B //Nologo "%(vbs)s"</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def vbs_text():
    """The launcher: run wsl hidden (0), wait for it (True), quit with its exit code."""
    return (
        'Dim sh, rc\r\n'
        'Set sh = CreateObject("WScript.Shell")\r\n'
        'rc = sh.Run("C:\\Windows\\System32\\wsl.exe -d Debian -- bash -lc ' + "'python3 -I " + str(SCRIPT) + " run'" + '", 0, True)\r\n'
        'WScript.Quit(rc)\r\n'
    )


def xml_text(vbs=VBS_DEFAULT):
    return (XML_HEAD % {"task": TASK} + "".join(XML_TRIGGER % {"at": t} for t in TIMES) + XML_TAIL % {"vbs": vbs})


def _wsl_path(cmd_var):
    win = subprocess.run(["cmd.exe", "/c", "echo " + cmd_var], capture_output=True, text=True, timeout=30).stdout.strip()
    return win, Path(subprocess.run(["wslpath", win], capture_output=True, text=True, timeout=15).stdout.strip())


def install():
    if "/.claude/scripts/" not in str(SCRIPT):
        print("refusing: the task must run the installed script (~/.claude/scripts/ledger_swap_daily.py), not %s;" % SCRIPT)
        print("install the scripts first, then run python3 ~/.claude/scripts/ledger_swap_task.py install")
        return 2
    local_win, local = _wsl_path("%LOCALAPPDATA%")
    (local / "afa").mkdir(parents=True, exist_ok=True)
    (local / "afa" / VBS_NAME).write_bytes(vbs_text().encode("ascii"))
    vbs_win = local_win + "\\afa\\" + VBS_NAME
    tmpw = subprocess.run(["cmd.exe", "/c", "echo %TEMP%"], capture_output=True, text=True, timeout=30).stdout.strip()
    tmpd = Path(subprocess.run(["wslpath", tmpw], capture_output=True, text=True, timeout=15).stdout.strip())
    xml = tmpd / "afa-ledgerswap.xml"
    xml.write_bytes(b"\xff\xfe" + xml_text(vbs_win).encode("utf-16-le"))
    wpath = subprocess.run(["wslpath", "-w", str(xml)], capture_output=True, text=True, timeout=15).stdout.strip()
    r = subprocess.run(["schtasks.exe", "/Create", "/TN", TASK, "/XML", wpath, "/F"], timeout=60)
    xml.unlink(missing_ok=True)
    if r.returncode == 0:
        subprocess.run(["schtasks.exe", "/Query", "/TN", TASK, "/FO", "LIST"], timeout=30)
    return r.returncode


if __name__ == "__main__":
    if sys.argv[1:] == ["install"]:
        sys.exit(install())
    if sys.argv[1:] == ["xml"]:
        print(xml_text())
        sys.exit(0)
    print(__doc__)
    sys.exit(1)
