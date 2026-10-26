#!/usr/bin/env python3
"""Minute-level log of the owner's OWN working time per client, from their first work to today.

  hours.py report --client NAME [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--machine NAME] [--no-sources] [--rows-in FILE ...]
  hours.py log    --client NAME [--since ..] [--until ..] [--out DIR] [--rows-in FILE ...]   (the sitting-by-sitting log)
  hours.py rows   --client NAME [--since ..] [--until ..] [--machine NAME] --out FILE        (interval rows to send to another machine)
  hours.py week   [--now ISO] [--out DIR] [--if-due]                                           (what the Monday timer runs)
  hours.py unclear --client NAME [--since ..] [--until ..]                                     (the prompts the rules could not place)

Read-only: it reads Claude Code transcripts (~/.claude/projects/*/*.jsonl), the git history of the client's repos, Gmail
sent mail (through gmail_api.py, metadata only) and the call recordings; it never writes to any of them.

What counts as the owner's time (the internship method of 25 Oct, now the standing rule):
  - typed:  only what the owner typed or clicked: prompts in hub/direct sessions plus their answers on question cards.
            Relays, lane starts, re-primes and timers typed into lane windows are the hub's, not theirs.
  - call:   a measured call (transcript length from the recording, or the calendar slot) counts as time spent.
  - mail:   mail the owner sent themselves (Gmail SENT, to the client's addresses).
  - git:    commits by hand (no AI trailer) in the client's repos; before the transcripts begin, every commit.
  Time rule (usage_ledger.py): each event is worth HUMAN_FLOOR seconds, events up to HUMAN_GAP apart bridge, and every
  source is merged into one set of intervals so no minute is counted twice. Each source also keeps its own column.

Which folders, words and addresses mean which client lives in private config, read at run time:
  ~/.config/ledger/clients.json  ->  "hours": {...}   (see load_config; nothing in this file names a client)
"""
import argparse
import collections
import datetime as dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HUMAN_GAP, HUMAN_FLOOR = 15 * 60, 5 * 60          # the ledger's rule (usage_ledger.py)
MAIL_LEAD = 5 * 60                                  # a sent mail was written in the five minutes before it left
CACHE_VERSION = 3

# ---------------------------------------------------------------------------------------------------------------
# Paths: everything hangs off $HOME so a test can point the whole tool at a temp dir
# ---------------------------------------------------------------------------------------------------------------


def home():
    return os.path.expanduser("~")


def projects_dir():
    return os.path.join(home(), ".claude", "projects")


def cache_dir():
    return os.path.join(home(), ".local", "share", "hours", "cache")


def config_path():
    return os.path.join(home(), ".config", "ledger", "clients.json")


def load_config():
    """clients.json["hours"]:
       owner_emails      addresses that are the owner's (also read from "author_emails")
       relay_patterns    extra regexes (anchored at the start of a prompt) for hub relays typed into lane windows
       clients           {key: {label, repos: [globs of repo names], words: [regexes], projects: {name: {repos, words}},
                                mail_to: [addresses or domains], git_repos: [paths], calls: [{dir, slot}]}}
       overrides         {"<sid6>|<Day dd hh:mm AM/PM>": key}  hand-labelled prompts the rules cannot read
       weekly            [client keys the Monday run reports]
    """
    try:
        raw = json.load(open(config_path()))
    except (OSError, ValueError):
        raw = {}
    cfg = dict(raw.get("hours") or {})
    cfg.setdefault("clients", {})
    cfg.setdefault("overrides", {})
    cfg.setdefault("relay_patterns", [])
    cfg.setdefault("weekly", [])
    cfg["owner_emails"] = sorted(set(cfg.get("owner_emails", []) + raw.get("author_emails", [])))
    return cfg


# ---------------------------------------------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------------------------------------------


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def day_start(d):
    """'2026-10-19' -> epoch of that midnight, Eastern."""
    return dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=ET).timestamp()


def window(since, until):
    """Inclusive dates -> [lo, hi) epochs. Missing since = the beginning of time, missing until = tomorrow."""
    lo = day_start(since) if since else 0.0
    hi = day_start(until) + 86400 if until else dt.datetime.now(ET).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + 86400
    return lo, hi


def f12(ts):
    return dt.datetime.fromtimestamp(ts, ET).strftime("%-I:%M %p")


def fday(ts):
    return dt.datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d")


def fdow(ts):
    return dt.datetime.fromtimestamp(ts, ET).strftime("%a %-d %b")


def merge_iv(iv):
    out = []
    for s, e in sorted(iv):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def stream_iv(ts, gap=HUMAN_GAP, floor=HUMAN_FLOOR):
    """The ledger's rule (usage_ledger.stream_intervals): each point is worth `floor` seconds; neighbours <= gap apart bridge."""
    ts = sorted(ts)
    iv = []
    for i, t in enumerate(ts):
        iv.append((t, t + floor))
        if i + 1 < len(ts) and ts[i + 1] - t <= gap:
            iv.append((t, ts[i + 1]))
    return merge_iv(iv)


def secs(iv):
    return sum(e - s for s, e in iv)


def clip(iv, lo, hi):
    return [(max(s, lo), min(e, hi)) for s, e in iv if min(e, hi) > max(s, lo)]


def overlap_secs(iv, s, e):
    return secs(clip(iv, s, e))


def hh(s):
    return "%.1f" % (s / 3600)


def split_days(iv):
    out = collections.defaultdict(float)
    for s, e in iv:
        while s < e:
            d = dt.datetime.fromtimestamp(s, ET)
            nxt = (d.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)).timestamp()
            cut = min(e, nxt)
            out[d.strftime("%Y-%m-%d")] += cut - s
            s = cut
    return out


# ---------------------------------------------------------------------------------------------------------------
# Loading transcripts (read-only), cached per file so a weekly run re-reads only what changed
# ---------------------------------------------------------------------------------------------------------------


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(i.get("text", "") for i in c if isinstance(i, dict) and isinstance(i.get("text"), str))
    return ""


def _asst_text(line):
    try:
        o = json.loads(line)
    except ValueError:
        return ""
    c = (o.get("message") or {}).get("content")
    if not isinstance(c, list):
        return text_of(c)
    return "\n".join(i.get("text", "") for i in c if isinstance(i, dict) and i.get("type") == "text")


