#!/usr/bin/env python3
"""Co-op call reader: clean a call transcript by deleting filler, then pull decisions, who owes what, card talk and open questions out of it on a model we run, and check every quote word for word.

  coop_call_actions.py --dir <call folder> [--model qwen3:8b] [--url http://127.0.0.1:11434] [--window-chars 3500]
                       [--overlap-chars 500] [--timeout 300] [--report <path>] [--dry-run] [--raw]
  coop_call_actions.py --dir <call folder> --clean [same options]   (clean only: writes transcript-clean.md and clean-report.md, then stops)
  coop_call_actions.py --dir <call folder> --compare                (no model, no network: counts of how transcript.md matches transcript.srt)
  coop_call_actions.py --selftest

Default mode reads transcript-clean.md in the call folder; when it is missing or older than transcript.srt the clean step runs first.
--raw reads the raw captions (transcript.srt, checked against transcript.md) exactly as before and is the only way to do so.

Co-op call recordings and transcripts go only to a model we run. This tool talks to a local Ollama and nothing else: it refuses any
URL whose host is not 127.0.0.1 or localhost, ignores proxy settings, and never prints a word of the transcript or of the model's
answer (counts and file paths only, also in errors).

Method (the model is never trusted; a program checks it):
  0. Cleaning (deletion only): transcript.srt is cut into windows and each numbered caption line goes to the model, which may only
     delete words (filler, the first copy of a stutter, an abandoned half-sentence). A program then checks each answer: the cleaned
     words must be a left-to-right subset of the raw words. The file gets the RAW tokens that survive, so the model's own spelling
     never reaches it; an answer that adds, rewords or reorders anything, or empties a line, is thrown away and the raw line kept.
     Every cue stays, with its time. transcript-clean.md (mode 600) holds "[hh:mm:ss] text" per cue; an older one is moved to
     transcript-clean.previous.md. clean-report.md (mode 600) line 1 is DONE or BLOCKED with the counts. transcript.srt and
     transcript.md are never written.
  1. the cleaned cues (or, with --raw, transcript.srt) are cut into windows of whole cues (about --window-chars characters, each
     overlapping the one before by about --overlap-chars). With --raw, transcript.md must be the same call as text.
  2. each window, one at a time and in order, goes to Ollama /api/chat (stream false, format json, temperature 0, num_ctx 8192,
     thinking off, keep_alive 5m) and comes back as {"items":[...]}: kind (decision, owes, topic, open_question), who, likely, card,
     quote. Invalid JSON is retried once; then the window is skipped and counted.
  3. the checker keeps an item only when its quote (case-folded, punctuation dropped, whitespace collapsed) is found in the window
     AND in the whole transcript and is at least 6 words long. The time is the start of the cue where the match begins, and must
     be the start second of a cue in the raw transcript.srt. Duplicates (same normalised quote) keep the earliest.
  4. actions.md (mode 600) in the call folder holds four sections, every line a checked quote with its time. An older actions.md is
     moved to actions.previous.md first. report.md (mode 600) line 1 is DONE or BLOCKED with the counts.

Use when: a co-op call (Greg, William, the Tuesday sysadmin call) has to be turned into decisions, owed work and card notes without the text ever reaching a vendor model; the hub runs it and reads only the counts.
Standard library only.
"""
from __future__ import annotations

import argparse
import bisect
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_URL = "http://127.0.0.1:11434"
KINDS = ("decision", "owes", "topic", "open_question")
WHO = ("Jonathan", "Greg", "William", "unknown")
CARDS = {
    "23": "old database copies",
    "147": "calendar",
    "174": "website security updates",
    "171": "hosting policy and Listmonk",
    "172": "sample files",
    "153": "git server",
    "154": "task tools",
    "156": "Fediverse",
    "69": "member Nextclouds",
    "141": "deployment automation",
    "170": "Proxmox",
    "113": "transcripts",
    "Eduity": "Eduity",
}
MIN_WORDS = 6
HEADER_LINE = "This file was produced by a local model on this machine, and every quote below was checked word for word against the transcript."


class Blocked(Exception):
    pass


# ---------------------------------------------------------------- text helpers

def normalise(text: str) -> str:
    t = re.sub(r"[^\w\s]|_", " ", text.casefold())
    return re.sub(r"\s+", " ", t).strip()


def fmt_time(seconds: float) -> str:
    s = int(seconds)
    return "%02d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)


_TIME_RE = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(text: str) -> list[dict]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        for pos, ln in enumerate(lines):
            m = _TIME_RE.search(ln)
            if m:
                body = " ".join(x.strip() for x in lines[pos + 1:]).strip()
                if body:
                    g = m.groups()
                    cues.append({"start": _secs(*g[0:4]), "end": _secs(*g[4:8]), "text": body})
                break
    for n, c in enumerate(cues):
        c["n"] = n
    return cues


def build_windows(cues: list[dict], window_chars: int, overlap_chars: int) -> list[tuple[int, int]]:
    """(first cue, one past the last cue) per window, whole cues only."""
    n = len(cues)
    out: list[tuple[int, int]] = []
    if n == 0:
        return out
    i = 0
    while True:
        j, total = i, 0
        while j < n and total < window_chars:
            total += len(cues[j]["text"]) + 1
            j += 1
        out.append((i, j))
        if j >= n:
            break
        k, ov = j, 0
        while k - 1 > i and ov < overlap_chars:
            k -= 1
            ov += len(cues[k]["text"]) + 1
        i = k
    return out


