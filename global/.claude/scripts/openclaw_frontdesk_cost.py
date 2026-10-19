#!/usr/bin/env python3
"""openclaw_frontdesk_cost.py: what the receptionist's own model calls cost, and the heartbeat setting that keeps them down.

The receptionist (OpenClaw, agent "main") calls the Anthropic API on its own Console key, apart from the dispatch pass
(openclaw_dispatch.py pass), which runs `claude -p` on Jonathan's own login. Spend on that key comes from the Main session's turns:
his chat messages, and the gateway's heartbeat, a turn every 30 minutes that carries the whole chat (about 40K tokens) to a model whose
5-minute prompt cache has long gone cold, so each one writes the full prompt again. On 19 Oct 11 of the day's 18 turns were heartbeats.

Runs INSIDE the OpenClawGateway distro as user openclaw (python3 stdlib only):
  wsl.exe -d OpenClawGateway -u openclaw -e python3 - report [--days N]  < THIS_FILE
  wsl.exe -d OpenClawGateway -u openclaw -e python3 - heartbeat apply    < THIS_FILE
  wsl.exe -d OpenClawGateway -u openclaw -e python3 - heartbeat verify   < THIS_FILE
  wsl.exe -d OpenClawGateway -u openclaw -e python3 - heartbeat rollback < THIS_FILE

  report              per day, model and kind of turn (heartbeat or chat): turns, prompt tokens, and the cost at list prices.
                      Reads the agent database read-only; prints numbers only, never a prompt, a message or a key.
  heartbeat apply     back up the config, then one validated write: the heartbeat runs on Haiku, with light context, in its own
                      isolated session, once an hour. Nothing else in the config is touched; the heartbeat still fires.
  heartbeat verify    read the config back.
  heartbeat rollback  restore the newest backup this script made.

List prices (dollars per million tokens: input, 5-minute cache write, cache read, output) are an upper bound: the Console cost report
for 17 and 18 Oct came in below what these prices give for the same turns, so compare the shares, and the day's total once billed.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

PRICES = {  # input, cache write (5 min), cache read, output
    "claude-sonnet-5-5": (3.0, 3.75, 0.30, 15.0),
    "claude-haiku-5-5": (1.0, 1.25, 0.10, 5.0),
}
DB_PATH = os.path.expanduser("~/.openclaw/agents/main/agent/openclaw-agent.sqlite")
HEARTBEAT_MODEL = "claude/claude-haiku-5-5"
HEARTBEAT_EVERY = "1h"
BACKUP_TAG = ".bak-heartbeat-"
TZ = ZoneInfo("America/New_York")


class Refused(Exception):
    pass


def list_cost(model, usage):
    """Dollars at list price for one turn's usage block. An unpriced model raises KeyError rather than reading as free."""
    inp, write, read, out = PRICES[model]
    return (usage.get("input", 0) * inp + usage.get("cacheWrite", 0) * write
            + usage.get("cacheRead", 0) * read + usage.get("output", 0) * out) / 1e6


def kind_of(prompt):
    return "heartbeat" if re.search("heartbeat", prompt or "", re.I) else "chat"


def ro_uri(path):
    return "file:%s?mode=ro" % path


def _day(ts):
    if isinstance(ts, str):
        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    else:
        t = datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc)
    return t.astimezone(TZ).strftime("%Y-%m-%d")


def read_turns(conn):
    """-> [(day, model, kind, usage)] for every completed model call that reported usage. The prompt is classified, never kept."""
    kinds, turns = {}, []
    for (raw,) in conn.execute("select event_json from trajectory_runtime_events order by seq"):
        ev = json.loads(raw)
        if ev.get("type") == "prompt.submitted":
            kinds[ev.get("runId")] = kind_of((ev.get("data") or {}).get("prompt"))
        elif ev.get("type") == "model.completed":
            usage = (ev.get("data") or {}).get("usage")
            if usage:
                turns.append((_day(ev["ts"]), ev["modelId"], kinds.get(ev.get("runId"), "chat"), usage))
    return turns


def summarize(turns):
    rows = {}
    for day, model, kind, usage in turns:
        r = rows.setdefault((day, model, kind), {"turns": 0, "tokens": 0, "cost": 0.0})
        r["turns"] += 1
        r["tokens"] += usage.get("input", 0) + usage.get("cacheWrite", 0) + usage.get("cacheRead", 0)
        r["cost"] += list_cost(model, usage)
    return rows


