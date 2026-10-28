#!/usr/bin/env python3
"""tab_record.py: film one browser tab, read only, and make a raw video plus a blurred copy.

  tab_record.py start <job> --tab <url part> [--port N] [--flag-dir DIR]... [--flag-prefix P]... [--cap MIN] [--auto]
  tab_record.py stop <name>             end a recording now (the videos are made when the capture ends)
  tab_record.py status                  recordings that are running now
  tab_record.py list [--limit N]        finished recordings, newest last
  tab_record.py finish <dir>            make the videos from a finished capture (the capture does this itself)
  tab_record.py cut <name> --from HH:MM:SS --to HH:MM:SS [--out FILE]   a blurred clip for one finding
  tab_record.py check <name> [--frames N]   full-size frames from the blurred video, to look at before it leaves the machine
  tab_record.py prune [--days N]        delete the capture frames of recordings older than N days (default 7)

How it records: tab_record_capture.cjs attaches to the browser's debugging port and follows ONE tab, taking a screenshot of the
whole page about every second and keeping a frame only when the picture changed. With each frame it saves where personal
data sits (email, phone, LinkedIn address, salary, client names, sensitive answers, every free-text box). It never clicks,
scrolls, types, navigates or closes anything. It ends 25 s after the job's submitted flag appears or the tab reaches a
confirmation address, when the tab closes, on `stop`, or at the cap.

Everything lives under ~/.claude/state/recordings/<date>/<name>/ (private, mode 0700):
  <name>-raw.mp4        the screen as it was. Never leaves this machine.
  <name>-blurred.mp4    personal data blurred. The only copy that may leave, and only after `check` frames are looked at.
  meta.json, timeline.json, index.tsv (one line per recording, in the recordings folder)
Optional private extras for the blur: ~/.config/afa/recording-blur.json {"patterns": ["regex", ...]}
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
W, H = 1280, 800  # the video canvas
MAX_GAP_S = 3.0  # a quiet spell longer than this is shown as this long (the clock on screen keeps the real time)
TAIL_S = 5.0  # the last frame (the confirmation page) is held this long
PAD = 6  # pixels added round every blurred rectangle
CONFIRM_RE = re.compile(r"confirmation|thank-?you|thanks|submitted|success|applied", re.I)


def home():
    return Path(os.environ.get("TAB_RECORD_HOME") or (Path.home() / ".claude/state/recordings"))


# ---------- pure helpers (these have tests) ----------

def safe_name(s):
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s or "").strip("-.")
    return s[:60] or "tab"


def flag_prefixes(job, extra=()):
    """Flag files for a job start with its key: aeye-submitted-submitted.flag, ln-submitted.flag, graf-submitted.flag."""
    out = []
    for p in [job, *extra]:
        p = (p or "").strip()
        if p and p not in out:
            out.append(p)
    return out


def job_key_from_steps(path):
    """aeye-p1-steps.json -> aeye; ../d6/rcat/rcat-p2-steps.json -> rcat."""
    b = os.path.basename(path or "")
    b = re.sub(r"-p\d+-steps\.json.*$", "", b)
    b = re.sub(r"-steps\.json.*$", "", b)
    return b


def job_key_from_flag(flag_name):
    """bitw-submitted -> bitw; subs-submitted.flag -> subs (the submit scripts are given '<key>-submitted', sometimes with .flag)."""
    k = re.sub(r"\.flag$", "", flag_name or "")
    return re.sub(r"-submitted$", "", k)


def merge_rects(rects, scale, width, height, pad=PAD):
    """Rectangles from the page ({x,y,w,h} in page pixels) -> padded integer boxes (l,t,r,b) in video pixels, clamped to the frame.
    Boxes that overlap are joined so a long line of text is one box."""
    boxes = []
    for r in rects:
        l = int((r["x"] - pad) * scale)
        t = int((r["y"] - pad) * scale)
        rr = int((r["x"] + r["w"] + pad) * scale + 0.999)
        b = int((r["y"] + r["h"] + pad) * scale + 0.999)
        l, t, rr, b = max(0, l), max(0, t), min(width, rr), min(height, b)
        if rr > l and b > t:
            boxes.append([l, t, rr, b])
    changed = True
    while changed:
        changed = False
        out = []
        for bx in boxes:
            for o in out:
                if bx[0] <= o[2] and bx[2] >= o[0] and bx[1] <= o[3] and bx[3] >= o[1]:
                    o[0], o[1], o[2], o[3] = min(o[0], bx[0]), min(o[1], bx[1]), max(o[2], bx[2]), max(o[3], bx[3])
                    changed = True
                    break
            else:
                out.append(list(bx))
        boxes = out
    return [tuple(b) for b in boxes]


def window_top(prev_top, bbox, frame_h, win_h=H):
    """Where the video window sits on a tall page. It stays put while the change is inside it, and re-centres on the change when not."""
    if frame_h <= win_h:
        return 0
    if bbox is None:
        return min(prev_top, frame_h - win_h)
    top_, bot = bbox[1], bbox[3]
    if prev_top <= top_ and bot <= prev_top + win_h:
        return min(prev_top, frame_h - win_h)
    cy = (top_ + bot) // 2
    return max(0, min(frame_h - win_h, cy - win_h // 2))


def durations(times_ms, max_gap=MAX_GAP_S, tail=TAIL_S):
    """Seconds each frame is shown: the real gap to the next frame, capped; the last frame is held for the tail."""
    out = []
    for i, t in enumerate(times_ms):
        out.append(tail if i == len(times_ms) - 1 else max(0.1, min((times_ms[i + 1] - t) / 1000.0, max_gap)))
    return out


def timeline(times_ms, durs):
    """[(wall ms, video second the frame starts at)]."""
    acc, rows = 0.0, []
    for t, d in zip(times_ms, durs):
        rows.append((t, round(acc, 3)))
        acc += d
    return rows


def video_offset(rows, wall_ms):
    """Video second for a wall-clock moment: the start of the last frame at or before it (0 before the first)."""
    off = 0.0
    for t, s in rows:
        if t <= wall_ms:
            off = s
        else:
            break
    return off


def wall_ms(date_iso, hms):
    """'14:03:07' on the recording's date -> epoch ms (local time)."""
    h, m, s = [int(x) for x in (hms.split(":") + ["0", "0"])[:3]]
    d = dt.date.fromisoformat(date_iso)
    return int(dt.datetime(d.year, d.month, d.day, h, m, s).timestamp() * 1000)