class Joined:
    """Normalised cue texts joined with one space, with the offset where each cue starts."""

    def __init__(self, cues: list[dict]):
        parts, self.offsets, pos = [], [], 0
        self.cues = cues
        for c in cues:
            t = normalise(c["text"])
            self.offsets.append(pos)
            parts.append(t)
            pos += len(t) + 1
        self.text = " ".join(parts)
        self.padded = " " + self.text + " "

    def find(self, quote_norm: str):
        """Cue (dict) where the quote begins, or None."""
        idx = self.padded.find(" " + quote_norm + " ")
        if idx < 0:
            return None
        k = bisect.bisect_right(self.offsets, idx) - 1
        # an empty cue shares its offset with the next one; walk forward to a cue whose text holds the start
        while k + 1 < len(self.offsets) and self.offsets[k + 1] <= idx:
            k += 1
        return self.cues[max(k, 0)]


def subset_keep(raw: str, cleaned: str):
    """The raw tokens that survive when `cleaned` is a left-to-right subset of the raw words, else None."""
    tokens = raw.split()
    flat = [(i, w) for i, tok in enumerate(tokens) for w in normalise(tok).split()]
    words = normalise(cleaned).split()
    if not words:
        return None
    p = 0
    used = set()
    for w in words:
        while p < len(flat) and flat[p][1] != w:
            p += 1
        if p >= len(flat):
            return None
        used.add(flat[p][0])
        p += 1
    return " ".join(tokens[i] for i in sorted(used))


def word_count(text: str) -> int:
    return len(normalise(text).split())


_CLEAN_MD_RE = re.compile(r"^\[(\d+):(\d\d):(\d\d)\]\s+(.*\S)\s*$")
_MD_TIME_RE = re.compile(r"\[\d+:\d\d:\d\d\]")


def parse_clean_md(text: str) -> list[dict]:
    cues = []
    for ln in text.splitlines():
        m = _CLEAN_MD_RE.match(ln)
        if m:
            start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            cues.append({"start": float(start), "end": float(start), "text": m.group(4), "n": len(cues)})
    return cues


def strip_md_times(text: str) -> str:
    """transcript.md puts [hh:mm:ss] before every cue; left in, normalise turns it into digits that break every run across a cue."""
    return _MD_TIME_RE.sub(" ", text)


# ---------------------------------------------------------------- Ollama

def check_url(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "http" or u.hostname not in ("127.0.0.1", "localhost") or u.username or u.password or u.path.strip("/") or u.query or u.fragment:
        raise SystemExit("coop_call_actions: refusing --url: only http://127.0.0.1[:port] or http://localhost[:port] is allowed (co-op text stays on this machine)")
    return "http://%s%s" % (u.hostname, (":%d" % u.port) if u.port else "")


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url: str, body, timeout: float):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_server(base: str, model: str) -> None:
    try:
        tags = http_json(base + "/api/tags", None, 10)
    except Exception as e:  # nothing of the reply is printed
        raise Blocked("no Ollama answers at %s (%s); start it, for example: docker start ollama-nx01" % (base, type(e).__name__))
    names = {m.get("name") for m in tags.get("models", []) if isinstance(m, dict)}
    if model not in names and model + ":latest" not in names:
        raise Blocked("model %s is not on the server at %s; pull it with: docker exec ollama-nx01 ollama pull %s" % (model, base, model))


PROMPT_SYSTEM = (
    "You read one stretch of a transcript of a spoken call between members of a small technology co-op (Jonathan, Greg, William). "
    "It is an automatic transcript with no speaker names; it may start or end in the middle of a sentence.\n"
    "Find only these four kinds of statements:\n"
    "- decision: something the speakers agreed or settled (\"we decided\", \"agreed\", \"let's do X\"). A question or a wish is not a decision.\n"
    "- owes: someone says a person will do a piece of work, or that a piece of work is still waiting for someone.\n"
    "- topic: something said about one of the topics listed below.\n"
    "- open_question: a real question about the co-op's work that nobody answers in this stretch. Closing small talk is not a question.\n"
    "Return one JSON object and nothing else: {\"items\": [ {\"kind\": ..., \"who\": ..., \"likely\": ..., \"card\": ..., \"quote\": ...} ]}\n"
    "who is one of: Jonathan, Greg, William, unknown. Use a name only when the words or context point to that person (the person who will do the work, or who is speaking). Set likely to true when you inferred it from context, false when the words name the person plainly.\n"
    "card is the topic the statement is about, written exactly as one of these, or null when none fits: "
    + ", ".join('"%s"' % v for v in CARDS.values())
    + ". Match on meaning: a statement about phone calendars is \"calendar\"; one about the hosting bill is \"hosting policy and Listmonk\".\n"
    "quote is an exact copy of at least six consecutive words from the stretch. COPY WORDS EXACTLY. NEVER PARAPHRASE. NEVER INVENT. "
    "One item per statement. If the stretch holds nothing for these kinds, return {\"items\": []}. When unsure, leave it out."
)


def ask_window(base: str, model: str, text: str, timeout: float):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEM},
            {"role": "user", "content": "Transcript stretch:\n\n" + text + "\n\nReturn the JSON object now."},
        ],
        "stream": False,
        "format": "json",
        "think": False,
        "keep_alive": "5m",
        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 1500},
    }
    reply = http_json(base + "/api/chat", body, timeout)
    content = reply["message"]["content"]
    data = json.loads(content)
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("shape")
    return data["items"]


