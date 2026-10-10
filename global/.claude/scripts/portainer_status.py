#!/usr/bin/env python3
"""Read-only Swarm status through Portainer: services, replicas, failing tasks. Never credentials.

    portainer_status.py --env <repo>/.env --prefix PORTAINER [--match REGEX]

Loads <PREFIX>_URL/_USER/_PASSWORD/_ENDPOINT_ID inside this process (never printed or put on a
command line). One POST to /api/auth for a session token, then GET requests only. Prints service
name, mode, running/desired replicas, and for unhealthy services the latest task states with their
error messages. It never prints service specs, env vars, secrets or labels.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request


def read_env(path, prefix):
    want = {f"{prefix}_{k}": k for k in ("URL", "USER", "PASSWORD", "ENDPOINT_ID")}
    vals = {}
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            if k in want:
                vals[want[k]] = v.strip().strip('"').strip("'")
    missing = [k for k in want.values() if not vals.get(k)]
    if missing:
        sys.exit(f"portainer_status: {prefix}_{'/'.join(missing)} not set")
    return vals


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))


def call(method, url, token=None, body=None):
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with OPENER.open(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--prefix", default="PORTAINER")
    ap.add_argument("--match", default=".", help="regex on service names to show")
    ap.add_argument("--logs", metavar="SERVICE", help="print the last --tail log lines of one service, masked")
    ap.add_argument("--tail", type=int, default=60)
    ap.add_argument("--since", type=int, default=0, help="unix seconds; only logs after this")
    ap.add_argument("--force-rerun", metavar="SERVICE",
                    help="WRITE: re-run one allowlisted one-shot staging service (ForceUpdate+1)")
    a = ap.parse_args(argv)
    if a.force_rerun:
        return force_rerun(a)
    if a.logs:
        return show_logs(a)
    c = read_env(a.env, a.prefix)
    base = c["URL"].rstrip("/")
    st, auth = call("POST", base + "/api/auth", body={"username": c["USER"], "password": c["PASSWORD"]})
    if st != 200 or not auth or "jwt" not in auth:
        print(f"__PORTAINER=error__ auth HTTP {st}")
        return 1
    tok, ep = auth["jwt"], c["ENDPOINT_ID"]
    d = f"{base}/api/endpoints/{ep}/docker"
    st, services = call("GET", d + "/services", tok)
    st2, tasks = call("GET", d + "/tasks", tok)
    if st != 200 or st2 != 200:
        print(f"__PORTAINER=error__ services HTTP {st}, tasks HTTP {st2}")
        return 1
    rx = re.compile(a.match, re.I)
    running = {}
    for t in tasks:
        if (t.get("Status") or {}).get("State") == "running":
            running[t.get("ServiceID")] = running.get(t.get("ServiceID"), 0) + 1
    rows = []
    for s in services:
        name = s["Spec"]["Name"]
        if not rx.search(name):
            continue
        mode = s["Spec"].get("Mode", {})
        desired = mode.get("Replicated", {}).get("Replicas") if "Replicated" in mode else None
        rows.append((name, "global" if "Global" in mode else "replicated", running.get(s["ID"], 0), desired, s["ID"]))
    print(f"__PORTAINER=ok__ {len(rows)} services matching /{a.match}/")
    for name, mode, run, desired, sid in sorted(rows):
        bad = desired is not None and run < desired
        print(f"{'!!' if bad else 'ok'} {name}  {mode}  running {run}/{desired if desired is not None else '-'}")
        if bad:
            ts = sorted((t for t in tasks if t.get("ServiceID") == sid),
                        key=lambda t: t.get("UpdatedAt", ""), reverse=True)[:3]
            for t in ts:
                s_ = t.get("Status") or {}
                msg = (s_.get("Err") or s_.get("Message") or "")[:160]
                print(f"     {t.get('UpdatedAt', '')[:19]} {s_.get('State')} (want {t.get('DesiredState')}): {msg}")
    return 0


SECRET_KV = re.compile(r"(?i)\b([A-Z0-9_]*(?:PASS(?:WORD)?|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY)[A-Z0-9_]*)"
                       r"(\s*[=:]\s*)(['\"]?)[^\s'\"]+")
TOKEN_SHAPES = re.compile(r"gh[opsu]_\w{20,}|github_pat_\w{20,}|sk[-_][\w-]{16,}|AKIA[0-9A-Z]{16}|"
                          r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}|xox[abprs]-[\w-]{10,}")
URL_PW = re.compile(r"(://[^:/\s]+:)[^@\s]+@")


def mask(line):
    line = SECRET_KV.sub(lambda m: m.group(1) + m.group(2) + m.group(3) + "<redacted>", line)
    line = URL_PW.sub(r"\1<redacted>@", line)
    return TOKEN_SHAPES.sub("<redacted-token>", line)


def show_logs(a):
    c = read_env(a.env, a.prefix)
    base = c["URL"].rstrip("/")
    st, auth = call("POST", base + "/api/auth", body={"username": c["USER"], "password": c["PASSWORD"]})
    if st != 200 or not auth:
        print(f"__PORTAINER=error__ auth HTTP {st}")
        return 1
    tok, d = auth["jwt"], f"{base}/api/endpoints/{c['ENDPOINT_ID']}/docker"
    st, services = call("GET", d + "/services", tok)
    sid = next((s["ID"] for s in services or [] if s["Spec"]["Name"] == a.logs), None)
    if not sid:
        print(f"__PORTAINER=error__ no service named {a.logs}")
        return 1
    req = urllib.request.Request(f"{d}/services/{sid}/logs?stdout=1&stderr=1&timestamps=1&tail={a.tail}&since={a.since}",
                                 headers={"Authorization": "Bearer " + tok})
    try:
        with OPENER.open(req, timeout=30) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        print(f"__PORTAINER=error__ logs HTTP {e.code}")
        return 1
    out, i = [], 0
    while i + 8 <= len(raw):   # Docker's multiplexed stream: 8-byte header, then the frame
        n = int.from_bytes(raw[i + 4:i + 8], "big")
        out.append(raw[i + 8:i + 8 + n].decode("utf-8", "replace"))
        i += 8 + n
    text = "".join(out) if out else raw.decode("utf-8", "replace")
    print(f"__PORTAINER=ok__ last {a.tail} lines of {a.logs} (secrets masked)")
    for line in text.splitlines():
        print(mask(line)[:300])
    return 0


# The only services this script will ever change: staging one-shot setup jobs in our own stack.
RERUN_OK = re.compile(r"^chattanooga-staging_(db-import|init|fix-settings|fix-ownership)$")


def force_rerun(a):
    if "PROD" in a.prefix.upper():
        print("__PORTAINER=refused__ --force-rerun is staging-only")
        return 2
    if not RERUN_OK.match(a.force_rerun):
        print(f"__PORTAINER=refused__ {a.force_rerun} is not an allowlisted one-shot staging job")
        return 2
    c = read_env(a.env, a.prefix)
    base = c["URL"].rstrip("/")
    st, auth = call("POST", base + "/api/auth", body={"username": c["USER"], "password": c["PASSWORD"]})
    if st != 200 or not auth:
        print(f"__PORTAINER=error__ auth HTTP {st}")
        return 1
    tok, d = auth["jwt"], f"{base}/api/endpoints/{c['ENDPOINT_ID']}/docker"
    st, services = call("GET", d + "/services", tok)
    svc = next((s for s in services or [] if s["Spec"]["Name"] == a.force_rerun), None)
    if not svc:
        print(f"__PORTAINER=error__ no service named {a.force_rerun}")
        return 1
    spec = svc["Spec"]
    spec.setdefault("TaskTemplate", {})["ForceUpdate"] = int(spec["TaskTemplate"].get("ForceUpdate", 0)) + 1
    version = svc["Version"]["Index"]
    st, _ = call("POST", f"{d}/services/{svc['ID']}/update?version={version}", tok, body=spec)
    print(f"__PORTAINER={'ok' if st == 200 else 'error'}__ force-rerun {a.force_rerun}: HTTP {st}")
    return 0 if st == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