def extract_file(path, slug):
    """One transcript -> {"sess": {...}, "recs": [user/ask records]}. Streams the file; parses only the lines it needs."""
    sid = os.path.basename(path)[:-6]
    recs = []
    pending = {}            # tool_use_id -> (ts, question json, previous assistant text) of an AskUserQuestion call
    last_text_line = ""     # raw line of the newest assistant line that carried text
    first_ts = last_ts = None
    cwd0 = None
    nprompt = 0
    with open(path, errors="replace") as fh:
        for line in fh:
            is_user = '"type":"user"' in line
            is_asst = (not is_user) and '"type":"assistant"' in line
            if not (is_user or is_asst):
                continue
            if is_asst:
                if '"type":"text"' in line:
                    last_text_line = line
                if "AskUserQuestion" in line:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    ts = parse_ts(o.get("timestamp"))
                    for i in (o.get("message") or {}).get("content") or []:
                        if isinstance(i, dict) and i.get("type") == "tool_use" and i.get("name") == "AskUserQuestion" and ts:
                            pending[i.get("id")] = (ts, json.dumps(i.get("input"))[:700], _asst_text(last_text_line)[-400:])
                continue
            if '"tool_result"' in line:
                hit = [t for t in pending if t in line]
                if not hit:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                ts = parse_ts(o.get("timestamp"))
                for i in (o.get("message") or {}).get("content") or []:
                    if isinstance(i, dict) and i.get("type") == "tool_result" and i.get("tool_use_id") in pending and ts:
                        a_ts, a_q, a_prev = pending.pop(i["tool_use_id"])
                        res = i.get("content")
                        rt = res if isinstance(res, str) else text_of(res)
                        recs.append({"k": "ask", "sid": sid, "slug": slug, "cwd": o.get("cwd"), "ts": ts, "asked": a_ts,
                                     "q": a_q, "prev": a_prev, "err": bool(i.get("is_error")), "text": rt[:400]})
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("isSidechain"):
                continue
            ts = parse_ts(o.get("timestamp"))
            if ts is None:
                continue
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
            cwd0 = cwd0 or o.get("cwd")
            c = (o.get("message") or {}).get("content")
            og = o.get("origin")
            t = text_of(c)
            nprompt += 1
            recs.append({"k": "user", "sid": sid, "slug": slug, "cwd": o.get("cwd"), "ts": ts, "entry": o.get("entrypoint"),
                         "origin": og.get("kind") if isinstance(og, dict) else None, "psrc": o.get("promptSource"),
                         "meta": bool(o.get("isMeta")), "compact": bool(o.get("isCompactSummary")),
                         "text": t[:1500], "len": len(t), "prev": _asst_text(last_text_line)[-500:] if last_text_line else ""})
    sess = {"k": "sess", "sid": sid, "slug": slug, "cwd": cwd0, "first": first_ts, "last": last_ts, "prompts": nprompt}
    return {"sess": sess, "recs": recs}


def load_records(since_ts=0.0, progress=None):
    """All records from transcripts modified on or after since_ts. Per-file cache keyed by size and mtime."""
    cdir = cache_dir()
    os.makedirs(cdir, mode=0o700, exist_ok=True)
    recs, sessions = [], []
    n = 0
    for slug in sorted(os.listdir(projects_dir())):
        for f in glob.glob(os.path.join(projects_dir(), slug, "*.jsonl")):
            try:
                st = os.stat(f)
            except OSError:
                continue
            if st.st_mtime < since_ts:
                continue
            key = hashlib.sha1(f.encode()).hexdigest()
            cp = os.path.join(cdir, key + ".json")
            stamp = [CACHE_VERSION, st.st_size, int(st.st_mtime)]
            got = None
            try:
                c = json.load(open(cp))
                if c.get("stamp") == stamp:
                    got = c
            except (OSError, ValueError):
                pass
            if got is None:
                x = extract_file(f, slug)
                got = {"stamp": stamp, "sess": x["sess"], "recs": x["recs"]}
                tmp = cp + ".tmp%d" % os.getpid()
                with open(tmp, "w") as out:
                    json.dump(got, out)
                os.replace(tmp, cp)
            sessions.append(got["sess"])
            recs += got["recs"]
            n += 1
            if progress and n % 200 == 0:
                progress(n)
    return recs, sessions


# ---------------------------------------------------------------------------------------------------------------
# Whose prompt is it? (port of the internship method, hours_human.py of 25 Oct)
# ---------------------------------------------------------------------------------------------------------------
NOISE = ("<command-name>", "<local-command", "<bash-", "[Request interrupted", "Caveat:", "<task-notification",
         "<system-reminder", "[SYSTEM NOTIFICATION", "This session is being continued")
# What the hub types into a lane window (a re-prime, a recycle notice, a relay). Any of them may follow a "<pasted_content>" wrapper
# (tmux paste) or a "> " quote. Relays that quote the owner by name ("<Name> says ...") are added from config["relay_patterns"].
RELAY_BASE = (r"Hub\b", r"From the hub", r"New (small )?(job|task|co-op mail|hub task)", r"Follow-up from the hub",
              r"PAUSED by ")
# a lane's first prompt (prompt.md as lane.sh passes it to claude) or a hub-typed prompt that starts a lane
LANE_INIT = re.compile(r"^(<pasted_content[^>]*>\s*)?(You are .{0,60}?\blane\b|# (Lane|Absorb lane|Restart|Test lane|[A-Za-z0-9_.-]+[: ])|"
                       r"Reply with the single word|Call the AskUserQuestion tool|> Hub)")
HOUSE_CMDS = ("/clear", "/model", "/effort", "/exit")


def relay_re(cfg):
    pats = list(RELAY_BASE) + list(cfg.get("relay_patterns", []))
    return re.compile(r"^(<pasted_content[^>]*>\s*)?(>\s*)?(" + "|".join(pats) + ")", re.I)


def cmdname(t):
    m = re.match(r"<command-name>(/[^<]+)</command-name>", t)
    return m.group(1) if m else None


def substantive(r):
    t = r["text"].lstrip()
    return not (r["meta"] or r["compact"] or t.startswith(NOISE))