def read_window(base, model, text, timeout):
    """Items for one window, or None after two failed tries."""
    for _ in range(2):
        try:
            return ask_window(base, model, text, timeout)
        except Exception:
            continue
    return None


CLEAN_SYSTEM = (
    "You are given numbered caption lines from an automatic transcript of a spoken call. For each line, return the same line with words DELETED only.\n"
    "You may delete: filler (um, uh, er, you know, \"like\" used as filler, \"okay so\", \"right?\" used as a tic), the first copy of a stutter or repeat "
    "(\"my my\" becomes \"my\"), and a clearly abandoned half-sentence.\n"
    "You must NOT reword, correct, reorder, add or translate any word. You must NOT drop a line for being off-topic or small talk (a tangent can hold a request). "
    "You must keep every line.\n"
    "Reply with one JSON object and nothing else: {\"lines\":[{\"n\":<number>,\"text\":\"<the line after deletions>\"}]}"
)


def ask_clean_window(base: str, model: str, numbered_text: str, timeout: float):
    """{n: text} for one window of numbered lines; raises when the answer is not the agreed shape."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": CLEAN_SYSTEM},
            {"role": "user", "content": "Numbered caption lines:\n\n" + numbered_text + "\n\nReturn the JSON object now."},
        ],
        "stream": False,
        "format": "json",
        "think": False,
        "keep_alive": "5m",
        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 3000},
    }
    reply = http_json(base + "/api/chat", body, timeout)
    data = json.loads(reply["message"]["content"])
    if isinstance(data, dict):
        data = data.get("lines")
    if not isinstance(data, list):
        raise ValueError("shape")
    out = {}
    for el in data:
        if not isinstance(el, dict) or not isinstance(el.get("n"), int) or isinstance(el.get("n"), bool) or not isinstance(el.get("text"), str):
            raise ValueError("shape")
        out[el["n"]] = el["text"]
    return out


def read_clean_window(base, model, numbered_text, timeout):
    """{n: text} for one window, or None after two failed tries."""
    for _ in range(2):
        try:
            return ask_clean_window(base, model, numbered_text, timeout)
        except Exception:
            continue
    return None


def clean_cues(base, model, cues, window_chars, overlap_chars, timeout):
    """(one "[time] text" line per cue, stats). Deletion only: a cue's text is the raw tokens that survive the subset check."""
    stats = {"cues": len(cues), "windows_read": 0, "windows_skipped": 0, "words_kept": 0, "words_dropped": 0,
             "cues_kept_raw": 0, "cues_failing_subset": 0}
    windows = build_windows(cues, window_chars, overlap_chars)
    result: dict[int, str] = {}
    for w, (a, b) in enumerate(windows, 1):
        sub = cues[a:b]
        answer = read_clean_window(base, model, "\n".join("%d: %s" % (c["n"], c["text"]) for c in sub), timeout)
        if answer is None:
            stats["windows_skipped"] += 1
            print("clean window %d/%d skipped" % (w, len(windows)), flush=True)
        else:
            stats["windows_read"] += 1
            print("clean window %d/%d read" % (w, len(windows)), flush=True)
        for c in sub:
            if c["n"] in result:  # decided by the first window that held it
                continue
            raw = c["text"]
            kept = None
            if answer is not None and c["n"] in answer:
                kept = subset_keep(raw, answer[c["n"]])
                if kept is None:
                    stats["cues_failing_subset"] += 1
            if kept is None:
                stats["cues_kept_raw"] += 1
                stats["words_kept"] += word_count(raw)
                result[c["n"]] = raw
            else:
                stats["words_kept"] += word_count(kept)
                stats["words_dropped"] += word_count(raw) - word_count(kept)
                result[c["n"]] = kept
    lines = ["[%s] %s" % (fmt_time(c["start"]), result[c["n"]]) for c in cues]
    return lines, stats


# ---------------------------------------------------------------- checker

_CARD_BY_NAME = {normalise(v): k for k, v in CARDS.items()}


def _card_key(value):
    """The card number (or "Eduity") for a topic name or number the model gave, else None."""
    if value is None:
        return None
    v = str(value).strip()
    if v in CARDS:
        return v
    return _CARD_BY_NAME.get(normalise(v))


def clean_item(raw):
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    quote = raw.get("quote")
    if kind not in KINDS or not isinstance(quote, str):
        return None
    who = raw.get("who") if raw.get("who") in WHO else "unknown"
    card = _card_key(raw.get("card"))
    return {"kind": kind, "who": who, "likely": bool(raw.get("likely")), "card": card, "quote": quote.strip()}


def check_items(raw_items, window: Joined, whole_norm_padded: str, md_norm_padded, counts: dict, raw_secs=None) -> list[dict]:
    kept = []
    for raw in raw_items:
        it = clean_item(raw)
        if it is None:
            counts["bad_shape"] += 1
            continue
        qn = normalise(it["quote"])
        if len(qn.split()) < MIN_WORDS:
            counts["too_short"] += 1
            continue
        cue = window.find(qn)
        if cue is None:
            counts["not_in_window"] += 1
            continue
        needle = " " + qn + " "
        if needle not in whole_norm_padded or (md_norm_padded is not None and needle not in md_norm_padded):
            counts["not_in_transcript"] += 1
            continue
        if raw_secs is not None and int(cue["start"]) not in raw_secs:
            counts["not_in_raw"] += 1
            continue
        it["qn"] = qn
        it["start"] = cue["start"]
        kept.append(it)
    return kept


