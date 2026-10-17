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
# 17 Oct (D5): the receptionist names no engine (Codex is out until 31 Oct; the hub picks) and never says a job started before
# the [hub] line does. These lines sit outside the hub-ask block, so they are swapped here. The example after NEW_EX is the
# live state's; NEW_EX itself is the not-live one, which names no engine either.
ENGINE_LINES = (
    ("Claude Code on the second tower, NCC-74656; Codex and Copilot lanes; a free review panel.",
     "Claude Code on the second tower, NCC-74656; a free review panel."),
    ("- Code in Jonathan's own repos goes to Codex or Copilot lanes.\n",
     "- Code in Jonathan's own repos, and mechanical edits, go to a hub worker; the hub picks what runs it.\n"),
    ("- Mechanical edits go to Sonnet or Haiku lanes.\n", ""),
    ("lane.sh (starts a lane in a tmux window, --engine codex|copilot)", "lane.sh (starts a lane in a tmux window)"),
    ('Example. Top layer: "That is a small test in your Claude_Commandz repo, and a Codex worker can take it. The hub is starting it now." Detail layer:',
     'Example. Top layer: "That is a small test in your Claude_Commandz repo; I\'m asking the hub to start a worker on it." Detail layer:'),
    ("TO: Codex lane\n", "TO: hub worker\n"),
)


def apply_section(text, section):
    section = section.rstrip("\n") + "\n"
    if BLOCK.search(text):
        text = BLOCK.sub(lambda _m: section, text, count=1)
    else:
        i = text.index("## Card rules") if "## Card rules" in text else text.index("## Reply rules")
        text = text[:i] + section + "\n" + text[i:]
    text = text.replace(OLD_TOP, NEW_TOP).replace(OLD_EX, NEW_EX)
    for old, new in ENGINE_LINES:
        text = text.replace(old, new)
    return text


# 17 Oct (frontdesk-9): the receptionist answers status itself, clients included, and sends anything else to the hub as a question;
# the ROUTE card is gone. Live state only: these lines promise the question path, which the not-live state does not have.
REPLY_RULES = Path(HERE / "openclaw_ask_hook/reply_rules.md")
CARD_SECTION = re.compile(r"^## Card rules\n.*?(?=^## |\Z)", re.S | re.M)
STATUS_LINES = (
    ('**What is in flight (the status digest):** the top of MEMORY.md holds a block headed "Work in flight (hub digest, as of ...)", rebuilt every 15 minutes with no model. It is your only record of the work: answer status questions and "what\'s on the to-do list?" from it, and give its as-of time; never present it as newer. For a named project, find its site, newest model, lanes, open items and next step there. "What model are we on?" means the newest .skp file for that site in the digest, never the AI model you run on. Route new items by it: name the running lane, or say nothing covers it. Client projects appear there by name and step only: that is the one thing you may say about a client; for anything past its status line, hand the item to Claude as the red lines say. If it is not in the digest, say so and ask; never guess.' + "\n",
     "**What is in flight (the status digest):** the top of MEMORY.md holds a block headed \"Work in flight (hub digest, as of ...)\", "
     "rebuilt every 15 minutes with no model. Answer status questions, \"where are we at?\" and \"what's on the to-do list?\" from it, and give "
     "its as-of time; never present it as newer. Its \"Projects on GitHub\" part has counts, PR titles and next steps. "
     "For a named project, find its site, newest model, lanes, open items and next step there. \"What model are "
     "we on?\" means the newest .skp file for that site in the digest, never the AI model you run on. Client projects get status too: the "
     "step, open issue and PR counts, PR titles with a one-line summary, who is waiting on whom, and what is next. If the digest does not "
     "hold it, never guess: send the question to the hub (see \"Reply rules\").\n"),
    ("**Routing:**\n- Code in Jonathan's own repos, and mechanical edits, go to a hub worker; the hub picks what runs it.\n- Reviews go to the free panel.\n- Web lookups and dashboard clicks go to Hatch.\n- Client judgment, client email, client code, plans and specs go to Claude.\n\n",
     '**Routing** (the hub does it): code in his own repos and mechanical edits go to a hub worker; reviews to the free panel; web lookups and dashboard clicks to Hatch; client judgment, email and code, plans and specs to Claude.\n\n'),
    ('**Your job grows by phase** (spec 002 in the AI_Workflow repo):\n- Phase 1 (done): talk with Jonathan here, keep notes, and learn the routing.\n- Phase 2 (shadow): read packs from the drop folder and write the route you\'d take, and why, into the route log. The hub still acts.\n- Dispatch (live since 16 Oct): for anything on the menu in "What happens to what Jonathan types", say in one line what is happening and end the reply with "Sending to the hub: <menu sentence>". The hub runs it at once; the "[hub]" line says what happened, so never claim a result. He works from this chat: never tell him to type a command or open a terminal.\n\n',
     '**Your job** (spec 002 in the AI_Workflow repo): phase 1 is done. Phase 2 (shadow): read packs from the drop folder and write the route you\'d take, and why, into the route log; the hub still acts. Dispatch (live since 16 Oct): for anything on the menu in "What happens to what Jonathan types", say in one line what is happening and end with the "Sending to the hub:" line. The hub runs it at once and its "[hub]" line says what happened, so never claim a result. Never tell him to type a command or open a terminal.\n\n'),
    ("- If an item's class is client or unknown, don't read it into your model. Hand it to Claude.\n",
     "- Never repeat a client's email contents, client code or credentials. Anything past a client's status goes to the hub.\n"
     "- Merging or deploying is Jonathan's call. A client's production change waits for its window: Sunday afternoon, or Tuesday evening "
     "for the co-op's servers. When he asks for one, name the window, ask if he means now, and end with \"Sending to the hub: are <repo> "
     "PRs <numbers> ready to merge?\"\n"),
)


