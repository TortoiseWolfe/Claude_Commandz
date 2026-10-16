#!/usr/bin/env python3
"""Fake Gemini on loopback, for staging the OpenClaw gateway chain (never the live gateway).

  openclaw_fake_gemini.py PORT CTL LOG

PORT  loopback port to listen on (127.0.0.1 only)
CTL   a JSON file, re-read on every request: {"<model id>": {"status": 429|200, "text": "...", "call": "<tool>", "args": {...}}}
      A model not listed answers 200 "ok". "call" makes the model reply with a function call to that tool,
      with "args" as its arguments: that is how a staged run plays a prompt-injected model.
      If CTL names a model "_decl_dump" with {"path": "..."}, each request's tool declarations are written there.
LOG   one JSON line per request: {"ts","model","stream","tools":[...names offered],"status","tool_result"}.
      tool_result is the newest function response in the request (cut to 400 chars). Never the prompt.

Speaks generateContent and streamGenerateContent (SSE). Python 3 stdlib only."""

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, CTL, LOG = int(sys.argv[1]), sys.argv[2], sys.argv[3]
ROUTE = re.compile(r"/models/([^/:]+):(generateContent|streamGenerateContent)")


def control(model):
    try:
        with open(CTL) as f:
            return json.load(f).get(model, {})
    except (OSError, ValueError):
        return {}


def last_tool_result(body):
    """The newest function response the gateway sent back (what a tool call returned), cut to 400 chars."""
    for turn in reversed(body.get("contents") or []):
        for part in turn.get("parts") or []:
            r = part.get("functionResponse")
            if r:
                return json.dumps(r.get("response"))[:400]
    return None


def tool_names(body):
    names = []
    for t in body.get("tools") or []:
        for d in t.get("functionDeclarations") or t.get("function_declarations") or []:
            names.append(d.get("name"))
    return sorted(n for n in names if n)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        m = ROUTE.search(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        model, stream = (m.group(1), m.group(2) == "streamGenerateContent") if m else ("?", False)
        ctl = control(model)
        status = int(ctl.get("status", 200))
        dump = control("_decl_dump").get("path")
        if dump:
            with open(dump, "w") as f:
                json.dump(body.get("tools"), f)
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": int(time.time()), "model": model, "stream": stream,
                                "tools": tool_names(body), "status": status,
                                "tool_result": last_tool_result(body)}) + "\n")
        if status != 200:
            err = json.dumps({"error": {"code": status, "status": "RESOURCE_EXHAUSTED",
                                        "message": "You exceeded your current quota (staged)."}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return
        if ctl.get("call"):
            part = {"functionCall": {"name": ctl["call"], "args": ctl.get("args", {})}}
        else:
            part = {"text": ctl.get("text", "ok")}
        msg = {"candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP", "index": 0}],
               "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
               "modelVersion": model}
        data = json.dumps(msg).encode()
        if stream:
            data = b"data: " + data + b"\r\n\r\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