def dedupe(items: list[dict], counts: dict) -> list[dict]:
    best: dict[str, dict] = {}
    for it in items:
        cur = best.get(it["qn"])
        if cur is None:
            best[it["qn"]] = it
        else:
            counts["duplicate"] += 1
            if it["start"] < cur["start"]:
                best[it["qn"]] = it
    uniq = list(best.values())
    out = []
    for it in uniq:
        # the same words again inside a longer quote of the same kind and card: keep the longer one
        if any(o is not it and o["kind"] == it["kind"] and o["card"] == it["card"] and (" " + it["qn"] + " ") in (" " + o["qn"] + " ") for o in uniq):
            counts["duplicate"] += 1
            continue
        out.append(it)
    return sorted(out, key=lambda x: (x["start"], x["qn"]))


def md_matches_srt(md_norm: str, srt_norm: str) -> bool:
    """transcript.md is the same call when most of the srt's 6-word runs appear in it."""
    words = srt_norm.split()
    if len(words) < MIN_WORDS:
        return False
    padded = " " + md_norm + " "
    step = max(1, len(words) // 200)
    tried = hit = 0
    for i in range(0, len(words) - MIN_WORDS + 1, step):
        tried += 1
        if " " + " ".join(words[i:i + MIN_WORDS]) + " " in padded:
            hit += 1
    return tried > 0 and hit / tried >= 0.5


# ---------------------------------------------------------------- output

def line(it: dict) -> str:
    return "- [%s] \"%s\"" % (fmt_time(it["start"]), it["quote"].replace("\n", " "))


def render_actions(items: list[dict]) -> str:
    out = [HEADER_LINE, ""]
    out.append("## 1. Decisions made on the call")
    dec = [i for i in items if i["kind"] == "decision"]
    out += [line(i) for i in dec] or ["nothing found"]
    out += ["", "## 2. Who owes what"]
    owes = [i for i in items if i["kind"] == "owes"]
    if not owes:
        out.append("nothing found")
    else:
        for who in WHO:
            group = [i for i in owes if i["who"] == who]
            if not group:
                continue
            out += ["", "### " + who]
            for i in group:
                mark = " (likely)" if i["likely"] and who != "unknown" else ""
                out.append(line(i) + mark)
    out += ["", "## 3. Anything said about each card topic"]
    for key, label in CARDS.items():
        title = "Eduity" if key == "Eduity" else "Card %s, %s" % (key, label)
        out += ["", "### " + title]
        group = [i for i in items if i["card"] == key]
        out += [line(i) for i in group] or ["nothing found"]
    other = [i for i in items if i["kind"] == "topic" and i["card"] is None]
    if other:
        out += ["", "### Other topics (no card)"] + [line(i) for i in other]
    out += ["", "## 4. Open questions nobody answered"]
    q = [i for i in items if i["kind"] == "open_question"]
    out += [line(i) for i in q] or ["nothing found"]
    return "\n".join(out) + "\n"


def write_private(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def write_report(path: Path, first: str, rest: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, first + "\n" + "\n".join(rest) + ("\n" if rest else ""))


# ---------------------------------------------------------------- main flow

def write_clean(call: Path, lines: list[str]) -> Path:
    """transcript-clean.md (mode 600); an older one moves to transcript-clean.previous.md first. The srt and md are never opened for writing."""
    path = call / "transcript-clean.md"
    if path.exists():
        os.replace(path, call / "transcript-clean.previous.md")
        os.chmod(call / "transcript-clean.previous.md", 0o600)
    write_private(path, "\n".join(lines) + "\n")
    return path


def clean_step(args, call: Path, base: str, cues: list[dict]) -> dict:
    lines, stats = clean_cues(base, args.model, cues, args.window_chars, args.overlap_chars, args.timeout)
    if stats["windows_read"] == 0:
        raise Blocked("no clean window could be read (%d skipped); check the model and the timeout" % stats["windows_skipped"])
    write_clean(call, lines)
    return stats


def _clean_counts(s: dict) -> str:
    return "cues %d, words kept %d, words dropped %d, cues kept raw %d, cues failing subset check %d" % (
        s["cues"], s["words_kept"], s["words_dropped"], s["cues_kept_raw"], s["cues_failing_subset"])


def _read_srt_cues(call: Path) -> list[dict]:
    srt = call / "transcript.srt"
    if not srt.is_file():
        raise Blocked("no transcript.srt in %s" % call)
    cues = parse_srt(srt.read_text(encoding="utf-8", errors="replace"))
    if not cues:
        raise Blocked("transcript.srt in %s holds no cues" % call)
    return cues


def run_clean(args) -> int:
    call = Path(os.path.expanduser(args.dir)).resolve()
    report = Path(os.path.expanduser(args.report)) if args.report else call / "clean-report.md"
    base = check_url(args.url)
    try:
        cues = _read_srt_cues(call)
        if args.dry_run:
            print("dry run: %d cues, %d windows would be cleaned, no model called" % (len(cues), len(build_windows(cues, args.window_chars, args.overlap_chars))))
            return 0
        check_server(base, args.model)
        st = clean_step(args, call, base, cues)
        first = "DONE clean cues %d, windows read %d, windows skipped %d, words kept %d, words dropped %d, cues kept raw %d, cues failing subset check %d" % (
            st["cues"], st["windows_read"], st["windows_skipped"], st["words_kept"], st["words_dropped"], st["cues_kept_raw"], st["cues_failing_subset"])
        rest = ["transcript-clean.md: %s (mode 600)" % (call / "transcript-clean.md")]
        write_report(report, first, rest)
        print(first)
        print("\n".join(rest))
        print("report: %s" % report)
        return 0
    except Blocked as e:
        first = "BLOCKED %s" % e
        try:
            write_report(report, first, [])
        except OSError:
            pass
        print(first)
        return 3


def run_compare(args) -> int:
    call = Path(os.path.expanduser(args.dir)).resolve()
    try:
        srt, md = call / "transcript.srt", call / "transcript.md"
        for f in (srt, md):
            if not f.is_file():
                raise Blocked("no %s in %s" % (f.name, call))
        srt_cues = parse_srt(srt.read_text(encoding="utf-8", errors="replace"))
        md_cues = parse_clean_md(md.read_text(encoding="utf-8", errors="replace"))
        # same-second cues are compared as one group (their normalised texts joined), and each member is counted with its group
        by_srt: dict[int, list[str]] = {}
        by_md: dict[int, list[str]] = {}
        for c in srt_cues:
            by_srt.setdefault(int(c["start"]), []).append(c["text"])
        for c in md_cues:
            by_md.setdefault(int(c["start"]), []).append(c["text"])
        same = differing = missing = 0
        for sec, texts in by_srt.items():
            if sec not in by_md:
                missing += len(texts)
            elif normalise(" ".join(texts)) == normalise(" ".join(by_md[sec])):
                same += len(texts)
            else:
                differing += len(texts)
        extra = sum(len(t) for sec, t in by_md.items() if sec not in by_srt)
        print("compare: srt cues %d, md cues %d, same %d, differing %d, missing %d, extra %d" % (len(srt_cues), len(md_cues), same, differing, missing, extra))
        return 0
    except Blocked as e:
        print("BLOCKED %s" % e)
        return 3


def run(args) -> int:
    call = Path(os.path.expanduser(args.dir)).resolve()
    actions = call / "actions.md"
    report = Path(os.path.expanduser(args.report)) if args.report else call / "report.md"
    base = check_url(args.url)
    try:
        raw_cues = _read_srt_cues(call)
        raw_secs = {int(c["start"]) for c in raw_cues}
        clean_path = call / "transcript-clean.md"
        need_clean = not args.raw and (not clean_path.is_file() or clean_path.stat().st_mtime < (call / "transcript.srt").stat().st_mtime)
        if args.raw:
            print("RAW: reading the raw captions (--raw), not the cleaned file")
        if args.dry_run:
            if args.raw or need_clean:
                n = len(build_windows(raw_cues, args.window_chars, args.overlap_chars))
                print("dry run: %d cues, %d windows would be read, no model called" % (len(raw_cues), n)
                      + ("" if args.raw else "; the clean step would run first"))
            else:
                cc = parse_clean_md(clean_path.read_text(encoding="utf-8", errors="replace"))
                print("dry run: %d cues, %d windows would be read, no model called" % (len(cc), len(build_windows(cc, args.window_chars, args.overlap_chars))))
            return 0
        check_server(base, args.model)
        clean_stats = None
        md_padded = None
        if args.raw:
            cues = raw_cues
            reading = "RAW captions (--raw)"
            whole = Joined(cues)
            md_path = call / "transcript.md"
            md_note = "transcript.md missing: checked against transcript.srt only"
            if md_path.is_file():
                md_norm = normalise(strip_md_times(md_path.read_text(encoding="utf-8", errors="replace")))
                if md_matches_srt(md_norm, whole.text):
                    md_padded, md_note = " " + md_norm + " ", "transcript.md checked"
                else:
                    md_note = "transcript.md differs from transcript.srt: checked against transcript.srt only"
        else:
            if need_clean:
                clean_stats = clean_step(args, call, base, raw_cues)
            cues = parse_clean_md(clean_path.read_text(encoding="utf-8", errors="replace"))
            if not cues:
                raise Blocked("transcript-clean.md in %s holds no cues" % call)
            reading = "transcript-clean.md"
            md_note = "quotes checked against transcript-clean.md"
            whole = Joined(cues)
        windows = build_windows(cues, args.window_chars, args.overlap_chars)
        counts = {"bad_shape": 0, "too_short": 0, "not_in_window": 0, "not_in_transcript": 0, "not_in_raw": 0, "duplicate": 0}
        read = skipped = 0
        collected: list[dict] = []
        for n, (a, b) in enumerate(windows, 1):
            sub = cues[a:b]
            text = "\n".join(c["text"] for c in sub)
            raw = read_window(base, args.model, text, args.timeout)
            if raw is None:
                skipped += 1
                print("window %d/%d skipped" % (n, len(windows)), flush=True)
                continue
            read += 1
            kept = check_items(raw, Joined(sub), whole.padded, md_padded, counts, None if args.raw else raw_secs)
            collected += kept
            print("window %d/%d read: %d items, %d kept" % (n, len(windows), len(raw), len(kept)), flush=True)
        if read == 0:
            raise Blocked("no window could be read (%d skipped); check the model and the timeout" % skipped)
        final = dedupe(collected, counts)
        offered = sum(counts.values()) + len(final)
        dropped = offered - len(final)
        if actions.exists():
            os.replace(actions, call / "actions.previous.md")
            os.chmod(call / "actions.previous.md", 0o600)
        write_private(actions, render_actions(final))
        first = "DONE windows read %d, windows skipped %d, items kept %d, items dropped %d" % (read, skipped, len(final), dropped)
        rest = [
            "model %s on %s; %s" % (args.model, base, md_note),
            "reading: %s" % reading,
        ]
        if clean_stats is not None:
            rest.append("clean: " + _clean_counts(clean_stats))
        rest += [
            "dropped: %s" % ", ".join("%s %d" % (k.replace("_", " "), v) for k, v in counts.items()),
            "actions: %s (mode 600)" % actions,
        ]
        write_report(report, first, rest)
        print(first)
        print("\n".join(rest))
        print("report: %s" % report)
        return 0
    except Blocked as e:
        first = "BLOCKED %s" % e
        try:
            write_report(report, first, [])
        except OSError:
            pass
        print(first)
        return 3


# ---------------------------------------------------------------- self-test

SELF_CUES = [
    "so uh we were looking at the calendar thing",
    "and Mira said the archive migration will happen in december",
    "yes the purple zeppelin database copies get deleted after the backup check",
    "okay that is decided then we move on",
    "Pavel will send the hosting policy draft to everyone by friday afternoon",
    "does anybody know who owns the green lantern git server account",
    "I think Tobias might look at the task board but nobody said so",
    "right so the Fediverse bridge is waiting for the new certificate to arrive",
    "no no the proxmox host stays exactly where it is for now",
    "anyway that was the whole agenda for today",
    "see you all next tuesday evening for the call",
    "thanks everybody have a good night now",
]

TRUE_QUOTES = [
    # (quote as the stub sends it, cue index where it truly begins)
    ("the purple zeppelin database copies get deleted after the backup check", 2),
    ("Pavel will send the hosting policy draft to everyone by friday afternoon", 4),
    ("Does anybody know who owns the green lantern git server account?", 5),  # case and punctuation differ: kept
    ("the Fediverse bridge is waiting for the new certificate to arrive", 7),
    ("and Mira said the archive migration will happen in december", 1),
]
BAD_QUOTES = [
    "the zeppelin database copies will be deleted once backups are checked",  # paraphrase
    "Pavel promised to send the hosting policy draft to the whole team by friday",  # paraphrase
    "we agreed that the invoice for the quantum espresso machine is approved",  # invented
    "yes the purple zeppelin",  # true but under six words
]


def _selftest_srt() -> str:
    out = []
    for i, t in enumerate(SELF_CUES):
        s = i * 7
        out.append("%d\n00:%02d:%02d,000 --> 00:%02d:%02d,500\n%s\n" % (i + 1, s // 60, s % 60, (s + 5) // 60, (s + 5) % 60, t))
    return "\n".join(out)


class _Stub(http.server.BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, format, *args):
        pass

    def _send(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send({"models": [{"name": self.state["tag"]}]})
        else:
            self._send({}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
        st = self.state
        opts = body.get("options", {})
        is_clean = body.get("messages", [{}])[0].get("content") == CLEAN_SYSTEM
        st["clean_calls" if is_clean else "calls"] += 1
        if not (body.get("stream") is False and body.get("format") == "json" and body.get("think") is False
                and opts.get("temperature") == 0 and opts.get("num_ctx") == 8192 and body.get("keep_alive") == "5m"
                and (not is_clean or opts.get("num_predict") == 3000)):
            st["bad_request"] = True
        if is_clean:
            lines = []
            for ln in body["messages"][1]["content"].splitlines():
                m = re.match(r"^(\d+): (.*)$", ln)
                if not m:
                    continue
                kept = []
                for w in m.group(2).split():
                    base_w = normalise(w)
                    if base_w in ("um", "uh") or (kept and normalise(kept[-1]) == base_w and base_w):
                        continue
                    kept.append(w)
                text = " ".join(kept)
                if "trapcue" in text:  # the trap: an added word the checker must refuse
                    text += " extraword"
                lines.append({"n": int(m.group(1)), "text": text})
            return self._send({"message": {"content": json.dumps({"lines": lines})}})
        if st["calls"] in st["fail_calls"]:  # an answer that is not JSON: call 1 must be retried, the last window fails twice and is skipped
            return self._send({"message": {"content": "this is not json {"}})
        items = [{"kind": "decision", "who": "Jonathan", "likely": False, "card": 23, "quote": TRUE_QUOTES[0][0]},
                 {"kind": "owes", "who": "Greg", "likely": True, "card": "171", "quote": TRUE_QUOTES[1][0]},
                 {"kind": "open_question", "who": "unknown", "likely": False, "card": 153, "quote": TRUE_QUOTES[2][0]},
                 {"kind": "topic", "who": "William", "likely": False, "card": 156, "quote": TRUE_QUOTES[3][0]},
                 {"kind": "topic", "who": "unknown", "likely": False, "card": None, "quote": TRUE_QUOTES[4][0]},
                 # the same true quote again (a second window, or twice in one answer): must be removed as a duplicate
                 {"kind": "owes", "who": "Greg", "likely": True, "card": "171", "quote": TRUE_QUOTES[1][0]},
                 # the same words as a longer quote of the same kind and card: dropped, the longer one stays
                 {"kind": "owes", "who": "Greg", "likely": True, "card": "171", "quote": "Pavel will send the hosting policy draft to everyone"},
                 {"kind": "bogus", "quote": "this item has no real kind at all so it is dropped"}]
        items += [{"kind": "decision", "who": "Greg", "likely": False, "card": None, "quote": q} for q in BAD_QUOTES]
        self._send({"message": {"content": json.dumps({"items": items})}})


def _fail(msg: str):
    print("SELFTEST FAIL: " + msg)
    sys.exit(1)


def selftest() -> int:
    me = str(Path(__file__).resolve())
    secret_words = ["zeppelin", "Pavel", "lantern", "Fediverse", "Mira", "espresso"]

    def start(tag, fail=()):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        _Stub.state = {"calls": 0, "clean_calls": 0, "tag": tag, "bad_request": False, "fail_calls": set(fail)}
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Stub)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, port

    def run_tool(*argv):
        return subprocess.run([sys.executable, "-I", me, *argv], capture_output=True, text=True, timeout=120)

    with tempfile.TemporaryDirectory() as tmp:
        # (a) the clean step alone, on a small invented call, through the stub
        cc = Path(tmp) / "cleancall"
        cc.mkdir()
        clean_raw = ["um so the plan plan is to uh review the budget",
                     "we need need the trapcue sample by monday",
                     "okay thanks everybody",
                     "uh uh I will send the notes",
                     "that is all for now"]
        srt_text = "\n".join("%d\n00:00:%02d,000 --> 00:00:%02d,500\n%s\n" % (i + 1, i * 4, i * 4 + 3, t) for i, t in enumerate(clean_raw))
        (cc / "transcript.srt").write_text(srt_text)
        srt_before = (cc / "transcript.srt").read_bytes()
        srv, port = start(DEFAULT_MODEL)
        c = run_tool("--dir", str(cc), "--url", "http://127.0.0.1:%d" % port, "--clean", "--timeout", "20")
        srv.shutdown()
        if c.returncode != 0:
            _fail("clean exit %d" % c.returncode)
        cf = cc / "transcript-clean.md"
        if not cf.is_file() or oct(cf.stat().st_mode & 0o777) != "0o600":
            _fail("transcript-clean.md missing or not mode 600")
        ctext = cf.read_text()
        if "trapcue sample by monday" not in ctext or "extraword" in ctext:
            _fail("the trap cue was not kept raw")
        rep1 = (cc / "clean-report.md").read_text().splitlines()[0]
        if not rep1.startswith("DONE clean cues 5"):
            _fail("clean report line 1: " + rep1)
        kd = re.search(r"words kept (\d+), words dropped (\d+)", rep1)
        total = sum(word_count(t) for t in clean_raw)
        if not kd or int(kd.group(1)) + int(kd.group(2)) != total or int(kd.group(2)) == 0:
            _fail("clean word counts do not add up: " + rep1)
        if (cc / "transcript.srt").read_bytes() != srt_before:
            _fail("transcript.srt changed")
        for w in ("trapcue", "budget", "notes", "sample", "monday", "everybody"):
            if w in (c.stdout + c.stderr).lower():
                _fail("clean stdout or stderr held call text: " + w)

        call = Path(tmp) / "call"
        call.mkdir()
        (call / "transcript.srt").write_text(_selftest_srt())
        (call / "transcript.md").write_text("# Call\n\n" + "\n\n".join(SELF_CUES) + "\n")
        # window sizes small enough that several windows overlap
        srv, port = start(DEFAULT_MODEL)
        flags = ["--dir", str(call), "--url", "http://127.0.0.1:%d" % port, "--window-chars", "200", "--overlap-chars", "80", "--timeout", "20"]
        # the clean file first (the stub drops um/uh and a repeated word only), then the actions read it
        cr = run_tool(*flags, "--clean")
        if cr.returncode != 0 or _Stub.state["clean_calls"] < 1:
            _fail("clean of the main call: %d %s" % (cr.returncode, cr.stdout))
        clean_lines = (call / "transcript-clean.md").read_text().splitlines()
        if len(clean_lines) != len(SELF_CUES) or "so we were looking at the calendar thing" not in clean_lines[0] or "uh" in clean_lines[0].split():
            _fail("clean file of the main call")
        for i, ln in enumerate(clean_lines):
            if i not in (0, 8) and not ln.endswith(SELF_CUES[i]):
                _fail("a cue without filler was changed: %d" % i)
        d = run_tool(*flags, "--dry-run")
        if d.returncode != 0 or "windows would be read" not in d.stdout:
            _fail("dry run: %r" % d.stdout)
        found = re.search(r"(\d+) windows", d.stdout)
        n_windows = int(found.group(1)) if found else 0
        if n_windows < 3:
            _fail("expected several windows, got %d" % n_windows)
        if _Stub.state["calls"] != 0:
            _fail("dry run called the model")

        # call 1 answers with broken JSON (retried once, then fine); the last window fails twice (skipped and counted)
        _Stub.state["fail_calls"].update((1, n_windows + 1, n_windows + 2))
        clean_before = _Stub.state["clean_calls"]
        r = run_tool(*flags)
        if r.returncode != 0:
            _fail("run exit %d: %s%s" % (r.returncode, r.stdout, r.stderr))
        if _Stub.state["clean_calls"] != clean_before:
            _fail("the actions run cleaned again though the clean file was fresh")
        if _Stub.state["bad_request"]:
            _fail("request was not stream false, format json, think false, temperature 0, num_ctx 8192, keep_alive 5m (num_predict 3000 for clean)")
        text = (call / "actions.md").read_text()
        for w in secret_words:
            if w.lower() in (r.stdout + r.stderr).lower():
                _fail("stdout or stderr held quote text: " + w)
        if oct((call / "actions.md").stat().st_mode & 0o777) != "0o600" or oct((call / "report.md").stat().st_mode & 0o777) != "0o600":
            _fail("actions.md and report.md must be mode 600")
        if not text.splitlines()[0].startswith("This file was produced by a local model"):
            _fail("first line of actions.md")
        for head in ("## 1. Decisions made on the call", "## 2. Who owes what", "## 3. Anything said about each card topic", "## 4. Open questions nobody answered"):
            if head not in text:
                _fail("missing section " + head)
        # true quotes kept with the cue's start time (cue i starts at i * 7 seconds)
        for q, cue in TRUE_QUOTES:
            stamp = "[%s]" % fmt_time(cue * 7)
            ln = [x for x in text.splitlines() if q in x and x.startswith("- " + stamp)]
            if not ln:
                _fail("true quote missing or wrong time: cue %d" % cue)
        for q in BAD_QUOTES:
            if q in text:
                _fail("a paraphrased, invented or short quote was kept")
        if "everyone\"" in text:
            _fail("a quote contained in a longer one was kept")
        if "this item has no real kind" in text:
            _fail("an item with a bad kind was kept")
        # duplicates: each true quote shows once in section 2 or once per section it belongs in, never twice in the same section
        pavel = [x for x in text.splitlines() if TRUE_QUOTES[1][0] in x]
        if len(pavel) != 2:  # section 2 (owes, Greg) and section 3 (card 171) once each
            _fail("duplicate not removed: %d lines" % len(pavel))
        if "(likely)" not in pavel[0]:
            _fail("likely mark missing")
        first = (call / "report.md").read_text().splitlines()[0]
        if not first.startswith("DONE windows read %d, windows skipped 1, " % (n_windows - 1)):
            _fail("report line 1: " + first)
        # a second run moves the old actions.md aside
        r2 = run_tool(*flags)
        if r2.returncode != 0 or not (call / "actions.previous.md").is_file() or oct((call / "actions.previous.md").stat().st_mode & 0o777) != "0o600":
            _fail("actions.previous.md")
        if _Stub.state["clean_calls"] != clean_before:
            _fail("the second actions run cleaned again")
        rw = run_tool(*flags, "--raw")
        if rw.returncode != 0 or "RAW: reading the raw captions" not in rw.stdout:
            _fail("--raw run: %d" % rw.returncode)
        srv.shutdown()

        # refuses a host that is not this machine, before anything is read
        bad = run_tool("--dir", str(call), "--url", "http://192.0.2.1:11434")
        if bad.returncode == 0 or "refusing" not in (bad.stdout + bad.stderr):
            _fail("a non-local URL was not refused")
        bad2 = run_tool("--dir", str(call), "--url", "http://127.0.0.1@evil.example:11434")
        if bad2.returncode == 0:
            _fail("a URL with userinfo was not refused")

        # model missing: BLOCKED with the pull command
        srv, port = start("other:1b")
        m = run_tool("--dir", str(call), "--url", "http://127.0.0.1:%d" % port, "--report", str(Path(tmp) / "rep2.md"))
        rep = (Path(tmp) / "rep2.md").read_text().splitlines()[0]
        if m.returncode == 0 or not rep.startswith("BLOCKED") or "ollama pull qwen3:8b" not in rep:
            _fail("missing model: " + rep)
        srv.shutdown()
        # nothing listening: BLOCKED
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        dead = sock.getsockname()[1]
        sock.close()
        u = run_tool("--dir", str(call), "--url", "http://127.0.0.1:%d" % dead, "--report", str(Path(tmp) / "rep3.md"))
        if u.returncode == 0 or not (Path(tmp) / "rep3.md").read_text().startswith("BLOCKED"):
            _fail("unreachable server")
        cmp_ = run_tool("--dir", str(call), "--compare")
        if cmp_.returncode != 0 or len(cmp_.stdout.splitlines()) != 1 or not cmp_.stdout.startswith("compare:"):
            _fail("--compare: %r" % cmp_.stdout)
    print("SELFTEST OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read a co-op call transcript on a local model and keep only quotes that check out word for word.")
    ap.add_argument("--dir", help="call folder holding transcript.srt and transcript.md")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--url", default=DEFAULT_URL, help="local Ollama only (127.0.0.1 or localhost)")
    ap.add_argument("--window-chars", type=int, default=3500)
    ap.add_argument("--overlap-chars", type=int, default=500)
    ap.add_argument("--timeout", type=float, default=300, help="seconds per window")
    ap.add_argument("--report", help="where to write report.md (default: next to actions.md)")
    ap.add_argument("--dry-run", action="store_true", help="count the windows, call no model")
    ap.add_argument("--clean", action="store_true", help="clean only: write transcript-clean.md and clean-report.md, then stop")
    ap.add_argument("--raw", action="store_true", help="read the raw captions (transcript.srt), not transcript-clean.md")
    ap.add_argument("--compare", action="store_true", help="counts of how transcript.md matches transcript.srt (no model, no network)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.dir:
        ap.error("--dir is required")
    if args.window_chars < 200 and not args.dry_run and args.window_chars < 50:
        ap.error("--window-chars is too small")
    if args.compare:
        return run_compare(args)
    if args.clean:
        return run_clean(args)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
