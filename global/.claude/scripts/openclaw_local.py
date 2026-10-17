#!/usr/bin/env python3
"""openclaw_local.py: OpenClaw's last-resort model, a local Ollama container, wired in as the LAST fallback.

  openclaw_local.py apply   [--model ID]   back up the config, add the ollama provider, append ollama/ID as the
                                           last fallback for agents.defaults and the receptionist, add the
                                           tool-less test agent "hubtest". One validated write.
  openclaw_local.py verify  [--model ID]   read back: order, tools untouched, no bindings on the test agent,
                                           and the container answers from this distro. Changes nothing.
  openclaw_local.py rollback [BACKUP]      restore BACKUP, or the newest backup this script made.
  openclaw_local.py probe   --model ID --message TEXT [--agent hubtest] [--reminder]
                                           one-off run on the test agent, forced onto one model.

Runs INSIDE the OpenClawGateway distro as user openclaw (python3 stdlib only):
  wsl.exe -d OpenClawGateway -u openclaw -e python3 - apply < THIS_FILE
It prints no key and no config value that could hold one.

Two rules from Jonathan (16 Oct), enforced in code and covered by tests/test_openclaw_local.py:
  1. His one chat is "Main session", agent:main:main. Nothing here writes to it.
  2. Nothing here creates or writes ANY session under agent "main": no --agent main, no --session-key
     starting "agent:main", no injected notes. Tests run on the separate agent "hubtest" (tools allow [],
     same models, no channel bindings), whose sessions do not show in his sidebar.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

PROTECTED_AGENT = "main"
TEST_AGENT = "hubtest"
RECEPTIONIST = "receptionist"
DEFAULT_MODEL = "qwen2.5:1.5b-instruct"
BASE_URL = "http://127.0.0.1:11434"  # no /v1: that selects OpenAI-compatible mode and loses tool-call parsing
PROVIDER = "ollama"
CONTEXT_WINDOW = 32768  # qwen2.5 1.5B native; ~1 GB of KV cache on the 8 GB GPU. 16384 left no room beside the ~10K-token main prompt.
GEMINI_CAP = 32000  # compaction fires near cap minus its reserve (about 24K), so one compacted chat always fits CONTEXT_WINDOW
GEMINI_MODELS = ("gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash")
COMPACTION = {  # 16 Oct: compaction ran on the 1.5B model and failed its own quality guard; the summary now comes from Gemini
    "mode": "safeguard", "model": "google/gemini-3.5-flash-lite", "keepRecentTokens": 6000,
    "recentTurnsPreserve": 4, "qualityGuard": {"enabled": True, "maxRetries": 2},
}
MAX_TOKENS = 1024
BOOTSTRAP = ("AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md")
# What a small local model needs at the very end of its prompt to hold the line: it follows the end of a long prompt best.
REMINDER = (
    "\n\n## FINAL REMINDER\nYou have no tools and nothing you say makes anything happen. If Jonathan asks for "
    "something to be done and it is not one of the listed forms, reply exactly: I can't start this myself; type "
    "start <repo>: <what you want> and the hub sends a worker.\nNever say you did, will do, or have done anything.\n"
)


class Refused(Exception):
    pass


def guard_target(agent=None, session_key=None, session_id=None):
    """Rule 2: refuse anything that would create or write a session under agent main."""
    if (agent or "").strip().lower() == PROTECTED_AGENT:
        raise Refused("refused: agent 'main' is Jonathan's chat; test on '%s'" % TEST_AGENT)
    if not agent:
        raise Refused("refused: no --agent given (the CLI would route to main); use '%s'" % TEST_AGENT)
    key = (session_key or "").strip().lower()
    if key.startswith("agent:%s:" % PROTECTED_AGENT) or key == PROTECTED_AGENT:
        raise Refused("refused: session key '%s' is under agent main" % session_key)
    if key and not key.startswith("agent:%s:" % agent.lower()):
        raise Refused("refused: session key must be under agent:%s:" % agent)
    return agent


def run(args, stdin=None, timeout=120):
    return subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout)


def config_path():
    r = run(["openclaw", "config", "file"])
    path = (r.stdout.strip().splitlines() or [""])[-1]
    if r.returncode != 0 or not os.path.isfile(path):
        raise SystemExit("no config file at the path openclaw reports")
    return path


def local_ref(model):
    return "%s/%s" % (PROVIDER, model)


def provider_block(model):
    return {
        "baseUrl": BASE_URL,
        "apiKey": "ollama-local",  # the documented marker for a loopback host; not a secret
        "api": "ollama",
        "timeoutSeconds": 600,  # a cold 7.8K-token prompt prefills for a while (measured on CPU; the GPU is faster, the timeout stays generous)
        "models": [{
            "id": model, "name": model, "reasoning": False, "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": CONTEXT_WINDOW, "contextTokens": CONTEXT_WINDOW, "maxTokens": MAX_TOKENS,
            "params": {"keep_alive": "30m", "num_ctx": CONTEXT_WINDOW},  # Ollama defaults to 4096 and the real prompt is ~10K
        }],
    }


def with_tail(models, ref):
    """The list with ref last, whatever else was there. Idempotent."""
    return [m for m in models if m != ref] + [ref]


def build_patch(cfg, model, state_dir):
    ref = local_ref(model)
    d = cfg["agents"]["defaults"]
    r = cfg["agents"]["entries"][RECEPTIONIST]
    d_fb = with_tail(d["model"].get("fallbacks", []), ref)
    r_fb = with_tail(r["model"].get("fallbacks", []), ref)
    d_allow = sorted(set(d.get("modelPolicy", {}).get("allow", [])) | {ref})
    r_allow = sorted(set(r.get("modelPolicy", {}).get("allow", [])) | {ref})
    return {
        "models": {"providers": {
            PROVIDER: provider_block(model),
            "google": {"models": [{"id": g, "name": g, "contextWindow": 200000, "contextTokens": GEMINI_CAP} for g in GEMINI_MODELS]},
        }},
        "agents": {
            "defaults": {
                "model": {"primary": d["model"]["primary"], "fallbacks": d_fb},
                "models": {ref: {}},
                "modelPolicy": {"allow": d_allow},
                "compaction": COMPACTION,
            },
            "entries": {
                RECEPTIONIST: {"model": {"primary": r["model"]["primary"], "fallbacks": r_fb},
                               "modelPolicy": {"allow": r_allow}},
                TEST_AGENT: {
                    "name": "Hub test",
                    "description": "Test agent for the hub. Never routed to; its sessions stay out of the main chat.",
                    "workspace": os.path.join(state_dir, "workspace-" + TEST_AGENT),
                    "model": {"primary": d["model"]["primary"], "fallbacks": d_fb},
                    "modelPolicy": {"allow": d_allow},
                    "tools": {"profile": "minimal", "allow": [], "deny": ["gateway", "session_status", "presence"]},
                },
            },
        },
    }


def apply(model):
    cfg_file = config_path()
    state_dir = os.path.dirname(cfg_file)
    cfg = json.load(open(cfg_file))
    bak = "%s.bak-local-%s" % (cfg_file, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(cfg_file, bak)
    os.chmod(bak, 0o600)
    print("backup: " + bak)
    patch = json.dumps(build_patch(cfg, model, state_dir))
    dry = run(["openclaw", "config", "patch", "--stdin", "--dry-run"], stdin=patch)
    if dry.returncode != 0:
        print("dry run refused; nothing changed\n" + dry.stdout[-400:] + dry.stderr[-400:])
        return 1
    real = run(["openclaw", "config", "patch", "--stdin"], stdin=patch)
    print((real.stdout + real.stderr).strip()[-400:])
    if real.returncode != 0:
        print("patch failed; restore with: openclaw_local.py rollback")
        return 1
    return verify(model)


def reachable(model):
    try:
        with urllib.request.urlopen(BASE_URL + "/api/tags", timeout=10) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except Exception as e:  # any failure is a FAIL line, not a crash
        return False, type(e).__name__
    return model in names, "%d models served" % len(names)


def verify(model, cfg=None, probe_net=True):
    ref = local_ref(model)
    cfg = cfg or json.load(open(config_path()))
    d = cfg["agents"]["defaults"]
    ents = cfg["agents"]["entries"]
    r = ents[RECEPTIONIST]
    ok = True

    def out(name, good, detail=""):
        nonlocal ok
        ok = ok and good
        print(("PASS " if good else "FAIL ") + name + (" " + detail if detail else ""))

    for label, m in (("defaults", d["model"]), (RECEPTIONIST, r["model"]), (TEST_AGENT, ents.get(TEST_AGENT, {}).get("model", {}))):
        chain = [m.get("primary")] + list(m.get("fallbacks", []))
        out("chain." + label, bool(chain[0]) and chain[-1] == ref and chain.count(ref) == 1,
            " > ".join(str(c) for c in chain))
    for label, pol in (("defaults", d.get("modelPolicy", {})), (RECEPTIONIST, r.get("modelPolicy", {}))):
        out("allowlist." + label, ref in pol.get("allow", []))
    out("defaults.models", ref in d.get("models", {}))
    rt = r.get("tools", {})
    out("receptionist.tools", rt.get("allow") == [] and rt.get("profile") == "minimal")
    ht = ents.get(TEST_AGENT, {}).get("tools", {})
    out("hubtest.tools", ht.get("allow") == [] and ht.get("profile") == "minimal")
    out("hubtest.no_bindings", not [b for b in cfg.get("bindings", []) if b.get("agentId") == TEST_AGENT])
    out("main.entry_untouched", set(ents.get("main", {})) <= {"workspace"}, "keys: " + ",".join(sorted(ents.get("main", {}))))
    p = cfg.get("models", {}).get("providers", {}).get(PROVIDER, {})
    out("provider", p.get("baseUrl") == BASE_URL and p.get("api") == "ollama" and any(m.get("id") == model for m in p.get("models", [])))
    provs = cfg.get("models", {}).get("providers", {})
    # google may carry per-model context caps only: no key, no URL, so nothing new to bill or reach
    paid = [k for k, v in provs.items() if k != PROVIDER and not (k == "google" and set(v) <= {"models"})]
    out("no_other_provider_added", not paid, ",".join(paid) or "only " + PROVIDER)
    if probe_net:
        good, detail = reachable(model)
        out("container_reachable_from_gateway_distro", good, detail)
    return 0 if ok else 1


def rollback(bak=None):
    cfg_file = config_path()
    if not bak:
        baks = sorted((f for f in os.listdir(os.path.dirname(cfg_file)) if ".bak-local-" in f),
                      key=lambda f: os.path.getmtime(os.path.join(os.path.dirname(cfg_file), f)))
        if not baks:
            print("no backup made by this script beside " + cfg_file)
            return 1
        bak = os.path.join(os.path.dirname(cfg_file), baks[-1])
    shutil.copy2(cfg_file, cfg_file + ".pre-rollback-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(bak, cfg_file)
    os.chmod(cfg_file, 0o600)
    print("restored " + bak)
    return 0


def sync_test_workspace(state_dir, reminder=False):
    """The test agent gets copies of main's bootstrap files, so it knows what main knows. Read-only on main's side."""
    src, dst = os.path.join(state_dir, "workspace"), os.path.join(state_dir, "workspace-" + TEST_AGENT)
    os.makedirs(dst, mode=0o700, exist_ok=True)
    for f in BOOTSTRAP:
        if os.path.isfile(os.path.join(src, f)):
            shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
    if reminder:  # the test agent's own copy only; main's AGENTS.md is never written
        with open(os.path.join(dst, "MEMORY.md"), "a") as fh:  # MEMORY.md is injected last, so this ends the prompt
            fh.write(REMINDER)
    return dst


def probe_command(agent, model, message, session_key=None):
    guard_target(agent, session_key)
    cmd = ["openclaw", "agent", "--agent", agent, "--model", local_ref(model) if "/" not in model else model,
           "--message", message, "--json", "--timeout", "900"]
    if session_key:
        cmd += ["--session-key", session_key]
    return cmd


def probe(agent, model, message, reminder=False):
    # a fresh session per probe, always under the test agent, so one question never sees the last one's answer
    cmd = probe_command(agent, model, message, "agent:%s:probe-%d" % (agent, int(time.time())))  # raises Refused before anything is written
    sync_test_workspace(os.path.dirname(config_path()), reminder)
    t0 = time.time()
    r = run(cmd, timeout=1000)
    print("seconds: %.1f" % (time.time() - t0))
    print((r.stdout + r.stderr)[-6000:])
    return r.returncode


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("apply", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("--model", default=DEFAULT_MODEL)
    rb = sub.add_parser("rollback")
    rb.add_argument("backup", nargs="?")
    pr = sub.add_parser("probe")
    pr.add_argument("--model", required=True)
    pr.add_argument("--message", required=True)
    pr.add_argument("--agent", default=TEST_AGENT)
    pr.add_argument("--reminder", action="store_true", help="append the final reminder to the test agent's MEMORY.md copy (injected last)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "apply":
            return apply(a.model)
        if a.cmd == "verify":
            return verify(a.model)
        if a.cmd == "rollback":
            return rollback(a.backup)
        return probe(a.agent, a.model, a.message, a.reminder)
    except Refused as e:
        print(e)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
