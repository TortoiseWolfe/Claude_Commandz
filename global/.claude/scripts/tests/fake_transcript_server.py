#!/usr/bin/env python3
"""A tiny stand-in for the transcript server: JSON-RPC over stdin/stdout, invented captions, two pages.

Test helper only (not a test file). Variants come from the environment:
  FAKE_DURATION   duration text from get_video_info (default "50 seconds", or "1 hour, 2 minutes, 3 seconds" when truncated; "none" leaves the field out)
  FAKE_MS         "1" sends start and duration in milliseconds
  FAKE_TRUNCATE   "1" sends only the first 3 cues (the video still reports its full length)
  FAKE_NO_INFO    "1" answers get_video_info with a JSON-RPC error
  FAKE_ERROR      "1" answers get_timed_transcript with isError
"""
import json
import os
import sys

WORDS = ["lantern", "orchard", "pebble", "harbor", "violet", "quarry", "meadow", "copper", "thistle", "ember"]
CUES = [{"start": i * 5, "duration": 5, "text": "invented %s line number %d" % (WORDS[i], i)} for i in range(10)]
PAGE = 6


def result(i, sc):
    return {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": "x"}], "structuredContent": sc}}


def cues():
    c = CUES[:3] if os.environ.get("FAKE_TRUNCATE") == "1" else CUES
    if os.environ.get("FAKE_MS") == "1":
        return [{"start": x["start"] * 1000, "duration": x["duration"] * 1000, "text": x["text"]} for x in c]
    return [dict(x) for x in c]


def handle(m):
    mid, method = m.get("id"), m.get("method")
    if mid is None:
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2024-11-05", "capabilities": {},
                                                          "serverInfo": {"name": "fake", "version": "0"}}}
    if method == "tools/list":
        names = ["get_video_info", "get_transcript", "get_timed_transcript", "get_video_info_extra"]
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [{"name": n} for n in names]}}
    if method == "tools/call":
        name = m["params"]["name"]
        args = m["params"].get("arguments", {})
        if name == "get_video_info":
            if os.environ.get("FAKE_NO_INFO") == "1":
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no info"}}
            sc = {"title": "Invented Title", "description": "Invented description words", "uploader": "Nobody",
                  "upload_date": "20990101"}
            d = os.environ.get("FAKE_DURATION", "1 hour, 2 minutes, 3 seconds" if os.environ.get("FAKE_TRUNCATE") == "1" else "50 seconds")
            if d != "none":
                sc["duration"] = d
            return result(mid, sc)
        if name == "get_timed_transcript":
            if os.environ.get("FAKE_ERROR") == "1":
                return {"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": "boom"}]}}
            allc = cues()
            at = int(args.get("next_cursor") or 0)
            seg = allc[at:at + PAGE]
            more = at + PAGE < len(allc)
            return result(mid, {"segments": seg, "next_cursor": str(at + PAGE) if more else None})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        r = handle(json.loads(line))
        if r is not None:
            print(json.dumps(r), flush=True)


if __name__ == "__main__":
    main()