def classify_sessions(users, relay):
    """lane   = first two substantive prompts are a lane initial prompt, a hub re-prime or a hub relay.
       hub    = first substantive prompt is a typed slash command (/prep, /session-prime).
       direct = anything else typed (a person at a window with no lane markers). empty = nothing substantive."""
    by = collections.defaultdict(list)
    for r in users:
        by[r["sid"]].append(r)
    cls = {}
    for sid, l in by.items():
        l.sort(key=lambda r: r["ts"])
        subs = [r for r in l if substantive(r)][:3]
        if not subs:
            cls[sid] = "empty"
        elif any(LANE_INIT.match(r["text"].lstrip()) or relay.match(r["text"].lstrip()) for r in subs[:2]):
            cls[sid] = "lane"
        elif subs[0]["text"].lstrip().startswith("<command-message>"):
            cls[sid] = "hub"
        else:
            cls[sid] = "direct"
    return cls


def owner_prompts(users, cls, relay):
    """The records that are the owner's own typed prompts, and a Counter of why the others were left out."""
    ex = collections.Counter()
    J = []
    for r in users:
        t = r["text"].lstrip()
        if r["entry"] != "cli":
            ex["sdk/headless session"] += 1
        elif r["compact"]:
            ex["compaction summary"] += 1
        elif r["meta"]:
            ex["meta/injected (isMeta)"] += 1
        elif t.startswith("<task-notification"):
            ex["task-notification"] += 1
        elif t.startswith(("<system-reminder", "[SYSTEM NOTIFICATION")):
            ex["system notice"] += 1
        elif t.startswith("This session is being continued"):
            ex["continued-session summary"] += 1
        elif cmdname(t) in HOUSE_CMDS:
            ex["housekeeping command"] += 1
        elif t.startswith(("<local-command", "<bash-", "[Request interrupted", "Caveat:", "<command-name>")):
            ex["local-command output / bash echo / interrupt marker"] += 1
        elif cls.get(r["sid"]) == "lane":
            ex["lane window: typed by the hub, not the owner"] += 1
        elif relay.match(t) or LANE_INIT.match(t):
            ex["hub/direct session: relay or lane-start text"] += 1
        else:
            J.append(dict(r, sclass=cls.get(r["sid"], "empty")))
    return J, ex


# ---------------------------------------------------------------------------------------------------------------
# Which client? By rule: the folder the session ran in, then the words in the prompt, then the message just before a short reply.
# ---------------------------------------------------------------------------------------------------------------
SCRATCH_RE = re.compile(r"^/tmp/claude-\d+/-[^/]*?-repos-([^/]+)/")
UUID_TAIL = re.compile(r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def repo_names(cwd):
    """The repo name(s) a working folder belongs to. Worktrees fold into their parent; ~/repos itself (the hub) is none."""
    if not cwd:
        return []
    m = SCRATCH_RE.match(cwd + "/")
    if m:
        return [UUID_TAIL.sub("", m.group(1))]
    root = os.path.join(home(), "repos") + "/"
    if not cwd.startswith(root):
        return []
    parts = cwd[len(root):].split("/")
    if parts[0] == ".worktrees" and len(parts) > 1:
        return [re.sub(r"-wf-[A-Za-z0-9]+$", "", parts[1].split("--")[0])]
    if parts[0] == "CD" and len(parts) > 1:
        return [parts[1], "CD"]
    return [re.sub(r"-wf-[A-Za-z0-9]+$", "", parts[0])] if parts[0] else []


def repo_hit(client, names):
    import fnmatch
    return any(fnmatch.fnmatchcase(n, g) for n in names for g in client.get("repos", []))


def words_hit(client, text):
    return any(re.search(w, text, re.I) for w in client.get("words", []))


def project_of(client, names, text):
    for pname, p in (client.get("projects") or {}).items():
        if repo_hit(p, names):
            return pname
    for pname, p in (client.get("projects") or {}).items():
        if words_hit(p, text):
            return pname
    return client.get("default_project", "general")


def ov_keys(ts, sid):
    d = dt.datetime.fromtimestamp(ts, ET)
    return sid[:6] + "|" + d.strftime("%a %d %I:%M:%S %p"), sid[:6] + "|" + d.strftime("%a %d %I:%M %p")


def label_event(cfg, r, ask=False):
    """-> {"clients": [...], "weak": [...], "why": str, "project": {client: project}}.
       clients = counted; weak = placed only by the message before a short reply (listed as unclear, not counted)."""
    clients = cfg["clients"]
    names = repo_names(r.get("cwd"))
    text = (r["q"] + " " + r["text"]) if ask else r["text"]
    out = {"clients": [], "weak": [], "why": "", "project": {}}
    ov = None
    for k in ov_keys(r["ts"], r["sid"]):
        ov = ov or cfg["overrides"].get(k)
    if ov:
        out["why"] = "hand label"
        if ov == "unclear":
            out["why"] = "hand-labelled unclear"
        elif ov != "other":
            out["clients"] = [c for c in ov.split("+") if c in clients]
    else:
        carded = False
        if ask:
            for lab, needles in cfg.get("ask_patterns", []):
                if any(n.lower() in r["q"].lower() for n in needles):
                    out["clients"] = [c for c in lab.split("+") if c in clients]
                    out["why"] = "question card text" if out["clients"] else "question card text (unclear)"
                    carded = True
                    break
        if not carded:
            hit = [k for k, c in clients.items() if repo_hit(c, names)]
            if hit:
                out["clients"], out["why"] = hit, "folder the session ran in"
            else:
                hit = [k for k, c in clients.items() if words_hit(c, text)]
                if hit:
                    out["clients"], out["why"] = hit, ("words in the prompt" if len(hit) == 1 else "words name several clients")
                elif len(text.strip()) < 80 and r.get("prev"):
                    hit = [k for k, c in clients.items() if words_hit(c, r["prev"])]
                    if hit:
                        out["weak"], out["why"] = hit, "short reply; only the message before it names the client"
    for k in out["clients"] + out["weak"]:
        out["project"][k] = project_of(clients[k], names, text + " " + (r.get("prev") or ""))
    return out


def snippet(r, ask=False, n=90):
    t = (r["q"] if ask else r["text"]).strip()
    m = re.match(r"<command-name>(/[^<]+)</command-name>", t)
    if m:
        a = re.search(r"<command-args>(.*?)</command-args>", t, re.S)
        t = m.group(1) + (" " + a.group(1) if a else "")
    elif ask:
        t = "answered a question card: " + (re.search(r'"question": "([^"]{0,80})', t) or [0, ""])[1]
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[: n - 1] + "…") if len(t) > n else t


