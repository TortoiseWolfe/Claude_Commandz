#!/usr/bin/env python3
"""demo_video.py: one command turns a shot-list spec into a narrated PR demo video.

    demo_video.py run SPEC.json [--out DIR] [--stages audio,record,assemble] [--force-audio] [--heavy]
    demo_video.py validate SPEC.json
    demo_video.py doctor [--install]
    demo_video.py open FILE.mp4

`run` does three stages with a timeout on every external call:
  audio     Piper TTS (Docker image) says each line; one wav and its length per line
  record    Playwright (Docker image) drives the app at 1280x800 and writes a .webm and a timeline.json
  assemble  ffmpeg (host) burns captions and mixes the voice

Outputs in DIR (default ./out), NAME is the spec's name:
  NAME-demo.mp4  NAME-captions-only.mp4  NAME.srt  NAME-contact-sheet.png  NAME-result.json

The first caption of every video is "Narration: AI voice." and is added here, never by the spec author. The spec format is
documented in the demo-video skill (SKILL.md) and spec.template.json beside it. Nothing here signs in anywhere or uploads
anything: a sign-in the app needs is the spec's own setup hook, running against a local demo stack.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABEL = "Narration: AI voice."
PLAYWRIGHT_VERSION = "1.50.1"
PLAYWRIGHT_IMAGE = "mcr.microsoft.com/playwright:v%s-noble" % PLAYWRIGHT_VERSION
PIPER_IMAGE = os.environ.get("DEMO_VIDEO_PIPER_IMAGE", "piper-tts-local")
CACHE = Path(os.environ.get("DEMO_VIDEO_CACHE", Path.home() / ".cache" / "demo-video"))
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")
VOICE_RE = re.compile(r"^[a-z]{2}_[A-Z]{2}-[a-z0-9_]+-(low|medium|high|x_low)$")
GAP_DEFAULT = 0.7
CAPTION_TAIL = 0.5
OVERRAN_WARN = 1.0
DURATION_WARN = 1.0

TARGET_KEYS = ("css", "text", "role", "label", "placeholder", "testId")
TARGET_EXTRA = ("exact", "name", "within", "nth")
ACTIONS = {
    "goto": ("path",), "scroll_to": ("target",), "scroll": ("to",), "point": ("target",), "highlight": ("target",),
    "click": ("target",), "fill": ("target", "value"), "press": ("key",), "wait": ("ms",), "wait_for": ("target",),
    "hook": ("name",),
}
CHECKS = {"visible": ("target",), "absent": ("target",), "count": ("target",), "order": ("within", "texts"),
          "text": ("target", "contains"), "url": ("matches",)}
SCROLL_TO = ("top", "bottom")
WAIT_STATES = ("visible", "hidden", "attached")
BLOCKS = ("start", "center", "end", "nearest")


class SpecError(Exception):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# ---------------------------------------------------------------- the spec

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _pattern_ok(v):
    if isinstance(v, str):
        return bool(v)
    if isinstance(v, dict) and set(v) <= {"regex", "flags"} and isinstance(v.get("regex"), str):
        try:
            re.compile(v["regex"])
        except re.error:
            return False
        return isinstance(v.get("flags", ""), str) and set(v.get("flags", "")) <= set("ims")
    return False


def check_target(t, where, errs, allow_within=True):
    if not isinstance(t, dict):
        errs.append("%s: target must be an object" % where)
        return
    keys = [k for k in TARGET_KEYS if k in t]
    if len(keys) != 1:
        errs.append("%s: target needs exactly one of %s (got %s)" % (where, ", ".join(TARGET_KEYS), keys or "none"))
        return
    extra = set(t) - set(TARGET_KEYS) - set(TARGET_EXTRA)
    if extra:
        errs.append("%s: unknown target key(s) %s" % (where, sorted(extra)))
    k = keys[0]
    if k in ("css", "testId"):
        if not (isinstance(t[k], str) and t[k]):
            errs.append("%s: %s must be a non-empty string" % (where, k))
    elif not _pattern_ok(t[k]):
        errs.append("%s: %s must be a string or {regex, flags}" % (where, k))
    if "name" in t:
        if k != "role":
            errs.append("%s: name only goes with role" % where)
        elif not _pattern_ok(t["name"]):
            errs.append("%s: name must be a string or {regex, flags}" % where)
    if k == "role" and not isinstance(t["role"], str):
        errs.append("%s: role must be a string" % where)
    if "exact" in t and not isinstance(t["exact"], bool):
        errs.append("%s: exact must be true or false" % where)
    if "nth" in t and not (isinstance(t["nth"], int) and not isinstance(t["nth"], bool) and t["nth"] >= 0):
        errs.append("%s: nth must be a whole number from 0" % where)
    if "within" in t:
        if not allow_within or not (isinstance(t["within"], str) and t["within"]):
            errs.append("%s: within must be a css selector string" % where)


def _check_action(a, where, errs, has_setup):
    if not isinstance(a, dict):
        errs.append("%s: must be an object" % where)
        return
    kinds = [k for k in ("do", "check") if k in a]
    if len(kinds) != 1:
        errs.append("%s: needs exactly one of do, check" % where)
        return
    kind = kinds[0]
    name = a[kind]
    table = ACTIONS if kind == "do" else CHECKS
    if name not in table:
        errs.append("%s: unknown %s %r (known: %s)" % (where, kind, name, ", ".join(sorted(table))))
        return
    for need in table[name]:
        if need not in a:
            errs.append("%s: %s %r needs %r" % (where, kind, name, need))
    if "target" in a:
        check_target(a["target"], where + ".target", errs)
    if kind == "do":
        if name == "goto" and not (isinstance(a.get("path"), str) and a["path"][:1] in ("/", "h")):
            errs.append("%s: goto path must start with / or http" % where)
        if name == "scroll" and a.get("to") not in SCROLL_TO:
            errs.append("%s: scroll to must be one of %s" % (where, SCROLL_TO))
        if name == "scroll_to" and a.get("block", "start") not in BLOCKS:
            errs.append("%s: block must be one of %s" % (where, BLOCKS))
        if name in ("wait", "highlight", "scroll_to", "wait_for") and any(
                k in a and not (_num(a[k]) and a[k] >= 0) for k in ("ms", "settleMs", "timeoutMs")):
            errs.append("%s: ms, settleMs and timeoutMs must be numbers from 0" % where)
        if name == "fill" and not isinstance(a.get("value"), str):
            errs.append("%s: fill value must be a string" % where)
        if name == "press" and not (isinstance(a.get("key"), str) and a["key"]):
            errs.append("%s: press key must be a string" % where)
        if name == "wait_for" and a.get("state", "visible") not in WAIT_STATES:
            errs.append("%s: state must be one of %s" % (where, WAIT_STATES))
        if name == "hook":
            if not has_setup:
                errs.append("%s: a hook action needs setup.module in the spec" % where)
            if not (isinstance(a.get("name"), str) and re.match(r"^[A-Za-z_]\w*$", a["name"])):
                errs.append("%s: hook name must be a function name" % where)
    else:
        if name == "count" and not any(k in a for k in ("equals", "atLeast")):
            errs.append("%s: count needs equals or atLeast" % where)
        if name == "order":
            if not (isinstance(a.get("within"), str) and a["within"]):
                errs.append("%s: order within must be a css selector" % where)
            tx = a.get("texts")
            if not (isinstance(tx, list) and len(tx) >= 2 and all(isinstance(x, str) and x for x in tx)):
                errs.append("%s: order texts must be a list of 2 or more strings" % where)
        if name == "text" and not (isinstance(a.get("contains"), str) and a["contains"]):
            errs.append("%s: text contains must be a non-empty string" % where)
        if name == "url":
            try:
                re.compile(str(a.get("matches", "")))
            except re.error:
                errs.append("%s: url matches must be a regular expression" % where)


def _module_ok(m, where, errs):
    if not isinstance(m, dict) or not isinstance(m.get("module"), str) or not m["module"]:
        errs.append("%s: needs a module path (relative to the spec's folder)" % where)
        return
    p = Path(m["module"])
    if p.is_absolute() or ".." in p.parts:
        errs.append("%s: module must stay inside the spec's folder" % where)
    if "export" in m and not (isinstance(m["export"], str) and re.match(r"^[A-Za-z_]\w*$", m["export"])):
        errs.append("%s: export must be a function name" % where)


def validate_spec(raw):
    """Every problem with a parsed spec, as a list of strings (empty = good)."""
    errs = []
    if not isinstance(raw, dict):
        return ["the spec must be a JSON object"]
    name = raw.get("name")
    if not (isinstance(name, str) and NAME_RE.match(name)):
        errs.append("name must be letters, digits, dot, dash or underscore (it names the output files)")
    base = raw.get("baseUrl")
    if not (isinstance(base, str) and re.match(r"^https?://[^/\s]+/?$", base)):
        errs.append("baseUrl must look like http://localhost:3000")
    vp = raw.get("viewport", {"width": 1280, "height": 800})
    if not (isinstance(vp, dict) and all(isinstance(vp.get(k), int) and 320 <= vp[k] <= 3840 for k in ("width", "height"))):
        errs.append("viewport must be {width, height} in whole pixels")
    if "gap" in raw and not (_num(raw["gap"]) and 0 <= raw["gap"] <= 10):
        errs.append("gap must be seconds from 0 to 10")
    if "voice" in raw and not (isinstance(raw["voice"], str) and VOICE_RE.match(raw["voice"])):
        errs.append("voice must look like en_US-lessac-medium")
    if "title" in raw and not isinstance(raw["title"], str):
        errs.append("title must be a string")
    run = raw.get("runner", {})
    if not isinstance(run, dict):
        errs.append("runner must be an object")
    else:
        extra = set(run) - {"image", "network", "user", "mounts", "env", "nodePath", "workdir", "timeoutSec", "heavy"}
        if extra:
            errs.append("runner: unknown key(s) %s" % sorted(extra))
        if "user" in run and run["user"] not in ("host", "root"):
            errs.append("runner.user must be host or root")
        for m in run.get("mounts", []) if isinstance(run.get("mounts", []), list) else []:
            if not (isinstance(m, dict) and isinstance(m.get("host"), str) and isinstance(m.get("container"), str)
                    and m["container"].startswith("/")):
                errs.append("runner.mounts entries need host and an absolute container path")
        if "mounts" in run and not isinstance(run["mounts"], list):
            errs.append("runner.mounts must be a list")
        if "env" in run and not (isinstance(run["env"], dict) and all(isinstance(v, str) for v in run["env"].values())):
            errs.append("runner.env must map names to strings")
        if "nodePath" in run and not (isinstance(run["nodePath"], list) and all(isinstance(v, str) for v in run["nodePath"])):
            errs.append("runner.nodePath must be a list of folders inside the container")
        if "timeoutSec" in run and not (_num(run["timeoutSec"]) and 30 <= run["timeoutSec"] <= 3600):
            errs.append("runner.timeoutSec must be 30 to 3600")
        if "network" in run and not (isinstance(run["network"], str) and re.match(r"^(host|bridge|container:[\w.-]+|[\w.-]+)$", run["network"])):
            errs.append("runner.network must be host, bridge, container:NAME or a network name")
    has_setup = "setup" in raw
    for key in ("setup", "cleanup"):
        if key in raw:
            _module_ok(raw[key], key, errs)
    if "cleanup" in raw and "setup" in raw and raw["cleanup"].get("module") != raw["setup"].get("module"):
        errs.append("cleanup.module must be the same file as setup.module (one hook module per spec)")
    start = raw.get("start", {})
    if not isinstance(start, dict) or set(start) - {"path", "waitFor", "settleMs"}:
        errs.append("start may hold path, waitFor and settleMs only")
    else:
        if "path" in start and not (isinstance(start["path"], str) and start["path"][:1] == "/"):
            errs.append("start.path must start with /")
        if "waitFor" in start:
            check_target(start["waitFor"], "start.waitFor", errs)
        if "settleMs" in start and not (_num(start["settleMs"]) and start["settleMs"] >= 0):
            errs.append("start.settleMs must be a number from 0")
    steps = raw.get("steps")
    if not (isinstance(steps, list) and steps):
        errs.append("steps must be a list with at least one step")
        return errs
    for i, s in enumerate(steps, 1):
        w = "steps[%d]" % i
        if not isinstance(s, dict):
            errs.append("%s: must be an object" % w)
            continue
        extra = set(s) - {"say", "caption", "actions"}
        if extra:
            errs.append("%s: unknown key(s) %s" % (w, sorted(extra)))
        if not (isinstance(s.get("say"), str) and s["say"].strip()):
            errs.append("%s: say (the narration) must be a non-empty string" % w)
        elif len(s["say"]) > 600:
            errs.append("%s: say is %d characters; split it into two steps (limit 600)" % (w, len(s["say"])))
        if "caption" in s and not (isinstance(s["caption"], str) and s["caption"].strip()):
            errs.append("%s: caption must be a non-empty string" % w)
        acts = s.get("actions", [])
        if not isinstance(acts, list):
            errs.append("%s: actions must be a list" % w)
            continue
        for j, a in enumerate(acts, 1):
            _check_action(a, "%s.actions[%d]" % (w, j), errs, has_setup)
        if isinstance(s.get("say"), str) and s["say"].strip().lower().startswith("narration:"):
            errs.append("%s: the narration label is added for you; do not write it" % w)
    return errs


def load_spec(path):
    p = Path(path)
    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise SpecError(["cannot read %s: %s" % (p, e)])
    errs = validate_spec(raw)
    if errs:
        raise SpecError(errs)
    return raw


def normalize(raw, spec_dir):
    """The spec as the recorder reads it: label step first, defaults filled, hook modules at their container path."""
    steps = [{"say": LABEL, "caption": LABEL, "actions": []}]
    for s in raw["steps"]:
        say = " ".join(s["say"].split())
        steps.append({"say": say, "caption": " ".join(s.get("caption", say).split()), "actions": s.get("actions", [])})
    out = {
        "name": raw["name"], "baseUrl": raw["baseUrl"].rstrip("/"), "viewport": raw.get("viewport", {"width": 1280, "height": 800}),
        "gap": raw.get("gap", GAP_DEFAULT), "voice": raw.get("voice", "en_US-lessac-medium"), "start": raw.get("start", {}),
        "steps": steps,
    }
    for key in ("setup", "cleanup"):
        if key in raw:
            out[key] = {"module": "/spec/" + Path(raw[key]["module"]).as_posix(), "export": raw[key].get("export", key)}
    return out


# ---------------------------------------------------------------- audio

def run_cmd(cmd, timeout, **kw):
    """subprocess.run with a hard timeout; tests replace this."""
    return subprocess.run(cmd, timeout=timeout, **kw)


def line_hash(text, voice):
    return hashlib.sha256(("%s\n%s" % (voice, text)).encode()).hexdigest()[:16]


def probe_duration(path, runner=None):
    runner = runner or run_cmd
    r = runner(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], 30,
               capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def make_audio(norm, out, runner=None, force=False, probe=None):
    """One wav per line plus NAME-audio.json {lines:[{text,sha,wav,dur}]}. A line whose text and voice are unchanged keeps its wav."""
    runner = runner or run_cmd
    probe = probe or (lambda f: probe_duration(f, runner))
    out = Path(out)
    name, voice = norm["name"], norm["voice"]
    meta_path = out / ("%s-audio.json" % name)
    old = {}
    if meta_path.exists() and not force:
        try:
            old = {m["sha"]: m for m in json.loads(meta_path.read_text())["lines"] if (out / m["wav"]).exists()}
        except (ValueError, KeyError):
            old = {}
    lines, made = [], 0
    for i, s in enumerate(norm["steps"]):
        sha = line_hash(s["say"], voice)
        wav = "%s-%d.wav" % (name, i)
        if sha in old and old[sha]["wav"] == wav:
            lines.append(old[sha])
            continue
        runner(["timeout", "120", "docker", "run", "--rm", "-i", "--cpus", "1", "--memory", "1g", "--user", "%d:%d" % (os.getuid(), os.getgid()),
                "-v", "%s:/out" % out, PIPER_IMAGE, "python", "-m", "piper", "--model", "/voices/%s.onnx" % voice,
                "--output_file", "/out/" + wav], 150, input=s["say"].encode(), check=True, capture_output=True)
        lines.append({"text": s["say"], "sha": sha, "wav": wav, "dur": probe(out / wav)})
        made += 1
    meta_path.write_text(json.dumps({"voice": voice, "lines": lines}, indent=1))
    return lines, made


# ---------------------------------------------------------------- record

def expand(p):
    return str(Path(os.path.expanduser(p)).resolve())


def docker_record_cmd(raw, norm, out, spec_dir, uid, gid, heavy=False, docker_heavy=None):
    """The docker command for the recording stage. Everything the container sees: /out (rw), /spec (ro), /dv (the recorder, ro),
    /dvmods (the Playwright install from `doctor --install`, ro), then the spec's own runner.mounts."""
    run = raw.get("runner", {})
    heavy = heavy or run.get("heavy", False)
    root = run.get("user") == "root"
    mods = ["/dvmods/node_modules"] + list(run.get("nodePath", []))
    cmd = ["docker", "run", "--rm"]
    if not heavy:
        cmd += ["--cpus", str(run.get("cpus", 4)), "--memory", run.get("memory", "8g")]
    if not root:
        cmd += ["--user", "%d:%d" % (uid, gid), "-e", "HOME=/tmp"]
    else:
        cmd += ["-e", "DV_CHOWN=%d:%d" % (uid, gid)]
    if run.get("network"):
        cmd += ["--network", run["network"]]
    cmd += ["-v", "%s:/out" % out, "-v", "%s:/spec:ro" % spec_dir, "-v", "%s:/dv:ro" % HERE,
            "-v", "%s:/dvmods:ro" % CACHE]
    for m in run.get("mounts", []):
        cmd += ["-v", "%s:%s%s" % (expand(m["host"]), m["container"], ":ro" if m.get("ro", True) else "")]
    env = dict(run.get("env", {}), NODE_PATH=":".join(mods))
    for k in sorted(env):
        cmd += ["-e", "%s=%s" % (k, env[k])]
    if run.get("workdir"):
        cmd += ["-w", run["workdir"]]
    cmd += [run.get("image", PLAYWRIGHT_IMAGE), "node", "/dv/demo_video_record.cjs", "/out/%s.run.json" % norm["name"]]
    if heavy:
        cmd = [docker_heavy or str(Path.home() / ".claude/scripts/docker-heavy.sh"), "--timeout", str(int(run.get("timeoutSec", 900))), "--"] + cmd
    return cmd


