#!/usr/bin/env python3
"""lane_rules_compose.py: build the rule file a Claude lane starts with from the parts in hub/lane-rules/.

Why: lane.sh used to send the whole of hub/lane-rules.md (about 14K tokens) to every lane, and every lane re-reads it on
every turn. About 60 percent of it covers mail, browser sign-ins and job forms, streams, co-op work and running the fleet.
Now the rules are split into parts: core.md (every lane gets it) and one part per kind of work. A lane gets core plus each
part whose trigger words appear in its name, folder, task text (the prompt above its tools list), or (for mail) its
connectors setting. A trigger is a plain word list below. Core's own table points to every part, so a lane that needs one
opens it.

  lane_rules_compose.py --name N --dir D --connectors on|off --prompt FILE --out FILE [--model M] [--rules-dir DIR]
  lane_rules_compose.py ... --rules LIST      auto (default: the keyword picking), all, none (core only) or part names, e.g. mail,coop
  lane_rules_compose.py --all --out FILE      every part (a lane whose composing failed still gets all the rules)
  lane_rules_compose.py --stats FILE...       which parts each prompt file would get, and the characters sent

The first line of the output says which parts it holds and where the others are, so a lane that thinks a rule is missing
can read hub/lane-rules/<part>.md. The key rules block (hub/lane-rules/key-rules-lane.txt) is not pasted here; the
event hook (rules_reminder.py) repeats it every 30 tool calls.

With --rules naming parts, the hub has decided: the lane gets exactly core plus those parts, and the keywords only warn, in one
line of the head comment per part the words would have loaded (the hit words, at most 4).
"""
import argparse
import os
import re
import sys
from pathlib import Path

HOME = Path.home()
DEFAULT_DIR = Path(os.environ.get("LANE_RULES_DIR") or HOME / "repos/hub/lane-rules")
ORDER = ["core", "mail", "browser", "streams", "coop", "fleet"]

# Plain word lists. Matched against lower-case "name + folder + first 30000 characters of the task" (the prompt above
# its "Tools you already have" list, so a tool's description cannot load a part).
TRIGGERS = {
    "mail": r"gmail|e-?mail|inbox|newsletter|listmonk",
    "browser": r"browser|sign[- ]?in|log ?in|login|chromium|playwright|puppeteer|\bcdp\b|cloudflare|captcha|passkey|"
               r"\bapply\b|job form|application form|\bforms? (?:fill|submit|answers)|lever|greenhouse|ashby|rippling|"
               r"upwork|godaddy|stripe|resume|career|cover letter|agent_browser|oauth|2fa|\bsudo\b|collaborator|webauthn|\bsso\b",
    "streams": r"\bstreams?\b|twitch|youtube|\bband\b|wagon|\bsongs?\b|music|lyric|jingle|blender|sketchup|site[- ]model|"
               r"data/sites|photogrammetry|\bobs\b|rtmp|album|ffmpeg|turnaround|poem|\b3d\b",
    "coop": r"co-?op|\bcd-|nextcloud|\bdeck\b|hubzilla|civicrm|talk post",
    "fleet": r"lane\.sh|lane_watch|lane_session|dispatch|openclaw|--engine|\bcodex\b|opencode|\bagy\b|copilot|budget|"
             r"usage_calibrate|claude_commandz|hub_digest|hub_lean",
}
# Client, organisation and people names live in a private file, never in this public repo:
#   ~/.config/afa/lane-rules-keywords.json   {"mail": ["word", ...], "coop": ["word", ...], ...}   (mode 600; LANE_RULES_KEYWORDS overrides the path)
# Each entry is a plain word or a regular-expression fragment. A missing file adds nothing; a bad entry is skipped.
KEYWORDS_FILE = "~/.config/afa/lane-rules-keywords.json"


