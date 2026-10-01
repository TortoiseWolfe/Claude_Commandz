#!/usr/bin/env python3
"""Transcribe a meeting recording (runs inside the container).

Pure logic (merge, collapse, formatting) has no heavy imports so the unit
tests can import this file on a host without faster-whisper or a GPU.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile


def fmt_hms(seconds):
    s = int(max(0, seconds))
    return "%02d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)


def fmt_srt_time(seconds):
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def merge_segments(segments):
    """Merge segments from all tracks, ordered by start (stable on end)."""
    return sorted(segments, key=lambda g: (g["start"], g["end"]))


def collapse_turns(segments):
    """Join consecutive same-speaker segments into one turn."""
    turns = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if turns and turns[-1]["speaker"] == seg["speaker"]:
            turns[-1]["end"] = max(turns[-1]["end"], seg["end"])
            turns[-1]["text"] += " " + text
        else:
            turns.append({"start": seg["start"], "end": seg["end"],
                          "speaker": seg["speaker"], "text": text})
    return turns


def to_srt(segments):
    out = []
    for i, g in enumerate(segments, 1):
        out.append("%d\n%s --> %s\n%s: %s\n" % (
            i, fmt_srt_time(g["start"]), fmt_srt_time(g["end"]),
            g["speaker"], g["text"].strip()))
    return "\n".join(out)


def to_markdown(turns, source, duration, model, device, when=None):
    when = when or datetime.date.today().isoformat()
    lines = ["# Transcript", "",
             "- Source: %s" % source,
             "- Date: %s" % when,
             "- Duration: %s" % fmt_hms(duration),
             "- Model: %s" % model,
             "- Device: %s" % device, ""]
    for t in turns:
        lines.append("**[%s] %s:** %s" % (fmt_hms(t["start"]), t["speaker"], t["text"]))
        lines.append("")
    return "\n".join(lines)


def speaker_names(arg, n):
    names = [s.strip() for s in (arg or "").split(",") if s.strip()]
    if n == 1 and not names:
        return ["Speaker"]
    return [names[i] if i < len(names) else "Speaker %d" % (i + 1) for i in range(n)]


def probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    return len(d.get("streams", [])), float(d.get("format", {}).get("duration") or 0)


def extract(path, track, wav):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-map",
                    "0:a:%d" % track, "-ac", "1", "-ar", "16000", wav], check=True)


def is_oom(exc):
    m = str(exc).lower()
    return "out of memory" in m or "cuda failed" in m or "cublas" in m


def load_model(name, device):
    from faster_whisper import WhisperModel
    ctype = "float16" if device == "cuda" else "int8"
    return WhisperModel(name, device=device, compute_type=ctype)


def run_tracks(model, wavs, names, language):
    segs = []
    for wav, who in zip(wavs, names):
        it, _info = model.transcribe(wav, language=language, vad_filter=True)
        for s in it:  # generator: OOM can surface here
            segs.append({"start": s.start, "end": s.end,
                         "speaker": who, "text": s.text.strip()})
    return segs


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", required=True)
    ap.add_argument("--speakers", default="")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--language", default=None)
    a = ap.parse_args(argv)

    n, duration = probe(a.input)
    if n < 1:
        sys.exit("no audio streams in %s" % a.input)
    names = speaker_names(a.speakers, n)
    os.makedirs(a.out, mode=0o700, exist_ok=True)
    os.chmod(a.out, 0o700)

    with tempfile.TemporaryDirectory() as tmp:
        wavs = []
        for i in range(n):
            w = os.path.join(tmp, "track%d.wav" % i)
            extract(a.input, i, w)
            wavs.append(w)
        device, used, segs = "cuda", a.model, None
        candidates = [a.model] + (["medium"] if a.model != "medium" else [])
        for cand in candidates:
            try:
                used = cand
                segs = run_tracks(load_model(cand, device), wavs, names, a.language)
                break
            except Exception as e:  # noqa: BLE001
                if is_oom(e) and cand != candidates[-1]:
                    print("CUDA OOM on %s, falling back to medium" % cand, file=sys.stderr)
                    continue
                raise
    # tmp wavs are removed by the context manager
    merged = merge_segments(segs)
    turns = collapse_turns(merged)
    src = os.path.basename(a.input)
    write_private(os.path.join(a.out, "transcript.md"),
                  to_markdown(turns, src, duration, used, device))
    write_private(os.path.join(a.out, "transcript.srt"), to_srt(merged))
    write_private(os.path.join(a.out, "transcript.json"),
                  json.dumps(merged, indent=2, ensure_ascii=False))
    print("model=%s device=%s tracks=%d duration=%.1fs" % (used, device, n, duration))


if __name__ == "__main__":
    main()