def print_report(rows, days=None):
    days_all = sorted({k[0] for k in rows})
    keep = set(days_all[-days:]) if days else set(days_all)
    print("%-10s %-18s %-9s %5s %9s %9s %8s" % ("day", "model", "kind", "turns", "tok/turn", "list $", "$/turn"))
    for key in sorted(rows):
        if key[0] not in keep:
            continue
        r = rows[key]
        print("%-10s %-18s %-9s %5d %9d %9.2f %8.3f" % (key + (r["turns"], r["tokens"] // r["turns"], r["cost"], r["cost"] / r["turns"])))


def heartbeat_patch(cfg, model=HEARTBEAT_MODEL, every=HEARTBEAT_EVERY):
    """The merge patch that makes the heartbeat cheap: only agents.defaults.heartbeat, its agentId kept."""
    d = cfg["agents"]["defaults"]
    allow = d.get("modelPolicy", {}).get("allow")
    if allow is not None and model not in allow:
        raise Refused("refused: %s is not in agents.defaults.modelPolicy.allow" % model)
    beat = dict(d.get("heartbeat", {}))
    beat.update({"every": every, "model": model, "lightContext": True, "isolatedSession": True})
    return {"agents": {"defaults": {"heartbeat": beat}}}


def verify_heartbeat(cfg, model=HEARTBEAT_MODEL, every=HEARTBEAT_EVERY):
    beat = cfg.get("agents", {}).get("defaults", {}).get("heartbeat", {})
    return [
        ("heartbeat.model", beat.get("model") == model),
        ("heartbeat.every", beat.get("every") == every),
        ("heartbeat.lightContext", beat.get("lightContext") is True),
        ("heartbeat.isolatedSession", beat.get("isolatedSession") is True),
        ("heartbeat.agentId", bool(beat.get("agentId"))),
    ]


def run(args, stdin=None, timeout=120):
    return subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout)


def config_path():
    r = run(["openclaw", "config", "file"])
    path = (r.stdout.strip().splitlines() or [""])[-1]
    if r.returncode != 0 or not os.path.isfile(path):
        raise SystemExit("no config file at the path openclaw reports")
    return path


def check(cfg):
    results = verify_heartbeat(cfg)
    for name, ok in results:
        print(("PASS " if ok else "FAIL ") + name)
    return 0 if all(ok for _, ok in results) else 1


def apply():
    cfg_file = config_path()
    cfg = json.load(open(cfg_file))
    bak = "%s%s%s" % (cfg_file, BACKUP_TAG, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(cfg_file, bak)
    os.chmod(bak, 0o600)
    print("backup: " + bak)
    patch = json.dumps(heartbeat_patch(cfg))
    dry = run(["openclaw", "config", "patch", "--stdin", "--dry-run"], stdin=patch)
    if dry.returncode != 0:
        print("dry run refused; nothing changed\n" + dry.stdout[-400:] + dry.stderr[-400:])
        return 1
    real = run(["openclaw", "config", "patch", "--stdin"], stdin=patch)
    print((real.stdout + real.stderr).strip()[-400:])
    if real.returncode != 0:
        print("patch failed; restore with: heartbeat rollback")
        return 1
    return check(json.load(open(cfg_file)))


def rollback():
    cfg_file = config_path()
    folder = os.path.dirname(cfg_file)
    baks = sorted((f for f in os.listdir(folder) if BACKUP_TAG in f), key=lambda f: os.path.getmtime(os.path.join(folder, f)))
    if not baks:
        print("no backup made by this script beside " + cfg_file)
        return 1
    shutil.copy2(cfg_file, cfg_file + ".pre-rollback-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(os.path.join(folder, baks[-1]), cfg_file)
    os.chmod(cfg_file, 0o600)
    print("restored " + baks[-1])
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--days", type=int, default=None)
    hb = sub.add_parser("heartbeat")
    hb.add_argument("action", choices=("apply", "verify", "rollback"))
    args = ap.parse_args(argv)
    if args.cmd == "report":
        conn = sqlite3.connect(ro_uri(DB_PATH), uri=True)
        print_report(summarize(read_turns(conn)), args.days)
        return 0
    if args.action == "apply":
        return apply()
    if args.action == "rollback":
        return rollback()
    return check(json.load(open(config_path())))


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Refused as e:
        print(e)
        sys.exit(1)