def load_private_triggers(path=None):
    """The part -> regex of private extra trigger words (only parts in TRIGGERS), or {} when there is no readable file."""
    import json
    where = Path(os.path.expanduser(path or os.environ.get("LANE_RULES_KEYWORDS") or KEYWORDS_FILE))
    try:
        data = json.loads(where.read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for part, words in (data.items() if isinstance(data, dict) else ()):
        if part not in TRIGGERS or not isinstance(words, list):
            continue
        good = []
        for w in words:
            try:
                if isinstance(w, str) and w.strip():
                    re.compile(w)
                    good.append(w)
            except re.error:
                continue
        if good:
            out[part] = "|".join(good)
    return out


def read_part(rules_dir, name):
    try:
        return (rules_dir / f"{name}.md").read_text()
    except OSError:
        return ""


def task_text(prompt_text):
    """The prompt above its first '## Tools you already have for this job' heading (the whole text if there is none)."""
    m = re.search(r"(?im)^## Tools you already have for this job", prompt_text)
    return prompt_text[:m.start()] if m else prompt_text


def pick_hits(name, dirpath, connectors, prompt_text):
    """{part: [matched words, lower case, at most 4]} for every part the keywords would load (core excluded)."""
    text = f"{name}\n{dirpath}\n{task_text(prompt_text)[:30000]}".lower()
    hits = {}
    private = load_private_triggers()
    for part in ORDER[1:]:
        words = []
        for rx in (TRIGGERS[part], private.get(part)):
            if rx:
                for m in re.finditer(rx, text):
                    w = m.group(0).strip().lower()
                    if w and w not in words:
                        words.append(w)
        if words:
            hits[part] = words[:4]
        elif part == "mail" and connectors == "on":
            hits[part] = ["connectors on"]
    return hits


def pick(name, dirpath, connectors, prompt_text):
    hits = pick_hits(name, dirpath, connectors, prompt_text)
    got = ["core"] + [p for p in ORDER[1:] if p in hits]
    if connectors == "on" and "mail" not in got:
        got.insert(1, "mail")
    return got


def parse_rules(spec):
    """'auto', 'all', 'none' or a comma list of part names -> None (auto) or the list of parts to load. Exits 2 on an unknown name."""
    spec = (spec or "auto").strip().lower()
    if spec == "auto":
        return None
    if spec == "all":
        return list(ORDER)
    names = [n.strip() for n in spec.split(",") if n.strip()]
    bad = [n for n in names if n not in ORDER and n != "none"]
    if bad or not names:
        sys.stderr.write("lane_rules_compose: unknown rule part %s; valid: auto, all, none, or a comma list of %s\n"
                         % (", ".join(bad) or repr(spec), ", ".join(ORDER[1:])))
        sys.exit(2)
    return ["core"] + [p for p in ORDER[1:] if p in names]


def compose(parts, rules_dir, warn=None):
    others = [p for p in ORDER if p not in parts]
    head = "<!-- lane rules parts loaded: %s%s -->" % (
        ", ".join(parts),
        ("; not loaded: %s (read ~/repos/hub/lane-rules/<part>.md if a rule of that kind seems missing)" % ", ".join(others))
        if others else "")
    chunks = [head]
    for part, words in (warn or {}).items():
        chunks[0] += "\n<!-- not loaded, but the prompt mentions: %s (%s); read hub/lane-rules/%s.md if the work needs it -->" % (
            part, ", ".join(words), part)
    for p in parts:
        t = read_part(rules_dir, p).rstrip("\n")
        if t:
            chunks.append(t)
    return "\n\n".join(chunks) + "\n"


def routes_for(name, dirpath, prompt_text, routes_file=None):
    """Sections of hub/routes.md whose site words appear in the lane's name, folder or prompt (empty when none or no file)."""
    try:
        raw = Path(routes_file or os.environ.get("HUB_ROUTES_FILE") or HOME / "repos/hub/routes.md").read_text()
    except OSError:
        return ""
    text = f"{name}\n{dirpath}\n{task_text(prompt_text)[:30000]}".lower()
    out = []
    for sec in re.split(r"(?m)^(?=## )", raw):
        m = re.match(r"## (.+)", sec)
        if not m:
            continue
        head = m.group(1)
        main = re.sub(r"\s*\(.*$", "", head).strip()
        words = main.split()
        names = [words[0]] if words else []  # the site's own name is the first word of the main name
        names += [n.strip().strip('"') for n in re.findall(r"\(([^)]*)\)", head) for n in n.split(",")]
        if any(n and re.search(r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])", text) for n in names):
            out.append(sec.rstrip("\n"))
    if not out:
        return ""
    return "## Routes that worked or failed before (hub/routes.md)\n\n" + "\n\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="")
    ap.add_argument("--dir", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--connectors", default="off")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--rules-dir", default=str(DEFAULT_DIR))
    ap.add_argument("--rules", default="auto")
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
    wanted = parse_rules(a.rules)
    pf = Path(a.prompt) if a.prompt else None
    warn = None
    if a.all or (wanted is None and (pf is None or not pf.is_file())):  # no prompt to judge by: every part
        parts = ORDER
    elif wanted is not None:
        parts = wanted
        if pf is not None and pf.is_file():
            hits = pick_hits(a.name, a.dir, a.connectors, pf.read_text(errors="replace"))
            warn = {p: w for p, w in hits.items() if p not in parts}
    else:
        parts = pick(a.name, a.dir, a.connectors, pf.read_text(errors="replace"))
    if not read_part(rules_dir, "core").strip():
        sys.exit("lane_rules_compose: %s/core.md is missing or empty" % rules_dir)
    text = compose(parts, rules_dir, warn)
    if pf is not None and pf.is_file() and not a.all:
        extra = routes_for(a.name, a.dir, pf.read_text(errors="replace"))
        if extra:
            text = text.rstrip("\n") + "\n\n" + extra
    out = Path(a.out)
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, out)
    print(",".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
