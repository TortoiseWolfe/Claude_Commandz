#!/usr/bin/env python3
"""openclaw_terminal_install.py: a Windows Terminal profile "Receptionist" and a Start menu shortcut that open the receptionist's chat in a terminal.

  openclaw_terminal_install.py apply      add the profile to Windows Terminal's settings (a .bak-receptionist copy is kept) and make the shortcut
  openclaw_terminal_install.py verify     show whether both exist (changes nothing)
  openclaw_terminal_install.py rollback   take the profile out of the settings and delete the one shortcut file

The window runs OpenClaw's own terminal chat (openclaw tui) on agent:main:main, the one chat the app shows. Enter sends and the up arrow brings
back earlier messages typed in that window (the TUI keeps them in memory, so closing the window forgets them). It opens an existing session by
key; it never creates one. What he types reaches the dispatcher the same way the app's messages do: the TUI sends as the webchat channel, which
is the channel the hub-ask hook accepts. Typing /new or /reset inside the window would start another chat or reset this one; do not.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

NAME = "Receptionist"
GUID = "{b6c1c1f4-6f0e-5b0a-9a52-0c0e7a3f2d11}"
COMMANDLINE = "wsl.exe -d OpenClawGateway -u openclaw -- openclaw tui --session agent:main:main"
SHORTCUT = NAME + ".lnk"


def profile():
    return {"guid": GUID, "name": NAME, "commandline": COMMANDLINE, "hidden": False, "tabTitle": NAME}


def _indent(text):
    m = re.search(r"\n( +)\S", text)
    return len(m.group(1)) if m else 4


def add_profile(text):
    """-> settings text with the Receptionist profile present once (replacing an older copy of it). Everything else is kept."""
    data = json.loads(text.lstrip("﻿"))
    entry = profile()
    lst = data.setdefault("profiles", {}).setdefault("list", [])
    lst[:] = [p for p in lst if p.get("guid") != GUID]
    lst.append(entry)
    return json.dumps(data, indent=_indent(text), ensure_ascii=False) + "\n"


def remove_profile(text):
    data = json.loads(text.lstrip("﻿"))
    lst = data.get("profiles", {}).get("list", [])
    lst[:] = [p for p in lst if p.get("guid") != GUID]
    return json.dumps(data, indent=_indent(text), ensure_ascii=False) + "\n"


def has_profile(text):
    return any(p.get("guid") == GUID for p in json.loads(text.lstrip("﻿")).get("profiles", {}).get("list", []))


def shortcut_script(lnk_win, wt_win):
    """PowerShell that writes the .lnk: wt.exe opening the Receptionist profile."""
    return ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');$s.TargetPath='%s';$s.Arguments='-p \"%s\"';"
            "$s.Description='Talk to the receptionist in a terminal';$s.Save()") % (lnk_win, wt_win, NAME)


def _win(path_cmd):
    r = subprocess.run(["cmd.exe", "/c", "echo", path_cmd], capture_output=True, text=True, timeout=30, cwd=str(Path.home()))
    return r.stdout.strip().replace("\r", "")


def _wslpath(win):
    return subprocess.run(["wslpath", win], capture_output=True, text=True, timeout=30).stdout.strip()


def locations():
    local = _wslpath(_win("%LOCALAPPDATA%"))
    roaming = _wslpath(_win("%APPDATA%"))
    packages = sorted(Path(local, "Packages").glob("Microsoft.WindowsTerminal_*"))
    settings = packages[0] / "LocalState" / "settings.json" if packages else None
    return settings, Path(roaming, "Microsoft/Windows/Start Menu/Programs", SHORTCUT), Path(local, "Microsoft/WindowsApps/wt.exe")


def main(argv):
    if len(argv) != 2 or argv[1] not in ("apply", "verify", "rollback"):
        print("usage: openclaw_terminal_install.py apply|verify|rollback", file=sys.stderr)
        return 2
    settings, lnk, wt = locations()
    if settings is None or not settings.exists():
        print("Windows Terminal settings.json not found", file=sys.stderr)
        return 1
    text = settings.read_text(encoding="utf-8-sig")
    if argv[1] == "verify":
        print("profile %s: %s" % (NAME, "present" if has_profile(text) else "missing"))
        print("shortcut %s: %s" % (lnk, "present" if lnk.exists() else "missing"))
        return 0
    if argv[1] == "apply":
        backup = settings.with_name("settings.json.bak-receptionist")
        if not backup.exists():
            backup.write_text(text, encoding="utf-8")
        settings.write_text(add_profile(text), encoding="utf-8")
        if not wt.exists():
            print("wt.exe not found at %s; profile added, no shortcut made" % wt, file=sys.stderr)
            return 1
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", shortcut_script(subprocess.run(["wslpath", "-w", str(lnk)], capture_output=True, text=True).stdout.strip(),
                                                                                    subprocess.run(["wslpath", "-w", str(wt)], capture_output=True, text=True).stdout.strip())],
                       capture_output=True, timeout=60, cwd=str(Path.home()))
        print("profile added; shortcut %s" % ("made" if lnk.exists() else "NOT made"))
        return 0 if lnk.exists() else 1
    settings.write_text(remove_profile(text), encoding="utf-8")
    if lnk.exists():
        os.unlink(str(lnk))
    print("profile and shortcut removed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
