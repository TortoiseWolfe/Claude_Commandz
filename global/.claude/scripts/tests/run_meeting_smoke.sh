#!/usr/bin/env bash
# Run the meeting_transcribe end-to-end smoke test (real container, GPU, about 80 s once the image and model are cached) and,
# only if it passed with nothing skipped, record meeting_smoke.stamp. Every docker job goes through docker-heavy.sh.
# Run it after any change under meeting_transcribe/ (the default suite fails until you do).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
out="$(timeout 1500 python3 -m pytest -q -s -rs -p no:cacheprovider smoke_meeting_transcribe.py 2>&1)" && rc=0 || rc=$?
printf '%s\n' "$out"
[ "$rc" -eq 0 ] || { echo "run_meeting_smoke.sh: the smoke test failed; stamp not written" >&2; exit 1; }
if grep -qiE '[0-9]+ (skipped|deselected|xfailed)' <<<"$out" || ! grep -qE '[0-9]+ passed' <<<"$out"; then
  echo "run_meeting_smoke.sh: the run was not a clean pass (skips or nothing ran); stamp not written" >&2; exit 1
fi
python3 meeting_smoke_stamp.py write