def build_events(cfg, recs, lo, hi, machine=""):
    """The owner's typed prompts and card answers in [lo, hi), each labelled by rule. Also returns the session classes."""
    relay = relay_re(cfg)
    users = [r for r in recs if r["k"] == "user"]
    asks = [r for r in recs if r["k"] == "ask"]
    cls = classify_sessions([r for r in users if r["entry"] == "cli"], relay)
    J, ex = owner_prompts([r for r in users if lo <= r["ts"] < hi], cls, relay)
    ev, seen, dropped = [], [], 0
    for r in sorted(J, key=lambda r: r["ts"]):
        # the same words in two sessions within 3 seconds are one keystroke burst (the hub resumed in two panes)
        norm = re.sub(r"\s+", " ", r["text"]).strip()[:200]
        if any(abs(r["ts"] - t) <= 3 and n == norm and s != r["sid"] for t, n, s in seen):
            dropped += 1
            continue
        seen.append((r["ts"], norm, r["sid"]))
        kind = "slash" if r["text"].lstrip().startswith("<command-message>") else \
            ("suggestion" if r["psrc"] == "suggestion_accepted" else ("queued" if r["psrc"] == "queued" else "typed"))
        lab = label_event(cfg, r)
        ev.append(dict(lab, ts=r["ts"], sid=r["sid"], src="typed", kind=kind, what=snippet(r), machine=machine, cwd=r.get("cwd")))
    cards = collections.Counter()
    for a in sorted((a for a in asks if lo <= a["ts"] < hi), key=lambda a: a["ts"]):
        if cls.get(a["sid"]) == "lane":
            cards["in a lane window (not counted)"] += 1
            continue
        lab = label_event(cfg, a, ask=True)
        cards["declined" if a["err"] else "answered"] += 1
        ev.append(dict(lab, ts=a["ts"], sid=a["sid"], src="typed", kind="card_declined" if a["err"] else "card", what=snippet(a, ask=True),
                       machine=machine, cwd=a.get("cwd")))
    return ev, cls, {"excluded": ex, "dropped_dupes": dropped, "cards": cards}


# ---------------------------------------------------------------------------------------------------------------
# Intervals: every source makes "raw" intervals {s, e, src, project, what, evid}; typed ones come from the ledger rule.
# ---------------------------------------------------------------------------------------------------------------
PRIO = {"call": 0, "typed": 1, "mail": 2, "git": 3}      # who gets the label where sources overlap; the minute still counts once
SRC_WORD = {"typed": "typed", "call": "call", "mail": "mail sent", "git": "commit by hand"}


def raw(s, e, src, project, what, evid):
    return {"s": s, "e": e, "src": src, "project": project, "what": what, "evid": evid}


def typed_raws(events, client):
    """The owner's typed prompts and card answers for one client: per session, the ledger rule; sessions overlap freely here."""
    by = collections.defaultdict(list)
    for e in events:
        if client in e["clients"]:
            by[e["sid"]].append(e)
    out = []
    for sid, evs in by.items():
        evs.sort(key=lambda x: x["ts"])
        ts = [x["ts"] for x in evs]
        for s, e in stream_iv(ts):
            inside = [x for x in evs if s <= x["ts"] < e]
            proj = collections.Counter(x["project"][client] for x in inside).most_common(1)[0][0]
            out.append(raw(s, e, "typed", proj, inside[0]["what"], "%d typed, session %s" % (len(inside), sid[:6])))
    return out


def point_raws(points, src):
    """points = [(ts, project, what, evid)] -> raws by the same rule (a point is worth 5 min; points <= 15 min apart bridge)."""
    pts = sorted(points)
    out, i = [], 0
    for s, e in stream_iv([p[0] for p in pts]):
        inside = [p for p in pts if s <= p[0] < e]
        proj = collections.Counter(p[1] for p in inside).most_common(1)[0][0]
        out.append(raw(s, e, src, proj, inside[0][2], "%d %s" % (len(inside), "; ".join(sorted({p[3] for p in inside}))[:60])))
    return out


def attribute(raws):
    """Sweep the raws into disjoint segments; where sources overlap the highest-priority one is the label, and the segment also
       records which sources covered it. The total of the segments is the time, with no minute counted twice."""
    cuts = sorted({x for r in raws for x in (r["s"], r["e"])})
    if not cuts:
        return []
    starts = sorted(raws, key=lambda r: r["s"])
    active, i, segs = [], 0, []
    for a, b in zip(cuts, cuts[1:]):
        while i < len(starts) and starts[i]["s"] <= a:
            active.append(starts[i])
            i += 1
        active = [r for r in active if r["e"] > a]
        if not active:
            continue
        best = min(active, key=lambda r: (PRIO[r["src"]], r["s"]))
        seg = dict(best, s=a, e=b, covered=sorted({r["src"] for r in active}))
        if segs and segs[-1]["e"] == a and segs[-1]["evid"] == seg["evid"] and segs[-1]["src"] == seg["src"] \
                and segs[-1]["what"] == seg["what"] and segs[-1]["covered"] == seg["covered"]:
            segs[-1]["e"] = b
        else:
            segs.append(seg)
    return segs


def sittings(segs, join=HUMAN_GAP):
    """Chains of segments with gaps up to `join` seconds between them. Minutes = covered seconds, not end - start."""
    out = []
    for g in sorted(segs, key=lambda g: g["s"]):
        if out and g["s"] - out[-1]["e"] <= join:
            out[-1]["segs"].append(g)
            out[-1]["e"] = max(out[-1]["e"], g["e"])
        else:
            out.append({"s": g["s"], "e": g["e"], "segs": [g]})
    return out


# ---------------------------------------------------------------------------------------------------------------
# Non-typing time: calls, mail the owner sent, commits by hand
# ---------------------------------------------------------------------------------------------------------------
def srt_length(path):
    """End of the last cue of a .srt, in seconds; None if unreadable."""
    last = None
    try:
        for line in open(path, errors="replace"):
            m = re.search(r"-->\s*(\d+):(\d\d):(\d\d)[,.](\d+)", line)
            if m:
                last = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + float("0." + m.group(4))
    except OSError:
        return None
    return last


def calendar_rows(cfg):
    """<out_dir>/calendar-events.tsv (filled from Google Calendar): start_iso, end_iso, title, client."""
    p = os.path.join(out_dir(cfg), "calendar-events.tsv")
    rows = []
    try:
        for line in open(p):
            f = line.rstrip("\n").split("\t")
            if len(f) >= 4 and not f[0].startswith(("start", "#")):
                s, e = parse_ts(f[0]), parse_ts(f[1])
                if s and e and e > s:
                    rows.append((s, e, f[2], f[3]))
    except OSError:
        pass
    return rows