def apply_reply_rules(text):
    """Live state only: swap the card rules for the reply rules and update the status and red lines. Applying it twice changes nothing."""
    for old, new in STATUS_LINES:
        text = text.replace(old, new)
    rules = REPLY_RULES.read_text().rstrip("\n") + "\n"
    return CARD_SECTION.sub(lambda _m: rules, text, count=1)


def _wsl(args, stdin=None):
    r = subprocess.run(["wsl.exe", "-d", "OpenClawGateway", "-u", "openclaw", "-e"] + args, input=stdin, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.replace("\r", "")


WRITE_SCRIPT = (
    'set -eu; f=$1; stamp=$2; cp -p "$f" "$f.bak-ask-$stamp"; tmp=$(mktemp "$f.new.XXXXXX"); '
    'cat > "$tmp"; chmod --reference="$f" "$tmp"; mv "$tmp" "$f"; echo written'
)


def write_argv(stamp):
    """A fixed command; the new file content travels on stdin and is never part of any shell text."""
    return ["bash", "-c", WRITE_SCRIPT, "_", AGENTS, stamp]


def main(argv):
    if len(argv) != 1 or argv[0] not in ("live", "notlive", "show"):
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    rc, text = _wsl(["cat", AGENTS])
    if rc != 0 or not ("## Card rules" in text or "## Reply rules" in text):
        print("could not read AGENTS.md", file=sys.stderr)
        return 1
    if argv[0] == "show":
        m = BLOCK.search(text)
        print(m.group(0) if m else "(no hub-ask section yet)")
        return 0
    new = apply_section(text, SECTIONS[argv[0]].read_text())
    if argv[0] == "live":
        new = apply_reply_rules(new)
    if new == text:
        print("AGENTS.md already says this")
        return 0
    if len(new) > 20000:
        print("AGENTS.md would pass the 20,000-character cap", file=sys.stderr)
        return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    body = new if new.endswith("\n") else new + "\n"
    rc, out = _wsl(write_argv(stamp), stdin=body)
    _, back = _wsl(["cat", AGENTS])
    if rc != 0 or "written" not in out or back.rstrip("\n") != new.rstrip("\n"):
        print("write or read-back failed; the backup AGENTS.md.bak-ask-%s is beside it" % stamp, file=sys.stderr)
        return 1
    print("AGENTS.md now says %s (%d chars); backup AGENTS.md.bak-ask-%s" % (argv[0], len(back), stamp))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
