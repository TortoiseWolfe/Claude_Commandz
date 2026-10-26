#!/usr/bin/env bash
# End-to-end smoke for demo_video.py: serves a tiny static page, then runs the real audio, record and assemble stages on
# fixtures/demo_video/smoke.spec.json (Docker: the Piper and Playwright images; about 1 minute once they are cached).
# Not part of the default suite. Run it after any change to demo_video.py or demo_video_record.cjs.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scripts="$(dirname "$here")"
out="$(mktemp -d /tmp/cc-test-demo-video-smoke-XXXXXX)"
web=dv-smoke-web-$$
cleanup() { timeout 60 docker rm -f "$web" >/dev/null 2>&1 || true; rm -rf "$out"; }
trap cleanup EXIT
python3 "$scripts/demo_video.py" doctor
# The fixture page is served from a container; the recorder joins its network, so http://localhost:8765 reaches it (host
# networking does not share WSL's localhost on every Docker setup).
timeout 120 docker run -d --rm --name "$web" -v "$here/fixtures/demo_video:/w:ro" -w /w python:3.12-slim python -m http.server 8765 >/dev/null
sleep 2
spec="$out/smoke.spec.json"
sed "s|\"network\": \"host\"|\"network\": \"container:$web\"|" "$here/fixtures/demo_video/smoke.spec.json" > "$spec"
timeout 900 python3 "$scripts/demo_video.py" run "$spec" --out "$out"
python3 - "$out" <<'PY'
import json, subprocess, sys
out = sys.argv[1]
r = json.load(open(out + "/smoke-result.json"))
def dur(f):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]))
assert r["checks"] and all(c["ok"] for c in r["checks"]), r["checks"]
assert len(r["checks"]) == 3, r["checks"]
assert abs(dur(out + "/smoke-demo.mp4") - dur(out + "/smoke-captions-only.mp4")) < 0.5
assert open(out + "/smoke.srt").read().startswith("1\n00:00:00,"), "srt"
assert "Narration: AI voice." in open(out + "/smoke.srt").read()
streams = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", out + "/smoke-demo.mp4"], text=True).split()
assert "audio" in streams and "video" in streams, streams
print("demo_video smoke: ok, narrated %.1f s, %d checks, warnings: %s" % (r["narrated"], len(r["checks"]), r["warnings"]))
PY