def call_raws(cfg, key, lo, hi):
    """Calls with a recording folder (length measured from the .srt) and calendar events. A recording's start is the calendar event
       of that day if there is one, else the configured weekly slot; a calendar event with no recording counts for its own length."""
    c = cfg["clients"][key]
    cal = [r for r in calendar_rows(cfg) if r[3] == key]
    out, used = [], set()
    for spec in c.get("calls", []):
        base = os.path.expanduser(spec["dir"])
        slot = re.match(r"(\w+) (\d+):(\d\d)-(\d+):(\d\d)", spec.get("slot", ""))
        for d in sorted(glob.glob(os.path.join(base, "????-??-??-*"))):
            m = re.match(r"(\d{4}-\d\d-\d\d)-(.*)", os.path.basename(d))
            n = srt_length(os.path.join(d, "transcript.srt"))
            if not m or not n:
                continue
            day0 = day_start(m.group(1))
            ev = [r for r in cal if day0 <= r[0] < day0 + 86400]
            if ev:
                s, how = min(ev)[0], "start from the calendar event"
                used.add(min(ev))
            elif slot:
                s, how = day0 + int(slot.group(2)) * 3600 + int(slot.group(3)) * 60, "start assumed from the weekly %s slot" % slot.group(1)
                if dt.datetime.fromtimestamp(day0, ET).strftime("%a")[:3] != slot.group(1)[:3]:
                    how += " (this call was on another weekday, so its time of day is unverified)"
            else:
                continue
            if lo <= s < hi:
                out.append(raw(s, s + n, "call", spec.get("project", "general"), "call: " + m.group(2).replace("-", " "),
                               "recording %d:%02d, %s" % (n // 60, n % 60, how)))
    for r in cal:
        if r not in used and lo <= r[0] < hi:
            out.append(raw(r[0], r[1], "call", c.get("calls", [{}])[0].get("project", "general") if c.get("calls") else "general",
                           "call: " + r[2], "calendar event %d min" % round((r[1] - r[0]) / 60)))
    return out


MSG_RE = re.compile(r"^MSG (\S+) \| (.*?) \| (.*)$")


def sent_mail(cfg, key, lo, hi):
    """[(ts, subject, id)] sent by the owner to the client's addresses. Gmail through gmail_api.py (metadata only)."""
    c = cfg["clients"][key]
    to = c.get("mail_to", [])
    if not to:
        return []
    tool = os.environ.get("HOURS_GMAIL") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "gmail_api.py")
    cmd = [tool] if os.environ.get("HOURS_GMAIL") else [sys.executable, tool]
    seen, out = {}, []
    t = lo if lo > 0 else day_start("2026-01-01")
    while t < hi:
        t2 = min(t + 30 * 86400, hi)
        q = "in:sent to:(%s) after:%s before:%s" % (" OR ".join(to), dt.datetime.fromtimestamp(t, ET).strftime("%Y/%m/%d"),
                                                      dt.datetime.fromtimestamp(t2 + 86400, ET).strftime("%Y/%m/%d"))
        try:
            res = subprocess.run(cmd + ["messages-list", q, "--limit", "500"], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            break
        for line in res.stdout.splitlines():
            m = MSG_RE.match(line)
            if not m:
                continue
            f = m.group(3).split(" | ")
            if len(f) < 5 or "SENT" not in f[-2].split(","):
                continue
            try:
                ts = email_utils_parse(f[-3])
            except Exception:
                continue
            if lo <= ts < hi and m.group(1) not in seen:
                seen[m.group(1)] = 1
                out.append((ts, " | ".join(f[1:-3]), m.group(1)))
        t = t2
    return out


def email_utils_parse(s):
    from email.utils import parsedate_to_datetime
    return parsedate_to_datetime(s).timestamp()


def mail_raws(cfg, key, lo, hi):
    """A mail was written in the five minutes before it left: [sent - 5 min, sent]. Mails close together bridge like prompts."""
    c = cfg["clients"][key]
    out = []
    for ts, subj, mid in sent_mail(cfg, key, lo, hi):
        out.append(raw(ts - MAIL_LEAD, ts, "mail", project_of(c, [], subj), "mail: " + subj[:70], "sent %s, id %s" % (f12(ts), mid[:8])))
    return out


def git_raws(cfg, key, lo, hi, first_transcript):
    """Commits by the owner: by hand (no AI trailer) always; before the transcripts begin, every commit (labelled git only)."""
    c = cfg["clients"][key]
    emails = {e.lower() for e in cfg["owner_emails"]}
    pts, seen = [], set()
    paths = []
    for g in c.get("git_repos", []):
        paths += sorted(glob.glob(os.path.expanduser(g)))
    for path in paths:
        if not os.path.isdir(path):
            continue
        try:
            res = subprocess.run(["git", "-C", path, "log", "--all", "--no-merges", "--format=%H%x1f%at%x1f%ae%x1f%s%x1f%B%x1e"],
                                 capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            continue
        name = os.path.basename(path.rstrip("/"))
        for rec in res.stdout.split("\x1e"):
            f = rec.strip("\n").split("\x1f")
            if len(f) < 5 or f[0] in seen or f[2].lower() not in emails:
                continue
            ts = float(f[1])
            if not lo <= ts < hi:
                continue
            ai = re.search(r"co-authored-by:|generated with \[?claude", f[4], re.I)
            if ai and ts >= first_transcript:
                continue
            seen.add(f[0])
            tag = name if not ai else name + ", AI-assisted, no transcript"
            if ts < first_transcript:
                tag += ", git only"
            pts.append((ts, project_of(c, [name], f[3]), "commit: " + f[3][:70], tag))
    return point_raws(pts, "git")


def out_dir(cfg):
    return os.path.expanduser(cfg.get("out_dir", "~/.local/share/hours/out"))


# ---------------------------------------------------------------------------------------------------------------
# One analysis per client: events -> raw intervals per source -> disjoint segments
# ---------------------------------------------------------------------------------------------------------------
def machine_name():
    try:
        return open(os.path.join(home(), ".config", "afa", "machine")).read().strip()
    except OSError:
        return ""


def read_rows_in(paths):
    """Rows another machine exported with `rows`: machine, client, project, src, start, end, what, evid (tab separated)."""
    out = collections.defaultdict(list)
    for p in paths:
        for line in open(p):
            f = line.rstrip("\n").split("\t")
            if len(f) >= 8 and f[0] != "machine":
                out[f[1]].append(raw(float(f[4]), float(f[5]), f[3], f[2], f[6], "%s: %s" % (f[0], f[7])))
    return out


def coverage(cfg, key, sessions, cls, counted_sids, lo, hi):
    """Every session on disk whose folder belongs to this client: counted, or why not."""
    c = cfg["clients"][key]
    rows = []
    for s in sessions:
        if not (s["first"] and s["last"]) or s["last"] < lo or s["first"] >= hi:
            continue
        names = repo_names(s["cwd"])
        if not repo_hit(c, names):
            continue
        sid = s["sid"]
        if sid in counted_sids:
            status = "counted"
        elif cls.get(sid) == "lane":
            status = "machine-only: a lane window; the hub typed every prompt, none is the owner's"
        elif sid not in cls:
            status = "machine-only: headless run, no prompt typed at a window"
        elif cls.get(sid) == "empty":
            status = "no prompt: opened and closed, or only notices"
        else:
            status = "no typed prompt of the owner's in the window (housekeeping, relays or another client's label)"
        rows.append((s["first"], sid, names[0] if names else "", s["prompts"], status))
    return sorted(rows)


def analyze(cfg, keys, lo, hi, machine="", sources=True, rows_in=()):
    recs, sessions = load_records(0.0)
    events, cls, info = build_events(cfg, recs, lo, hi, machine)
    first_tr = min([s["first"] for s in sessions if s["first"]] or [0])
    extra = read_rows_in(rows_in)
    res = {"events": events, "info": info, "cls": cls, "first_transcript": first_tr, "clients": {}, "machine": machine}
    for key in keys:
        counted = [e for e in events if key in e["clients"]]
        weak = [e for e in events if key in e["weak"]]
        raws = typed_raws(events, key)
        if sources:
            raws += call_raws(cfg, key, lo, hi) + mail_raws(cfg, key, lo, hi) + git_raws(cfg, key, lo, hi, first_tr)
        raws += [r for r in extra.get(key, []) if lo <= r["s"] < hi]
        by_src = {s: merge_iv([(r["s"], r["e"]) for r in raws if r["src"] == s]) for s in PRIO}
        segs = attribute(raws)
        weak_iv = merge_iv([(r["s"], r["e"]) for r in typed_raws([dict(e, clients=[key], project={key: e["project"].get(key, "general")}) for e in weak], key)])
        res["clients"][key] = {
            "counted": counted, "weak": weak, "raws": raws, "by_src": by_src, "segs": segs,
            "weak_extra": secs(merge_iv([(g["s"], g["e"]) for g in segs] + weak_iv)) - secs([(g["s"], g["e"]) for g in segs]),
            "coverage": coverage(cfg, key, sessions, cls, {e["sid"] for e in counted}, lo, hi),
        }
    return res


def ksum(res_k):
    segs = res_k["segs"]
    total = sum(g["e"] - g["s"] for g in segs)
    typed = secs(res_k["by_src"]["typed"])
    return {"total": total, "typed": typed, "other": total - sum(g["e"] - g["s"] for g in segs if g["src"] == "typed"),
            "src": {s: secs(iv) for s, iv in res_k["by_src"].items()}}


def per_day(segs):
    d = collections.defaultdict(float)
    for g in segs:
        for k, v in split_days([(g["s"], g["e"])]).items():
            d[k] += v
    return d


def week_of(day):
    d = dt.datetime.strptime(day, "%Y-%m-%d")
    return (d - dt.timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def sit_row(st):
    segs = st["segs"]
    tot = sum(g["e"] - g["s"] for g in segs)
    proj = collections.Counter()
    what = collections.Counter()
    bysrc = collections.Counter()
    for g in segs:
        proj[g["project"]] += g["e"] - g["s"]
        what[g["what"]] += g["e"] - g["s"]
        bysrc[g["src"]] += g["e"] - g["s"]
    ps = [p for p, v in proj.most_common() if v >= 0.2 * tot] or [proj.most_common(1)[0][0]]
    ws = [w for w, _ in what.most_common(3)]
    srcs = ", ".join("%s %d min" % (SRC_WORD[s], round(bysrc[s] / 60)) for s in PRIO if bysrc[s])
    evid = sorted({g["evid"] for g in segs})
    s, e = st["s"], st["e"]
    endtxt = f12(e) if fday(e - 1) == fday(s) else "%s (%s)" % (f12(e), dt.datetime.fromtimestamp(e - 1, ET).strftime("%a %-d %b"))
    return {"date": fday(s), "dow": fdow(s), "start": f12(s), "end": endtxt, "minutes": round(tot / 60), "secs": tot,
            "project": " + ".join(ps), "what": " / ".join(ws), "source": srcs, "evidence": "; ".join(evid)[:240]}


# ---------------------------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------------------------
def report_text(cfg, res, key, lo, hi, join_min=30):
    label = cfg["clients"][key].get("label", key)
    r = res["clients"][key]
    sm = ksum(r)
    out = []
    P = out.append
    P("== %s" % label)
    P("   directed hours, typed only (the ledger rule, per session then union; the internship method): %s h  (%d min)"
      % (hh(sm["typed"]), round(sm["typed"] / 60)))
    P("   all sources merged, no minute counted twice: %s h  (%d min)" % (hh(sm["total"]), round(sm["total"] / 60)))
    P("   by source, each on its own: " + ", ".join("%s %s h" % (SRC_WORD[s], hh(v)) for s, v in sm["src"].items())
      + " ; added beyond typed time: %s h" % hh(sm["total"] - sm["typed"]))
    kinds = collections.Counter(e["kind"] for e in r["counted"])
    P("   his typed prompts and card answers: %d (%s)" % (len(r["counted"]), dict(kinds)))
    days = per_day(r["segs"])
    P("   hours by day: " + ", ".join("%s %s" % (dt.datetime.strptime(d, "%Y-%m-%d").strftime("%a %-d %b"), hh(v)) for d, v in sorted(days.items())))
    sit = sittings(r["segs"], join_min * 60)
    P("   sittings (joined when <= %d min apart): %d" % (join_min, len(sit)))
    for st in sit[:80]:
        row = sit_row(st)
        P("     %s  %s to %s  (%d min)  %s" % (row["dow"], row["start"], row["end"], row["minutes"], row["source"]))
    if len(sit) > 80:
        P("     ... %d more (use `log` for all)" % (len(sit) - 80))
    P("   unclear, not counted: %d prompts that only the message before them places here; they would add %s h"
      % (len(r["weak"]), hh(r["weak_extra"])))
    return "\n".join(out)


def week_start_ts(ts):
    d = dt.datetime.fromtimestamp(ts, ET)
    return (d.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=d.weekday())).timestamp()


TSV_COLS = ["date", "start", "end", "minutes", "project", "what", "source", "evidence", "machine"]


def log_files(cfg, res, key, lo, hi, machine, outdir):
    """<log_name>-time-log.md and .tsv (private: the output folder is not in a public repo)."""
    r = res["clients"][key]
    label = cfg["clients"][key].get("label", key)
    rows = [sit_row(st) for st in sittings(r["segs"])]
    slug = cfg["clients"][key].get("log_name", key)
    os.makedirs(outdir, exist_ok=True)
    tsv = os.path.join(outdir, slug + "-time-log.tsv")
    with open(tsv, "w") as fh:
        fh.write("\t".join(TSV_COLS) + "\n")
        for w in rows:
            fh.write("\t".join(str(w[c]).replace("\t", " ") if c != "machine" else machine for c in TSV_COLS) + "\n")
    sm = ksum(r)
    days = per_day(r["segs"])
    weeks = collections.defaultdict(lambda: collections.defaultdict(float))
    projs = collections.defaultdict(float)
    for g in r["segs"]:
        for d, v in split_days([(g["s"], g["e"])]).items():
            weeks[week_of(d)]["total"] += v
            weeks[week_of(d)][g["src"]] += v
        projs[g["project"]] += g["e"] - g["s"]
    md = []
    P = md.append
    P("# %s: every sitting, minute by minute" % label)
    P("")
    P("Private (hub only). Built %s by hours.py from %s to %s. The count is the owner's own time and does not depend on what anyone pays." % (
        dt.datetime.now(ET).strftime("%a %-d %b %Y, %-I:%M %p"), fday(lo) if lo else "the first record", fday(hi - 1)))
    P("")
    P("**Total: %s h (%d min) over %d sittings and %d days.** Typed alone: %s h. Calls, mail and commits by hand add %s h." % (
        hh(sm["total"]), round(sm["total"] / 60), len(rows), len(days), hh(sm["typed"]), hh(sm["total"] - sm["typed"])))
    P("")
    P("How a minute is counted: each prompt he typed (or card he answered) is worth 5 minutes, prompts up to 15 minutes apart join into one "
      "run, and every source below is merged so no minute counts twice (the ledger's rule, as used for the internship hours of 25 Oct). "
      "Hub relays and lane windows are never his typing. Transcripts begin %s; before that a row's source says git, mail or call. "
      "Minutes in a row are covered minutes, so end minus start can be larger." % fday(res["first_transcript"]))
    P("")
    P("## Totals per week (Monday start, Eastern)")
    P("")
    P("| week of | hours | minutes | typed | calls | mail sent | commits by hand |")
    P("|---|---|---|---|---|---|---|")
    for w in sorted(weeks):
        v = weeks[w]
        P("| %s | %s | %d | %d | %d | %d | %d |" % (w, hh(v["total"]), round(v["total"] / 60), round(v["typed"] / 60), round(v["call"] / 60),
                                                    round(v["mail"] / 60), round(v["git"] / 60)))
    P("")
    P("Columns after minutes are minutes labelled by the highest-priority source covering them (call, typed, mail, commit); they add up to the total.")
    P("")
    P("## Totals per project")
    P("")
    P("| project | hours | minutes |")
    P("|---|---|---|")
    for p, v in sorted(projs.items(), key=lambda x: -x[1]):
        P("| %s | %s | %d |" % (p, hh(v), round(v / 60)))
    P("")
    P("## The sittings")
    P("")
    P("| date | start | end | min | project | what | source |")
    P("|---|---|---|---|---|---|---|")
    for w in rows:
        P("| %s | %s | %s | %d | %s | %s | %s |" % (w["dow"] + " " + w["date"][:4], w["start"], w["end"], w["minutes"], w["project"],
                                                    w["what"].replace("|", "/"), w["source"]))
    P("")
    P("## Unclear: not counted, here so you can decide")
    P("")
    P("Short replies that only the message before them places here. Counting them would add %s h." % hh(r["weak_extra"]))
    P("")
    for e in sorted(r["weak"], key=lambda e: e["ts"]):
        P("- %s %s, session %s: %s (%s)" % (fdow(e["ts"]), f12(e["ts"]), e["sid"][:6], e["what"], e["why"]))
    P("")
    cov = r["coverage"]
    cnt = collections.Counter(c[4].split(":")[0] if c[4].startswith("machine-only") else c[4] for c in cov)
    P("## Every session on disk in this client's folders (%d)" % len(cov))
    P("")
    for k, v in cnt.most_common():
        P("- %d: %s" % (v, k))
    P("")
    P("| started | session | folder | prompts | not counted because |")
    P("|---|---|---|---|---|")
    for first, sid, repo, n, status in cov:
        if status != "counted":
            P("| %s %s | %s | %s | %d | %s |" % (fdow(first), f12(first), sid[:6], repo, n, status))
    mdp = os.path.join(outdir, slug + "-time-log.md")
    with open(mdp, "w") as fh:
        fh.write("\n".join(md) + "\n")
    return mdp, tsv, rows


def unclear_text(cfg, res, key):
    r = res["clients"][key]
    out = ["Unclear for %s: %d prompts, not counted (they would add %s h)" % (cfg["clients"][key].get("label", key), len(r["weak"]), hh(r["weak_extra"]))]
    for e in sorted(r["weak"], key=lambda e: e["ts"]):
        out.append("  %s %s  %s  %s  [%s]" % (fdow(e["ts"]), f12(e["ts"]), e["sid"][:6], e["what"], e["why"]))
    cov = [c for c in r["coverage"] if c[4] != "counted"]
    out.append("Sessions in its folders not counted: %d" % len(cov))
    for first, sid, repo, n, status in cov:
        out.append("  %s %s  %s  %s  %d prompts  %s" % (fdow(first), f12(first), sid[:6], repo, n, status))
    return "\n".join(out)


def rows_file(res, keys, machine, path, all_sources=False):
    with open(path, "w") as fh:
        fh.write("\t".join(["machine", "client", "project", "src", "start", "end", "what", "evid"]) + "\n")
        for k in keys:
            for x in res["clients"][k]["raws"]:
                if all_sources or x["src"] in ("typed", "git"):
                    fh.write("\t".join([machine, k, x["project"], x["src"], "%.0f" % x["s"], "%.0f" % x["e"],
                                        x["what"].replace("\t", " "), x["evid"].replace("\t", " ")]) + "\n")


# ---------------------------------------------------------------------------------------------------------------
# The Monday run
# ---------------------------------------------------------------------------------------------------------------
def state_dir():
    return os.path.join(home(), ".claude", "state")


def due(now_ts):
    d = dt.datetime.fromtimestamp(now_ts, ET)
    if d.weekday() != 0 or d.hour < 6:
        return False
    try:
        return open(os.path.join(state_dir(), "hours-week.stamp")).read().strip() != d.strftime("%Y-%m-%d")
    except OSError:
        return True


def run_week(cfg, now_ts, machine, outdir, if_due=False):
    """Last full week (Monday to Sunday, Eastern) for every client in config["weekly"]: a weekly file, the full log refreshed,
       and the digest line the hub shows. Writes the day's stamp last."""
    import fcntl
    os.makedirs(state_dir(), exist_ok=True)
    lock = open(os.path.join(state_dir(), "hours-week.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    if if_due and not due(now_ts):
        return None
    this_mon = week_start_ts(now_ts)
    lo, hi = this_mon - 7 * 86400, this_mon
    keys = [k for k in cfg["weekly"] if k in cfg["clients"]]
    last_day = fday(hi - 1)
    res = analyze(cfg, keys, lo, hi, machine)
    wdir = os.path.join(outdir, "weekly")
    os.makedirs(wdir, exist_ok=True)
    body = ["# Week of %s to %s" % (fday(lo), last_day), ""]
    parts = []
    for k in keys:
        body += ["```", report_text(cfg, res, k, lo, hi), "```", ""]
        sm = ksum(res["clients"][k])
        bits = "typed %s" % hh(sm["typed"])
        for s in ("call", "mail", "git"):
            if sm["src"][s]:
                bits += ", %s %s" % (SRC_WORD[s], hh(sm["src"][s]))
        parts.append("%s %s h (%s)" % (cfg["clients"][k].get("label", k), hh(sm["total"]), bits))
    with open(os.path.join(wdir, fday(lo) + ".md"), "w") as fh:
        fh.write("\n".join(body))
    # the full history for the clients that keep a log (it is cheap now that transcripts are cached)
    for k in keys:
        if cfg["clients"][k].get("log_name"):
            full = analyze(cfg, [k], 0.0, hi, machine)
            log_files(cfg, full, k, 0.0, hi, machine, outdir)
    line = "Hours, week of %s: %s" % (dt.datetime.fromtimestamp(lo, ET).strftime("%a %-d %b"), "; ".join(parts))
    with open(os.path.join(state_dir(), "hours-week.txt"), "w") as fh:
        fh.write(line + "\n")
    with open(os.path.join(state_dir(), "hours-week.stamp"), "w") as fh:
        fh.write(dt.datetime.fromtimestamp(now_ts, ET).strftime("%Y-%m-%d") + "\n")
    return line


# ---------------------------------------------------------------------------------------------------------------
def pick_clients(cfg, arg):
    if not arg or arg == "all":
        return list(cfg["clients"])
    keys = [a.strip() for a in arg.split(",")]
    bad = [k for k in keys if k not in cfg["clients"]]
    if bad:
        sys.exit("unknown client %s; configured: %s" % (", ".join(bad), ", ".join(cfg["clients"]) or "none (see clients.json hours)"))
    return keys


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["report", "log", "rows", "week", "unclear"])
    ap.add_argument("--client")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--machine", default=None)
    ap.add_argument("--no-sources", action="store_true", help="typed time only (the internship method on its own)")
    ap.add_argument("--rows-in", nargs="*", default=[])
    ap.add_argument("--out")
    ap.add_argument("--now")
    ap.add_argument("--if-due", action="store_true")
    ap.add_argument("--join", type=int, default=30)
    a = ap.parse_args(argv)
    cfg = load_config()
    machine = a.machine if a.machine is not None else machine_name()
    outdir = os.path.expanduser(a.out) if a.out and a.mode in ("log", "week") else out_dir(cfg)
    lo, hi = window(a.since, a.until)
    if a.mode == "week":
        now = parse_ts(a.now) if a.now else dt.datetime.now(ET).timestamp()
        line = run_week(cfg, now, machine, outdir, if_due=a.if_due)
        print(line or "nothing to do (not due, or another run holds the lock)")
        return 0
    keys = pick_clients(cfg, a.client)
    res = analyze(cfg, keys, lo, hi, machine, sources=not a.no_sources, rows_in=a.rows_in)
    if a.mode == "report":
        ex = res["info"]["excluded"]
        allp = [e for e in res["events"]]
        P = print
        P("window %s to %s (Eastern), machine %s" % (fday(lo) if lo else "start", fday(hi - 1), machine or "?"))
        by_sid = collections.defaultdict(list)
        for e in allp:
            by_sid[e["sid"]].append(e["ts"])
        all_iv = merge_iv([x for ts in by_sid.values() for x in stream_iv(ts)])
        P("all typed activity, every project, union over sessions: %s h, %d events ; duplicates dropped %d ; cards %s"
          % (hh(secs(all_iv)), len(allp), res["info"]["dropped_dupes"], dict(res["info"]["cards"])))
        P("")
        for k in keys:
            P(report_text(cfg, res, k, lo, hi, a.join))
            P("")
    elif a.mode == "log":
        for k in keys:
            mdp, tsv, rows = log_files(cfg, res, k, lo, hi, machine, outdir)
            print("%s: %d sittings -> %s, %s" % (k, len(rows), mdp, tsv))
    elif a.mode == "rows":
        if not a.out:
            sys.exit("rows needs --out FILE")
        rows_file(res, keys, machine, os.path.expanduser(a.out))
        print("wrote", a.out)
    elif a.mode == "unclear":
        for k in keys:
            print(unclear_text(cfg, res, k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
