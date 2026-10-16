#!/usr/bin/env python3
"""openclaw_ask_agents.py: keep what OpenClaw's AGENTS.md promises equal to what the hub-ask path really does.

  openclaw_ask_agents.py notlive    nothing Jonathan types reaches the hub: "I can't start this myself yet; ask the hub."
  openclaw_ask_agents.py live       the hook and the poller are running: say what they will actually do
  openclaw_ask_agents.py show       print the section now in AGENTS.md (changes nothing)

It replaces the block between the hub-ask markers (or inserts it before "## Card rules"), and removes the two
promises in the card rules that no tool could keep. The change is read back and compared; a backup copy of
AGENTS.md is written first. Hub -> OpenClaw only, as the openclaw user, through wsl.exe with a timeout.
"""
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECTIONS = {"live": HERE / "openclaw_ask_hook/agents_live.md", "notlive": HERE / "openclaw_ask_hook/agents_notlive.md"}
AGENTS = "/home/openclaw/.openclaw/workspace/AGENTS.md"
BLOCK = re.compile(r"<!-- hub-ask:begin -->.*?<!-- hub-ask:end -->\n?", re.S)

OLD_TOP = ("- **Top layer, always:** one or two plain sentences saying what will happen, who does it, and when or where he will see the result; "
           "or the straight answer to his question. No field names, no folder paths, no jargon.")
NEW_TOP = ("- **Top layer, always:** one or two plain sentences: the straight answer to his question, or the top line that the section "
           "\"What happens to what Jonathan types\" gives for his request. Say only what will actually happen. No field names, no folder paths, no jargon.")
OLD_EX = ('Example. Top layer: "A Codex lane will write the missing test for the pickup script and report back; you will see it appear in the lane list '
          'in the hub when it is done." Detail layer:')
NEW_EX = 'Example. Top layer: "I can\'t start this myself yet; ask the hub." Detail layer:'


def apply_section(text, section):
    section = section.rstrip("\n") + "\n"
    if BLOCK.search(text):
        text = BLOCK.sub(lambda _m: section, text, count=1)
    else:
        i = text.index("## Card rules")
        text = text[:i] + section + "\n" + text[i:]
    return text.replace(OLD_TOP, NEW_TOP).replace(OLD_EX, NEW_EX)


def _wsl(args, stdin=None):
    r = subprocess.run(["wsl.exe", "-d", "OpenClawGateway", "-u", "openclaw", "--"] + args, input=stdin, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.replace("\r", "")


def main(argv):
    if len(argv) != 1 or argv[0] not in ("live", "notlive", "show"):
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    rc, text = _wsl(["cat", AGENTS])
    if rc != 0 or "## Card rules" not in text:
        print("could not read AGENTS.md", file=sys.stderr)
        return 1
    if argv[0] == "show":
        m = BLOCK.search(text)
        print(m.group(0) if m else "(no hub-ask section yet)")
        return 0
    new = apply_section(text, SECTIONS[argv[0]].read_text())
    if new == text:
        print("AGENTS.md already says this")
        return 0
    if len(new) > 20000:
        print("AGENTS.md would pass the 20,000-character cap", file=sys.stderr)
        return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    script = ("set -eu\nf=%s\ncp -p \"$f\" \"$f.bak-ask-%s\"\ntmp=$(mktemp \"$f.new.XXXXXX\")\ncat > \"$tmp\" <<'__OCASK_AGENTS_EOF__'\n%s__OCASK_AGENTS_EOF__\n"
              "chmod --reference=\"$f\" \"$tmp\"\nmv \"$tmp\" \"$f\"\necho written\n") % (AGENTS, stamp, new if new.endswith("\n") else new + "\n")
    rc, out = _wsl(["bash", "-s"], stdin=script)
    _, back = _wsl(["cat", AGENTS])
    if rc != 0 or "written" not in out or back.rstrip("\n") != new.rstrip("\n"):
        print("write or read-back failed; the backup AGENTS.md.bak-ask-%s is beside it" % stamp, file=sys.stderr)
        return 1
    print("AGENTS.md now says %s (%d chars); backup AGENTS.md.bak-ask-%s" % (argv[0], len(back), stamp))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
