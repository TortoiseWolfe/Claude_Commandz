#!/usr/bin/env bash
# End-to-end smoke for tab_record.py: launches its OWN headless Chromium (never the fleet browser) on a page that holds a fake email,
# phone number and LinkedIn address, films it with tab_record.py, and checks that the raw video shows them and the blurred one does not.
# Needs node + Playwright (~/.cache/demo-video/node_modules), ffmpeg and PIL. Not part of the default suite: run it after any change to
# tab_record.py or tab_record_capture.cjs.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scripts="$(dirname "$here")"
pw="${TAB_RECORD_PLAYWRIGHT:-$HOME/.cache/demo-video/node_modules/playwright}"
out="$(mktemp -d /tmp/cc-test-tab-record-smoke-XXXXXX)"
port=9461
export TAB_RECORD_HOME="$out/rec"
cleanup() { [ -n "${bpid:-}" ] && kill "$bpid" 2>/dev/null || true; rm -rf "$out"; }
trap cleanup EXIT
cat > "$out/page.html" <<'HTML'
<html><body style="font:28px sans-serif;margin:40px">
<h1>Smoke page</h1>
<p>Contact: jane.smoke@example.com</p>
<p>Phone: 423-555-0187</p>
<p>Profile: linkedin.com/in/jane-smoke</p>
<iframe srcdoc="&lt;p style='font:24px sans-serif'&gt;Inside: frame.person@example.com&lt;/p&gt;" style="width:600px;height:80px;border:1px solid #888"></iframe>
<p id="late" style="display:none">Application submitted</p>
</body></html>
HTML
cat > "$out/browser.cjs" <<JS
const { chromium } = require('$pw')
;(async () => {
  const b = await chromium.launch({ args: ['--remote-debugging-port=$port'] })
  const p = await b.newPage({ viewport: { width: 1000, height: 600 }, deviceScaleFactor: 2 })
  await p.goto('file://$out/page.html')
  setTimeout(() => process.exit(0), 120000)
})()
JS
node "$out/browser.cjs" & bpid=$!
for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:$port/json/version" >/dev/null && break; sleep 1; done
python3 "$scripts/tab_record.py" start smoke --tab page.html --port "$port" --cap 2
sleep 6
python3 "$scripts/tab_record.py" stop smoke
for _ in $(seq 1 90); do [ -f "$(find "$out/rec" -name finished.json | head -1)" ] 2>/dev/null && break; sleep 1; done
d="$(find "$out/rec" -mindepth 2 -maxdepth 2 -type d -name smoke | head -1)"
test -s "$d/smoke-raw.mp4" && test -s "$d/smoke-blurred.mp4"
python3 - "$d" <<'PY'
import json, subprocess, sys
d = sys.argv[1]
rects = [json.loads(l) for l in open(d + "/rects.ndjson")]
kinds = {r["why"] for line in rects for r in line["rects"]}
assert "text" in kinds, kinds
# three personal strings on the page and one inside an iframe; the page runs at 2x so the scale is proved too
assert sum(1 for r in rects[0]["rects"] if r["why"] == "text") >= 3, rects[0]
assert any(r["why"].startswith("frame-") for r in rects[0]["rects"]), rects[0]
assert rects[0]["dpr"] > 0.5, rects[0]
def frame(video, out):
    subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", video, "-frames:v", "1", out])
frame(d + "/smoke-raw.mp4", d + "/raw.png")
frame(d + "/smoke-blurred.mp4", d + "/blur.png")
from PIL import Image, ImageChops
page = (0, 0, 1280, 720)  # above the clock bar, which differs between the two videos by design
a, b = Image.open(d + "/raw.png").convert("L").crop(page), Image.open(d + "/blur.png").convert("L").crop(page)
diff = ImageChops.difference(a, b).point(lambda v: 255 if v > 40 else 0).getbbox()
assert diff, "blurred video is identical to the raw one"
print("tab_record smoke: ok (blurred differs from raw at", diff, ")")
PY
# the delivery steps: cut (with a size limit), check, approve, find, ok. This clip is of the fake page only.
tr="python3 $scripts/tab_record.py"
secs="$(python3 -c "import json,sys;f=json.load(open('$d/finished.json'));print(f['startMs']//1000)")"
from_t="$(date -d @"$secs" +%H:%M:%S)"
to_t="$(date -d @"$((secs + 4))" +%H:%M:%S)"
clip="$d/smoke-clip.mp4"
$tr cut smoke --from "$from_t" --to "$to_t" --out "$clip" --max-mb 9.5
test -s "$clip" && test -s "$clip.json"
$tr ok "$clip" && { echo "an unapproved clip passed ok"; exit 1; } || true
$tr approve "$clip" --by smoke && { echo "approve ran without check"; exit 1; } || true
$tr check "$clip" --frames 3 >/dev/null
test -s "$clip.check/check-00.png"
$tr approve "$clip" --by smoke
$tr ok "$clip"
$tr cut smoke --from "$from_t" --to "$to_t" --out "$d/tiny.mp4" --max-mb 0.001 && { echo "a clip over the limit was not reported"; exit 1; } || true
$tr find --at "$(date -d @"$((secs + 2))" +%H:%M:%S)" | grep -q smoke
[ -n "${TAB_RECORD_KEEP_CLIP:-}" ] && { cp "$clip" "$clip.json" "$clip.approved" "$TAB_RECORD_KEEP_CLIP"/; true; }
echo "tab_record smoke: delivery steps ok"
