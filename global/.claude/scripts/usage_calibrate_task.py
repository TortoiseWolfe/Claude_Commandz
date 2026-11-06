#!/usr/bin/env python3
"""Register \\AFA\\UsageCalibrate with Windows Task Scheduler: usage_calibrate.js every day at 7:30 AM and
once more at 12:05 PM on Wednesdays (just after the weekly limit resets at 12:00 PM).

    usage_calibrate_task.py install     idempotent (schtasks /F); prints the task afterward
    usage_calibrate_task.py xml         print the task XML, register nothing

The action is wscript running a one-line launcher (usage_calibrate.vbs, written under %LOCALAPPDATA%\\afa by install),
which runs wsl.exe hidden, waits, and quits with the script's exit code. conhost --headless, which rp_weekly.py
uses, swallows that code, so a Cloudflare check (exit 6) used to show as Last Result 0 (issue 273). Run only when the
user is logged on (the script needs the fleet browser profile and the WSLg screen).
"""
import subprocess
import sys
from pathlib import Path

TASK = "\\AFA\\UsageCalibrate"
SCRIPT = Path(__file__).resolve().with_name("usage_calibrate.js")

XML = """<?xml version="1.0" encoding="UTF-16"?>
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
    <CalendarTrigger>
      <StartBoundary>2026-10-28T07:30:00</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
    <CalendarTrigger>
      <StartBoundary>2026-10-28T12:05:00</StartBoundary>
      <ScheduleByWeek>
        <DaysOfWeek><Wednesday /></DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>C:\\Windows\\System32\\wscript.exe</Command>
      <Arguments>//B //Nologo "%(vbs)s"</Arguments>
    </Exec>
  </Actions>
</Task>
"""


VBS_NAME = "usage_calibrate.vbs"
VBS_DEFAULT = "%LOCALAPPDATA%\\afa\\" + VBS_NAME


def vbs_text():
    """The launcher: run wsl hidden (0), wait for it (True), quit with its exit code."""
    return (
        'Dim sh, rc\r\n'
        'Set sh = CreateObject("WScript.Shell")\r\n'
        'rc = sh.Run("C:\\Windows\\System32\\wsl.exe -d Debian -- bash -lc ' + str(SCRIPT) + '", 0, True)\r\n'
        'WScript.Quit(rc)\r\n'
    )


def xml_text(vbs=VBS_DEFAULT):
    return XML % {"task": TASK, "vbs": vbs}


def _wsl_path(cmd_var):
    win = subprocess.run(["cmd.exe", "/c", "echo " + cmd_var], capture_output=True, text=True, timeout=30).stdout.strip()
    return win, Path(subprocess.run(["wslpath", win], capture_output=True, text=True, timeout=15).stdout.strip())


def install():
    local_win, local = _wsl_path("%LOCALAPPDATA%")
    (local / "afa").mkdir(parents=True, exist_ok=True)
    (local / "afa" / VBS_NAME).write_bytes(vbs_text().encode("ascii"))
    vbs_win = local_win + "\\afa\\" + VBS_NAME
    tmpw = subprocess.run(["cmd.exe", "/c", "echo %TEMP%"], capture_output=True, text=True, timeout=30).stdout.strip()
    tmpd = Path(subprocess.run(["wslpath", tmpw], capture_output=True, text=True, timeout=15).stdout.strip())
    xml = tmpd / "afa-usagecalibrate.xml"
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