def record(raw, norm, lines, out, spec_dir, heavy=False, runner=None):
    runner = runner or run_cmd
    out = Path(out)
    run = dict(norm, durations=[l["dur"] for l in lines])
    (out / ("%s.run.json" % norm["name"])).write_text(json.dumps(run, indent=1))
    for stale in ("%s.webm" % norm["name"], "%s-timeline.json" % norm["name"]):
        (out / stale).unlink(missing_ok=True)
    timeout = int(raw.get("runner", {}).get("timeoutSec", 900))
    cmd = docker_record_cmd(raw, norm, out, spec_dir, os.getuid(), os.getgid(), heavy)
    if cmd[0] != "docker":
        runner(cmd, timeout + 120, check=True)
    else:
        runner(["timeout", str(timeout)] + cmd, timeout + 30, check=True)
    tl = json.loads((out / ("%s-timeline.json" % norm["name"])).read_text())
    if not (out / ("%s.webm" % norm["name"])).exists():
        raise RuntimeError("the recorder finished without a .webm")
    return tl


# ---------------------------------------------------------------- assemble

def ass_time(s):
    cs = int(round(s * 100))
    return "%d:%02d:%02d.%02d" % (cs // 360000, cs % 360000 // 6000, cs % 6000 // 100, cs % 100)


def srt_time(s):
    ms = int(round(s * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms % 3600000 // 60000, ms % 60000 // 1000, ms % 1000)


def caption_windows(timeline):
    return [(s["start"], s["start"] + s["dur"] + CAPTION_TAIL) for s in timeline["steps"]]


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: %d
PlayResY: %d
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,26,&H00FFFFFF,&H00FFFFFF,&HB0000000,&HB0000000,0,0,0,0,100,100,0,0,3,10,0,2,80,80,24,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_text(t):
    return t.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def build_ass(timeline, captions, size=(1280, 800)):
    ev = "".join("Dialogue: 0,%s,%s,Cap,,0,0,0,,%s\n" % (ass_time(a), ass_time(b), ass_text(c))
                 for (a, b), c in zip(caption_windows(timeline), captions))
    return ASS_HEAD % size + ev


def build_srt(timeline, captions):
    return "".join("%d\n%s --> %s\n%s\n\n" % (i, srt_time(a), srt_time(b), c)
                   for i, ((a, b), c) in enumerate(zip(caption_windows(timeline), captions), 1))


def find_markers(raw_rgb, fps=25):
    """First red frame, then first blue frame after it, in seconds, from a 1x1-pixel rgb24 dump of the recording."""
    px = [raw_rgb[i:i + 3] for i in range(0, len(raw_rgb) - 2, 3)]
    red = next((i for i, p in enumerate(px) if p[0] > 200 and p[1] < 60 and p[2] < 60), None)
    if red is None:
        raise RuntimeError("no red start marker frame in the recording")
    blue = next((i for i, p in enumerate(px) if i > red and p[2] > 200 and p[0] < 60 and p[1] < 60), None)
    if blue is None:
        raise RuntimeError("no blue end marker frame in the recording")
    return red / fps, blue / fps


def sync_numbers(t_red, t_blue, tl):
    """Where in the .webm the demo starts, how long it runs there, and the webm clock's speed against the wall clock."""
    b = (t_blue - t_red) / (tl["mBlue"] - tl["mRed"])
    return t_red + (tl["demoOffset"] - tl["mRed"]) * b, tl["total"] * b, b


def mix_filter(timeline, first_input=1):
    steps = timeline["steps"]
    fc = "".join("[%d:a]adelay=%d:all=1[a%d];" % (i + first_input, int(s["start"] * 1000), i) for i, s in enumerate(steps))
    return fc + "".join("[a%d]" % i for i in range(len(steps))) + "amix=inputs=%d:normalize=0:dropout_transition=0[aout]" % len(steps)


def contact_times(timeline):
    return [s["start"] + s["dur"] / 2 for s in timeline["steps"]]


def assemble(norm, lines, tl, out, runner=None, markers=None):
    runner = runner or run_cmd
    out = Path(out)
    name = norm["name"]
    captions = [s["caption"] for s in norm["steps"]]
    (out / ("%s.ass" % name)).write_text(build_ass(tl, captions, (norm["viewport"]["width"], norm["viewport"]["height"])))
    (out / ("%s.srt" % name)).write_text(build_srt(tl, captions))
    if markers is None:
        rgb = runner(["ffmpeg", "-v", "error", "-i", str(out / ("%s.webm" % name)), "-vf", "fps=25,scale=1:1", "-f", "rawvideo",
                      "-pix_fmt", "rgb24", "-"], 300, capture_output=True, check=True).stdout
        markers = find_markers(rgb)
    off, tot, b = sync_numbers(markers[0], markers[1], tl)
    cap = "%s-captions-only.mp4" % name
    demo = "%s-demo.mp4" % name
    base = ["ffmpeg", "-y", "-v", "error"]
    runner(base + ["-ss", "%.3f" % off, "-t", "%.3f" % tot, "-i", "%s.webm" % name, "-vf",
                   "setpts=(PTS-STARTPTS)/%.6f,subtitles=%s.ass,fps=25" % (b, name), "-c:v", "libx264", "-preset", "medium",
                   "-crf", "20", "-pix_fmt", "yuv420p", "-an", cap], 600, check=True, cwd=out)
    inputs = [x for l in lines for x in ("-i", l["wav"])]
    runner(base + ["-i", cap] + inputs + ["-filter_complex", mix_filter(tl), "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
                   "-c:a", "aac", "-b:a", "128k", demo], 600, check=True, cwd=out)
    sheet = contact_sheet(name, tl, out, runner)
    return {"sync": {"trim": off, "len": tot, "speed": b}, "contactSheet": sheet}


def contact_sheet(name, tl, out, runner=None):
    runner = runner or run_cmd
    out = Path(out)
    times = contact_times(tl)
    cols = 3 if len(times) > 4 else 2
    rows = -(-len(times) // cols)
    tmp = tempfile.mkdtemp(prefix="demo-video-sheet-")
    try:
        for i, t in enumerate(times):
            runner(["ffmpeg", "-y", "-v", "error", "-ss", "%.2f" % t, "-i", str(out / ("%s-captions-only.mp4" % name)), "-frames:v", "1",
                    "-vf", "scale=640:-2", os.path.join(tmp, "f%02d.png" % i)], 60, check=True)
        sheet = "%s-contact-sheet.png" % name
        runner(["ffmpeg", "-y", "-v", "error", "-framerate", "1", "-i", os.path.join(tmp, "f%02d.png"), "-vf",
                "tile=%dx%d:padding=6:color=white" % (cols, rows), "-frames:v", "1", str(out / sheet)], 60, check=True)
        return sheet
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def verify(norm, tl, out, probe=None):
    """Facts the lane checks before showing the video: lengths, overruns, the first caption, failed checks."""
    probe = probe or probe_duration
    out = Path(out)
    name = norm["name"]
    warns = []
    demo, cap = probe(out / ("%s-demo.mp4" % name)), probe(out / ("%s-captions-only.mp4" % name))
    if abs(demo - tl["total"]) > DURATION_WARN:
        warns.append("narrated cut is %.1f s but the recorded timeline is %.1f s" % (demo, tl["total"]))
    if abs(demo - cap) > 0.5:
        warns.append("narrated cut %.1f s and captions-only cut %.1f s differ by more than 0.5 s" % (demo, cap))
    for i, s in enumerate(tl["steps"]):
        if s.get("overran", 0) > OVERRAN_WARN:
            warns.append("step %d: its actions ran %.1f s past its narration, so the picture lags the voice" % (i, s["overran"]))
    if norm["steps"][0]["caption"] != LABEL:
        warns.append("the first caption is not %r" % LABEL)
    stalled = sum(x["sec"] for x in tl.get("stalls", []))
    if stalled > OVERRAN_WARN:
        warns.append("the recording container froze %d time(s) for %.1f s in all (a Docker/WSL hiccup, not the spec): the picture holds "
                     "still then and the cut is that much longer; run again for a clean one if it matters" % (len(tl["stalls"]), stalled))
    bad = [c for c in tl.get("checks", []) if not c.get("ok")]
    for c in bad:
        warns.append("check failed: step %s: %s" % (c.get("step"), c.get("msg")))
    return {"narrated": round(demo, 2), "captionsOnly": round(cap, 2), "timeline": round(tl["total"], 2),
            "stalledSec": round(stalled, 2), "netOfStalls": round(demo - stalled, 2), "warnings": warns}


# ---------------------------------------------------------------- doctor, open

def doctor(install=False, runner=None):
    """What is installed and what is missing; with install=True, builds the Piper image and caches Playwright. Returns (lines, ok)."""
    runner = runner or run_cmd
    lines, ok = [], True

    def probe(label, cmd, want=None, timeout=20):
        nonlocal ok
        try:
            r = runner(cmd, timeout, capture_output=True, text=True)
            good = r.returncode == 0 and (want is None or want in (r.stdout + r.stderr))
        except (OSError, subprocess.TimeoutExpired):
            good = False
        lines.append("%-34s %s" % (label, "ok" if good else "MISSING"))
        ok = ok and good
        return good

    have_docker = probe("docker answers", ["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
    probe("ffmpeg has the subtitles filter", ["ffmpeg", "-hide_banner", "-filters"], want="subtitles")
    probe("ffprobe", ["ffprobe", "-version"])
    font = any(Path(p).exists() for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"))
    lines.append("%-34s %s" % ("DejaVu Sans font", "ok" if font else "MISSING (captions fall back to another font)"))
    if have_docker:
        for label, image, build in (("Piper image " + PIPER_IMAGE, PIPER_IMAGE, True), ("Playwright image", PLAYWRIGHT_IMAGE, False)):
            got = probe(label, ["docker", "image", "inspect", image])
            if not got and install:
                if build:
                    d = HERE.parent / "skills" / "demo-video" / "piper"
                    d = d if d.exists() else Path.home() / ".claude/skills/demo-video/piper"
                    runner(["timeout", "1200", "docker", "build", "-t", image, str(d)], 1260, check=True)
                else:
                    runner(["timeout", "1800", "docker", "pull", image], 1860, check=True)
                lines.append("  installed %s" % image)
                ok = True if all("MISSING" not in l for l in lines[:-1]) else ok
    mods = CACHE / "node_modules" / "playwright"
    if not mods.exists() and install and have_docker:
        CACHE.mkdir(parents=True, exist_ok=True)
        runner(["timeout", "600", "docker", "run", "--rm", "--user", "%d:%d" % (os.getuid(), os.getgid()), "-e", "HOME=/tmp",
                "-v", "%s:/dvmods" % CACHE, "-w", "/dvmods", PLAYWRIGHT_IMAGE, "npm", "install", "--no-audit", "--no-fund",
                "--silent", "playwright@%s" % PLAYWRIGHT_VERSION], 660, check=True)
        lines.append("  installed playwright@%s in %s" % (PLAYWRIGHT_VERSION, CACHE))
    present = (CACHE / "node_modules" / "playwright").exists()
    lines.append("%-34s %s" % ("playwright npm package (%s)" % CACHE, "ok" if present else "MISSING (run: demo_video.py doctor --install)"))
    ok = ok and present
    lines.append("%-34s %s" % ("powershell.exe (open on screen)", "ok" if shutil.which("powershell.exe") else "not found (open falls back to xdg-open)"))
    return lines, ok


def open_cmd(path, wslpath=None, which=shutil.which):
    path = str(Path(path).resolve())
    if which("powershell.exe"):
        win = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True, timeout=10, check=True).stdout.strip() if wslpath is None else wslpath
        return ["powershell.exe", "-NoProfile", "-Command", "Start-Process -FilePath '%s'" % win.replace("'", "''")]
    return ["xdg-open", path]


# ---------------------------------------------------------------- the one command

def run_all(spec_path, out, stages=("audio", "record", "assemble"), force_audio=False, heavy=False, say=print, runner=None):
    spec_path = Path(spec_path).resolve()
    raw = load_spec(spec_path)
    norm = normalize(raw, spec_path.parent)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    name = norm["name"]
    for key in ("setup", "cleanup"):
        if key in raw and not (spec_path.parent / raw[key]["module"]).is_file():
            raise SpecError(["%s.module %s does not exist next to the spec" % (key, raw[key]["module"])])
    if raw["baseUrl"].split("//")[1].split(":")[0] in ("localhost", "127.0.0.1") and not raw.get("runner", {}).get("network"):
        say("warning: baseUrl is local but runner.network is not set; the container cannot see the host's localhost (use host or container:NAME)")
    lines = None
    if "audio" in stages:
        lines, made = make_audio(norm, out, runner, force_audio)
        say("audio: %d lines (%d voiced now), %.1f s of speech" % (len(lines), made, sum(l["dur"] for l in lines)))
    if lines is None:
        lines = json.loads((out / ("%s-audio.json" % name)).read_text())["lines"]
    tl = None
    if "record" in stages:
        tl = record(raw, norm, lines, out, spec_path.parent, heavy, runner)
        passed = sum(1 for c in tl.get("checks", []) if c.get("ok"))
        say("record: %.1f s demo, %d/%d on-screen checks passed" % (tl["total"], passed, len(tl.get("checks", []))))
    if "assemble" in stages:
        if tl is None:
            tl = json.loads((out / ("%s-timeline.json" % name)).read_text())
        info = assemble(norm, lines, tl, out, runner)
        res = verify(norm, tl, out)
        res.update(name=name, files={k: str(out / v) for k, v in (
            ("narrated", name + "-demo.mp4"), ("captionsOnly", name + "-captions-only.mp4"), ("srt", name + ".srt"),
            ("contactSheet", info["contactSheet"]))}, checks=tl.get("checks", []), sync=info["sync"])
        (out / ("%s-result.json" % name)).write_text(json.dumps(res, indent=1))
        say("assemble: narrated %.1f s, captions-only %.1f s" % (res["narrated"], res["captionsOnly"]))
        for k, v in res["files"].items():
            say("  %-12s %s" % (k, v))
        for w in res["warnings"]:
            say("WARNING: " + w)
        return res
    return {"name": name}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("spec")
    r.add_argument("--out", default="out")
    r.add_argument("--stages", default="audio,record,assemble")
    r.add_argument("--force-audio", action="store_true")
    r.add_argument("--heavy", action="store_true", help="run the recorder through docker-heavy.sh")
    v = sub.add_parser("validate")
    v.add_argument("spec")
    d = sub.add_parser("doctor")
    d.add_argument("--install", action="store_true")
    o = sub.add_parser("open")
    o.add_argument("file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "validate":
            n = normalize(load_spec(a.spec), Path(a.spec).resolve().parent)
            print("spec ok: %s, %d lines (label included)" % (n["name"], len(n["steps"])))
        elif a.cmd == "doctor":
            lines, ok = doctor(a.install)
            print("\n".join(lines))
            return 0 if ok else 1
        elif a.cmd == "open":
            cmd = open_cmd(a.file)
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            print("opened:", a.file)
        else:
            stages = tuple(s for s in a.stages.split(",") if s)
            if not stages or set(stages) - {"audio", "record", "assemble"}:
                print("stages must be a comma list of audio, record, assemble", file=sys.stderr)
                return 2
            run_all(a.spec, a.out, stages, a.force_audio, a.heavy)
    except SpecError as e:
        print("spec problems:\n  " + "\n  ".join(e.errors), file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or b"")
        tail = tail.decode(errors="replace") if isinstance(tail, bytes) else str(tail)
        print("FAILED: %s\n%s" % (" ".join(map(str, e.cmd))[:200], tail[-1500:]), file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as e:
        print("FAILED: timed out after %s s: %s" % (e.timeout, " ".join(map(str, e.cmd))[:200]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