def concat_text(files, durs):
    """ffmpeg concat-demuxer list; the last file is repeated because the demuxer drops the final duration."""
    lines = []
    for f, d in zip(files, durs):
        lines.append("file '%s'" % str(f).replace("'", "'\\''"))
        lines.append("duration %.3f" % d)
    if files:
        lines.append("file '%s'" % str(files[-1]).replace("'", "'\\''"))
    return "\n".join(lines) + "\n"


def stamp_label(ms, name):
    t = dt.datetime.fromtimestamp(ms / 1000)
    return "%s  %s" % (t.strftime("%a %-d %b %-I:%M:%S %p"), name)


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError, TypeError):
        return False


# ---------- browser port ----------

def _port_ok(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%s/json/version" % port, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def discover_port(explicit=None):
    """--port, then TAB_RECORD_PORT, then the fleet browser's own command line (as sc-lib.js does), then 9334, then 9333."""
    cands = [explicit, os.environ.get("TAB_RECORD_PORT")]
    try:
        lock = os.readlink(Path.home() / ".config/afa/browser/SingletonLock")
        pid = lock.split("-")[-1]
        for a in Path("/proc/%s/cmdline" % pid).read_bytes().decode().split("\0"):
            if a.startswith("--remote-debugging-port="):
                cands.append(a.split("=", 1)[1])
    except Exception:
        pass
    cands += ["9334", "9333"]
    for c in cands:
        if c and _port_ok(c):
            return int(c)
    return None


def playwright_path():
    for p in (Path.home() / ".cache/demo-video/node_modules/playwright", Path.home() / "repos/consemble/node_modules/playwright"):
        if p.exists():
            return str(p)
    return "playwright"


# ---------- start / stop / status ----------

def rec_dirs():
    base = home()
    if not base.exists():
        return []
    return sorted(p for day in base.iterdir() if day.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}$", day.name) for p in day.iterdir() if p.is_dir())


def running(d):
    pf = d / "pid"
    return pf.exists() and pid_alive(pf.read_text().strip() or "0") and not (d / "ended.json").exists()


def meta_tab(d):
    try:
        return json.loads((d / "meta.json").read_text()).get("tab")
    except Exception:
        return None


def cmd_start(a):
    today = dt.date.today().isoformat()
    day = home() / today
    base = safe_name(a.job)
    for d in rec_dirs():
        if d.parent.name == today and d.name.startswith(base) and running(d) and re.fullmatch(re.escape(base) + r"(-\d{6})?", d.name):
            print("already recording: %s" % d)
            return 0
        if d.parent.name == today and running(d) and meta_tab(d) == a.tab:
            print("already recording this tab: %s" % d)  # a second script touching the same job's tab must not start a second film
            return 0
    port = discover_port(a.port)
    if not port:
        print("tab_record: no browser debugging port answers (tried --port, TAB_RECORD_PORT, the fleet browser, 9334, 9333)", file=sys.stderr)
        return 2
    name = base if not (day / base).exists() else "%s-%s" % (base, dt.datetime.now().strftime("%H%M%S"))
    d = day / name
    d.mkdir(parents=True, mode=0o700)
    os.chmod(home(), 0o700)
    os.chmod(day, 0o700)
    prefixes = flag_prefixes(base, a.flag_prefix or [])
    cfg = {
        "port": port, "tab": a.tab, "dir": str(d), "playwright": playwright_path(), "startedAtMs": int(time.time() * 1000),
        "capMinutes": a.cap, "flagDirs": a.flag_dir or [], "flagPrefixes": prefixes, "intervalMs": 1000,
        "extraPatterns": extra_patterns(),
    }
    (d / "config.json").write_text(json.dumps(cfg, indent=1))
    meta = {"name": name, "job": a.job, "tab": a.tab, "date": today, "startedMs": cfg["startedAtMs"], "auto": bool(a.auto)}
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    log = open(d / "supervisor.log", "ab")
    sh = 'node "$1" "$2"; python3 "$3" finish "$4"'
    p = subprocess.Popen(["sh", "-c", sh, "sh", str(HERE / "tab_record_capture.cjs"), str(d / "config.json"), str(Path(__file__).resolve()), str(d)],
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    (d / "pid").write_text(str(p.pid))
    print("recording %s (tab %s, port %s) -> %s" % (name, a.tab, port, d))
    return 0


def extra_patterns():
    try:
        return list(json.loads((Path.home() / ".config/afa/recording-blur.json").read_text()).get("patterns", []))
    except Exception:
        return []


def find_rec(name):
    hits = [d for d in rec_dirs() if d.name == name]
    if not hits:
        sys.exit("tab_record: no recording called %s" % name)
    return hits[-1]


def cmd_stop(a):
    d = find_rec(a.name)
    (d / "stop").write_text("stop")
    print("stop requested for %s; the videos are made when the capture ends (about a minute)" % d.name)
    return 0


def cmd_status(a):
    n = 0
    for d in rec_dirs():
        if running(d):
            n += 1
            frames = len(list((d / "frames").glob("f*.png"))) if (d / "frames").exists() else 0
            print("%s  frames %d  %s" % (d.name, frames, d))
    if not n:
        print("nothing recording")
    return 0


def cmd_list(a):
    idx = home() / "index.tsv"
    lines = idx.read_text().splitlines() if idx.exists() else []
    for l in lines[-a.limit:]:
        print(l)
    return 0


# ---------- the videos ----------

def _font(size):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def blur_boxes(im, boxes):
    from PIL import ImageFilter
    for (l, t, r, b) in boxes:
        reg = im.crop((l, t, r, b))
        small = reg.resize((max(1, (r - l) // 12), max(1, (b - t) // 12)))
        reg = small.resize((r - l, b - t), 0).filter(ImageFilter.GaussianBlur(3))
        im.paste(reg, (l, t))
    return im


def render_frames(d, blurred, workdir):
    """Writes one 1280x800 png per kept frame and returns (files, times). Reads the frames and rects the capture saved."""
    from PIL import Image, ImageChops, ImageDraw
    rects = {}
    rf = d / "rects.ndjson"
    if rf.exists():
        for line in rf.read_text().splitlines():
            try:
                j = json.loads(line)
                rects[j["ms"]] = j.get("rects", [])
            except Exception:
                pass
    frames = sorted((d / "frames").glob("f*.png"), key=lambda p: int(p.stem[1:]))
    name = json.loads((d / "meta.json").read_text()).get("name", d.name)
    files, times, prev, top = [], [], None, 0
    font = _font(15)
    for i, fp in enumerate(frames):
        ms = int(fp.stem[1:])
        im = Image.open(fp).convert("RGB")
        scale = W / im.width
        if im.width != W:
            im = im.resize((W, max(1, round(im.height * scale))))
        # the area that changed, from the unblurred pictures (so the window follows the typing)
        bbox = None
        if prev is not None and prev.size == im.size:
            bbox = ImageChops.difference(prev, im).getbbox()
        prev = im.copy()
        if blurred:
            # the page reported its personal data in page pixels; a rectangle written for this frame is scaled the same way
            im = blur_boxes(im, merge_rects(rects.get(ms, []), scale, im.width, im.height))
        last = i == len(frames) - 1
        top = 0 if (last or i == 0) else window_top(top, bbox, im.height)
        canvas = Image.new("RGB", (W, H), (24, 24, 28))
        canvas.paste(im.crop((0, top, W, min(im.height, top + H))), (0, 0))
        dr = ImageDraw.Draw(canvas)
        dr.rectangle((0, H - 26, W, H), fill=(0, 0, 0))
        dr.text((10, H - 22), stamp_label(ms, name) + ("   [blurred]" if blurred else "   [raw: private]"), fill=(255, 255, 255), font=font)
        out = workdir / ("%05d.png" % i)
        canvas.save(out, compress_level=1)
        files.append(out)
        times.append(ms)
    return files, times


def make_video(files, durs, out):
    lst = out.parent / (out.stem + ".concat.txt")
    lst.write_text(concat_text(files, durs))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", "fps=10,format=yuv420p",
           "-c:v", "libx264", "-crf", "23", "-preset", "veryfast", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    lst.unlink(missing_ok=True)
    if r.returncode:
        raise RuntimeError("ffmpeg: " + r.stderr[-300:])


def cmd_finish(a):
    d = Path(a.dir)
    meta = json.loads((d / "meta.json").read_text())
    ended = json.loads((d / "ended.json").read_text()) if (d / "ended.json").exists() else {"why": "capture died", "frames": 0}
    frames = sorted((d / "frames").glob("f*.png")) if (d / "frames").exists() else []
    if not frames:
        (d / "finished.json").write_text(json.dumps({"ok": False, "why": ended.get("why"), "note": "no frames"}))
        print("no frames in", d)
        return 1
    name = meta["name"]
    result = {"ok": True, "why": ended.get("why"), "frames": len(frames)}
    for kind in ("raw", "blurred"):
        work = d / ("work-" + kind)
        work.mkdir(exist_ok=True, mode=0o700)
        files, times = render_frames(d, kind == "blurred", work)
        durs = durations(times)
        out = d / ("%s-%s.mp4" % (name, kind))
        make_video(files, durs, out)
        os.chmod(out, 0o600)
        shutil.rmtree(work, ignore_errors=True)
        result[kind] = str(out)
        if kind == "blurred":
            (d / "timeline.json").write_text(json.dumps(timeline(times, durs)))
            result.update(startMs=times[0], endMs=times[-1], seconds=round(sum(durs), 1))
    (d / "finished.json").write_text(json.dumps(result, indent=1))
    first_url = ""
    try:
        first_url = json.loads((d / "rects.ndjson").read_text().splitlines()[0]).get("url", "")
    except Exception:
        pass
    row = "\t".join([name, dt.datetime.fromtimestamp(result["startMs"] / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                     dt.datetime.fromtimestamp(result["endMs"] / 1000).strftime("%H:%M:%S"), str(result["frames"]),
                     "%.0fs" % result["seconds"], result["why"], result["raw"], result["blurred"], first_url])
    with open(home() / "index.tsv", "a") as f:
        f.write(row + "\n")
    print("done:", row)
    return 0


def cmd_cut(a):
    d = find_rec(a.name)
    fin = json.loads((d / "finished.json").read_text())
    rows = json.loads((d / "timeline.json").read_text())
    date = json.loads((d / "meta.json").read_text())["date"]
    s = video_offset(rows, wall_ms(date, a.frm))
    e = video_offset(rows, wall_ms(date, a.to)) + 2
    out = Path(a.out) if a.out else d / ("%s-clip-%s-%s.mp4" % (d.name, a.frm.replace(":", ""), a.to.replace(":", "")))
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "%.2f" % s, "-to", "%.2f" % e, "-i", fin["blurred"],
                        "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-300:], file=sys.stderr)
        return 1
    os.chmod(out, 0o600)
    print("blurred clip (%.1fs to %.1fs of the video):" % (s, e), out)
    return 0


def cmd_check(a):
    d = find_rec(a.name)
    fin = json.loads((d / "finished.json").read_text())
    outdir = d / "check"
    shutil.rmtree(outdir, ignore_errors=True)
    outdir.mkdir(mode=0o700)
    secs = fin["seconds"]
    n = max(2, a.frames)
    for i in range(n):
        t = secs * (i + 0.5) / n
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "%.2f" % t, "-i", fin["blurred"], "-frames:v", "1", str(outdir / ("check-%02d.png" % i))])
    print("look at these at full size before the blurred video leaves the machine:")
    for p in sorted(outdir.glob("*.png")):
        print(" ", p)
    return 0


def cmd_prune(a):
    cutoff = time.time() - a.days * 86400
    n = 0
    for d in rec_dirs():
        fr = d / "frames"
        if fr.exists() and (d / "finished.json").exists() and (d / "finished.json").stat().st_mtime < cutoff:
            shutil.rmtree(fr, ignore_errors=True)
            (d / "rects.ndjson").unlink(missing_ok=True)
            n += 1
    print("pruned the capture frames of %d recordings" % n)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tab_record.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("job")
    s.add_argument("--tab", required=True, help="part of the tab's address")
    s.add_argument("--port")
    s.add_argument("--flag-dir", action="append")
    s.add_argument("--flag-prefix", action="append")
    s.add_argument("--cap", type=int, default=90, help="minutes")
    s.add_argument("--auto", action="store_true", help="started by a script, not a person")
    s.set_defaults(fn=cmd_start)
    s = sub.add_parser("stop"); s.add_argument("name"); s.set_defaults(fn=cmd_stop)
    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("list"); s.add_argument("--limit", type=int, default=20); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("finish"); s.add_argument("dir"); s.set_defaults(fn=cmd_finish)
    s = sub.add_parser("cut"); s.add_argument("name"); s.add_argument("--from", dest="frm", required=True); s.add_argument("--to", required=True); s.add_argument("--out"); s.set_defaults(fn=cmd_cut)
    s = sub.add_parser("check"); s.add_argument("name"); s.add_argument("--frames", type=int, default=6); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("prune"); s.add_argument("--days", type=int, default=7); s.set_defaults(fn=cmd_prune)
    a = ap.parse_args(argv)
    if a.cmd == "start":
        home().mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            cmd_prune(argparse.Namespace(days=7))
        except Exception:
            pass
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
