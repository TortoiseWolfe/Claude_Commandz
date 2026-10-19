#!/usr/bin/env python3
"""hub_lean.py: make the hub session (claude started in ~/repos) start as lean as a lane does.

Why: the hub re-reads everything it starts with on every turn. Measured 19 Oct: 81.5K tokens before its first
reply, against 47K for a lane. The difference is lane.sh's launch flags, which the hub never got. A project
settings file in ~/repos reaches the hub with no new flags typed: Claude Code reads <cwd>/.claude/settings.json.

What it writes into ~/repos/.claude/settings.json (the workspace .gitignore allowlist keeps it untracked):
  enabledPlugins        false for every plugin in lane.sh's LANE_PLUGINS_OFF (parsed from lane.sh, so there is one list)
  permissions.deny      lane.sh's LANE_TOOLS_OFF: the Artifact tools, whose schema alone is about 13K tokens
  deniedMcpServers      every claude.ai connector except the three the hub used in its 16-19 Oct transcripts (Gmail,
                        Google Calendar, Google Drive): Resend, Calendly, Clay ... are not even connected.
Other keys already in the file are kept; the three above are managed.

  hub_lean.py --print     show the managed settings
  hub_lean.py --install   write them if the file differs (silent when already current)
  hub_lean.py --check     exit 1 when the file is missing or stale
"""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The claude.ai connectors the hub turns off. The hub's 16-19 Oct transcripts (26 sessions) called only Gmail (daily:
# list_drafts, search_threads, get_draft, create_draft ...), Google Drive (2 searches) and Google Calendar (2 calls); those
# three stay on. An allow-list would be tidier, but allowedMcpServers ignores claude.ai connectors (it blocked Gmail too,
# probed 19 Oct), so a connector added to the account later loads until its name is added here.
HUB_CONNECTORS_OFF = [
    "claude.ai Brevo", "claude.ai Base44", "claude.ai Calendly", "claude.ai Claude Docs", "claude.ai Clay",
    "claude.ai Google Docs", "claude.ai Google Sheets", "claude.ai Google Slides", "claude.ai PayPal",
    "claude.ai Resend", "claude.ai Slack", "claude.ai Trimble SketchUp", "claude.ai Vibe Prospecting",
    "claude.ai ZipRecruiter",
]


def lane_list(name):
    """The space-separated value of NAME="..." in lane.sh (the one place the lane launch flags are kept)."""
    text = (HERE / "lane.sh").read_text()
    m = re.search(r'^%s="([^"]*)"' % name, text, re.M)
    if not m:
        sys.exit("hub_lean: %s not found in lane.sh" % name)
    return m.group(1).split()


def managed():
    return {
        "enabledPlugins": {p: False for p in lane_list("LANE_PLUGINS_OFF")},
        "permissions": {"deny": lane_list("LANE_TOOLS_OFF")},
        "deniedMcpServers": [{"serverName": n} for n in HUB_CONNECTORS_OFF],
    }


def target():
    return Path(os.environ.get("AFA_REPOS") or Path.home() / "repos") / ".claude" / "settings.json"


def merged(existing):
    """Existing settings with the managed keys applied: plugins merged by name, deny entries added, denied servers replaced."""
    want = managed()
    out = dict(existing)
    out["enabledPlugins"] = {**existing.get("enabledPlugins", {}), **want["enabledPlugins"]}
    perms = dict(existing.get("permissions", {}))
    perms["deny"] = list(dict.fromkeys([*perms.get("deny", []), *want["permissions"]["deny"]]))
    out["permissions"] = perms
    out["deniedMcpServers"] = want["deniedMcpServers"]
    return out


def load(path):
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except ValueError:
        sys.exit("hub_lean: %s is not valid JSON; leaving it alone" % path)
    if not isinstance(data, dict):
        sys.exit("hub_lean: %s is not a JSON object; leaving it alone" % path)
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--print", action="store_true", dest="show")
    g.add_argument("--install", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.show:
        print(json.dumps(managed(), indent=2))
        return 0
    path = target()
    existing = load(path)
    want = merged(existing)
    if existing == want:
        return 0
    if a.check:
        print("hub_lean: %s is missing or stale; run hub_lean.py --install" % path, file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(want, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    print("hub_lean: wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
