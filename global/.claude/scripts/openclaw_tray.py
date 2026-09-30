#!/usr/bin/env python3
"""Narrow client for the OpenClaw tray's local MCP server (127.0.0.1:8765).

WHY A SCRIPT AND NOT `claude mcp add`. The tray serves 52 tools, not just a sandboxed runner:
app.settings.set can switch the sandbox off, system.execApprovals.set rewrites the approval
policy, others approve device pairing or record the screen and microphone. Registering the
server would put all of them in front of every session and every subagent. This client can
only call the four tools in ALLOWED; everything else is refused before any request is made.

The token lives in %APPDATA%\\OpenClawTray\\mcp-token.txt, is written with a UTF-8 BOM, and is
read fresh on every call (it is never copied anywhere). Needs WSL mirrored networking so
Windows' 127.0.0.1 is reachable from here.

  openclaw_tray.py notify "<title>" "<body>"
  openclaw_tray.py run [--timeout-ms N] -- <argv...>     # e.g. -- powershell.exe -NoProfile -Command "..."
  openclaw_tray.py info
  openclaw_tray.py approvals

Prints the tool's JSON result. Exit 0 on success; 1 on a tool error or a failed/timed-out
run; 2 on usage, auth or connection errors.
"""
import json
import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8765/"
TOKEN_FILE = "/mnt/c/Users/JonPo/AppData/Roaming/OpenClawTray/mcp-token.txt"
ALLOWED = {
    "notify": "system.notify",
    "run": "system.run",
    "info": "device.info",
    "approvals": "system.execApprovals.get",
}


def die(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def token():
    try:
        with open(TOKEN_FILE, encoding="utf-8-sig") as f:
            return f.read().strip()
    except OSError as e:
        die(f"no MCP token ({e}); is EnableMcpServer on and the tray running?")


def post(body, tok, session=None, timeout=15):
    headers = {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(URL, json.dumps(body).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            sid = r.headers.get("Mcp-Session-Id") or session
    except urllib.error.HTTPError as e:
        die(f"HTTP {e.code} from tray MCP: {e.read().decode()[:200]}")
    except (urllib.error.URLError, TimeoutError) as e:
        die(f"tray MCP unreachable at {URL}: {e}")
    if not raw.strip():
        return None, sid
    if not raw.lstrip().startswith("{"):  # SSE framing: take the last data: line
        raw = [l[5:].strip() for l in raw.splitlines() if l.startswith("data:")][-1]
    return json.loads(raw), sid


def call(tool, args, timeout=15):
    tok = token()
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "claude-code-openclaw_tray", "version": "1"}}}
    _, sid = post(init, tok)
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, tok, sid)
    msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
           "params": {"name": tool, "arguments": args}}
    resp, _ = post(msg, tok, sid, timeout=timeout)
    if "error" in resp:
        die(json.dumps(resp["error"]), 1)
    result = resp["result"]
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    try:
        payload = json.loads(texts[0]) if len(texts) == 1 else texts
    except json.JSONDecodeError:
        payload = texts[0]
    return result.get("isError", False), payload


def main(argv):
    if not argv or argv[0] not in ALLOWED:
        die(f"usage: openclaw_tray.py {{{','.join(ALLOWED)}}} ...  (other tray tools are refused)")
    verb, rest = argv[0], argv[1:]
    timeout = 15
    if verb == "notify":
        if len(rest) != 2:
            die('usage: openclaw_tray.py notify "<title>" "<body>"')
        args = {"title": rest[0], "body": rest[1]}
    elif verb == "run":
        timeout_ms = 30000
        if rest[:1] == ["--timeout-ms"]:
            timeout_ms, rest = int(rest[1]), rest[2:]
        if rest[:1] != ["--"] or len(rest) < 2:
            die("usage: openclaw_tray.py run [--timeout-ms N] -- <argv...>")
        args = {"command": rest[1:], "timeoutMs": timeout_ms}
        # The tray's policy is allowlist + ask on-miss: an unlisted command waits for Jonathan
        # to answer a prompt on Windows, so leave five minutes for that on top of the run.
        timeout = timeout_ms / 1000 + 300
    else:
        args = {}
    is_error, payload = call(ALLOWED[verb], args, timeout)
    print(json.dumps(payload, indent=2) if not isinstance(payload, str) else payload)
    failed = is_error or (verb == "run" and isinstance(payload, dict) and not payload.get("success", False))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
