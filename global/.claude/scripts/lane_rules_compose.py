#!/usr/bin/env python3
"""lane_rules_compose.py: build the rule file a Claude lane starts with from the parts in hub/lane-rules/.

Why: lane.sh used to send the whole of hub/lane-rules.md (about 14K tokens) to every lane, and every lane re-reads it on
every turn. About 60 percent of it covers mail, browser sign-ins and job forms, streams, co-op work and running the fleet.
Now the rules are split into parts: core.md (every lane gets it) and one part per kind of work. A lane gets core plus each
part whose trigger words appear in its name, folder, prompt text, or (for mail) its connectors setting. When unsure, the
part goes in: a trigger is a plain word list below, and a lane that mentions any of them gets the rules.

  lane_rules_compose.py --name N --dir D --connectors on|off --prompt FILE --out FILE [--model M] [--rules-dir DIR]
  lane_rules_compose.py --all --out FILE      every part (a lane whose composing failed still gets all the rules)
  lane_rules_compose.py --stats FILE...       which parts each prompt file would get, and the characters sent

The first line of the output says which parts it holds and where the others are, so a lane that thinks a rule is missing
can read hub/lane-rules/<part>.md. A short "key rules" block (hub/lane-rules/key-rules-lane.txt) sits above core; the
event hook (rules_reminder.py) repeats the same block every 30 tool calls.
"""
import argparse
import os
import re
import sys
from pathlib import Path

HOME = Path.home()
DEFAULT_DIR = Path(os.environ.get("LANE_RULES_DIR") or HOME / "repos/hub/lane-rules")
ORDER = ["core", "mail", "browser", "streams", "coop", "fleet"]

# Plain word lists. Matched against lower-case "name + folder + first 30000 characters of the prompt".
TRIGGERS = {
    "mail": r"gmail|e-?mail|inbox|draft|calendar|newsletter|recruiter|greg|william|invoice|outreach|"
            r"listmonk|resend|mailbox|\bmail\b|reply to|client reply|absorb",
    "browser": r"browser|sign[- ]?in|log ?in|login|chromium|playwright|puppeteer|\bcdp\b|cloudflare|captcha|passkey|"
               r"\bapply\b|job form|application form|\bforms? (?:fill|submit|answers)|scout|lever|greenhouse|ashby|rippling|"
               r"upwork|godaddy|stripe|resume|career|cover letter|agent_browser|oauth|2fa|\bsudo\b|collaborator|webauthn|\bsso\b",
    "streams": r"\bstreams?\b|twitch|youtube|\bband\b|wagon|\bsongs?\b|music|lyric|jingle|blender|sketchup|site[- ]model|"
               r"data/sites|photogrammetry|\bobs\b|rtmp|album|ffmpeg|turnaround|poem|\b3d\b",
    "coop": r"co-?op|chattanooga|william|\bgreg\b|eduity|\bcd-|nextcloud|\bdeck\b|hubzilla|civicrm|techjoy|"
            r"consemble|peermentor|elev8tek|harmonic ?egg|\bjoc\b|talk post",
    "fleet": r"lane\.sh|lane_watch|lane_session|dispatch|openclaw|--engine|\bcodex\b|opencode|\bagy\b|copilot|budget|"
             r"usage_calibrate|claude_commandz|hub_digest|hub_lean",
}
WORKSPACE_DIRS = ("/repos", "/repos/hub")


def read_part(rules_dir, name):
    try:
        return (rules_dir / f"{name}.md").read_text()
    except OSError:
        return ""


def pick(name, dirpath, connectors, prompt_text):
    text = f"{name}\n{dirpath}\n{prompt_text[:30000]}".lower()
    got = ["core"]
    for part in ORDER[1:]:
        if part == "mail" and connectors == "on":
            got.append(part)
        elif re.search(TRIGGERS[part], text):
            got.append(part)
    d = (dirpath or "").rstrip("/")
    if d.endswith(WORKSPACE_DIRS) and "fleet" not in got:  # a lane started in the workspace or hub folder may run the fleet
        got.append("fleet")
    return got


def compose(parts, rules_dir):
    key = ""
    try:
        key = (rules_dir / "key-rules-lane.txt").read_text().strip()
    except OSError:
        pass
    others = [p for p in ORDER if p not in parts]
    head = "<!-- lane rules parts loaded: %s%s -->" % (
        ", ".join(parts),
        ("; not loaded: %s (read ~/repos/hub/lane-rules/<part>.md if a rule of that kind seems missing)" % ", ".join(others))
        if others else "")
    chunks = [head]
    if key:
        chunks.append(key)
    for p in parts:
        t = read_part(rules_dir, p).rstrip("\n")
        if t:
            chunks.append(t)
    return "\n\n".join(chunks) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="")
    ap.add_argument("--dir", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--connectors", default="off")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--rules-dir", default=str(DEFAULT_DIR))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--stats", nargs="*")
    a = ap.parse_args()
    rules_dir = Path(a.rules_dir)
    if a.stats is not None:
        tot = {p: 0 for p in ORDER}
        n = 0
        for f in a.stats:
            try:
                t = Path(f).read_text(errors="replace")
            except OSError:
                continue
            got = pick(Path(f).parent.name, "", "off", t)
            n += 1
            for p in got:
                tot[p] += 1
            print("%6d chars  %-22s %s" % (len(compose(got, rules_dir)), Path(f).parent.name[:22], ",".join(got)))
        print("prompts %d; part inclusion: %s" % (n, " ".join("%s=%d" % (p, tot[p]) for p in ORDER)))
        return 0
    if not a.out:
        sys.exit("lane_rules_compose: --out FILE is required")
    pf = Path(a.prompt) if a.prompt else None
    if a.all or pf is None or not pf.is_file():  # no prompt to judge by: every part
        parts = ORDER
    else:
        parts = pick(a.name, a.dir, a.connectors, pf.read_text(errors="replace"))
    if not read_part(rules_dir, "core").strip():
        sys.exit("lane_rules_compose: %s/core.md is missing or empty" % rules_dir)
    text = compose(parts, rules_dir)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, out)
    print(",".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
