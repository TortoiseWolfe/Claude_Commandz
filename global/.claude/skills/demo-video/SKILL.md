---
name: demo-video
description: Make a narrated PR demo video (AI voice, burned-in captions, on-screen checks) from a shot-list spec with one command. Use when asked for a demo video, a Loom-style walkthrough, or a screen recording of a change for a PR or a ticket.
---

# demo-video: a PR demo video from a spec

One command turns a spec file (the shot list) into a narrated cut, a captions-only cut, an SRT and a contact sheet.

```
python3 ~/.claude/scripts/demo_video.py run SPEC.json --out DIR
```

Outputs in DIR, where NAME is the spec's name: `NAME-demo.mp4` (narrated), `NAME-captions-only.mp4`, `NAME.srt`, `NAME-contact-sheet.png`, `NAME-result.json`.
Stages: audio (Piper TTS), record (Playwright, 1280x800), assemble (ffmpeg). `--stages audio,record` runs part; `--force-audio` redoes the voice; `--heavy` sends the recorder through `~/.claude/scripts/docker-heavy.sh`.

## Steps for a lane

1. Read the PR's diff and the ticket. Pick the 3 to 8 things a reviewer should see (before/after, the edge case, the fix working).
2. Write the spec from `spec.template.json` (beside this file). Real specs carry the app's URL, accounts and details: keep them in your lane folder (`<lane>/video/specs/`), never in a public repo.
3. `python3 ~/.claude/scripts/demo_video.py validate SPEC.json` until it says `spec ok`.
4. Bring up the app locally (the demo stack, with `docker compose -p NAME` and a timeout on every docker command) and sign in through the spec's own `setup` hook, off camera.
5. `python3 ~/.claude/scripts/demo_video.py run SPEC.json --out DIR`.
6. Read `NAME-result.json` and the run output: every on-screen check should be ok, `warnings` explained or empty. Open `NAME-contact-sheet.png` (6 frames): captions readable, the highlight on the right thing, no sign-in or personal data on screen. Check both durations (narrated and captions-only are within about 0.5 s of each other).
7. `python3 ~/.claude/scripts/demo_video.py open DIR/NAME-demo.mp4` puts the narrated cut on Jonathan's screen (PowerShell Start-Process); the window stays open until he closes it. Never close it.
8. Write VIDEO-READY in your report.md with both mp4 paths (narrated and captions-only), the duration, and the check results.
9. Tear the demo stack down (`docker compose -p NAME down`, with a timeout), remove demo users you created.

## Spec format (JSON)

```
{name, baseUrl, viewport?, gap? 0.7, voice?, title?,
 runner?  {image, network, user (host|root), mounts[{host, container, ro}], env, nodePath, workdir, timeoutSec, heavy, cpus, memory},
 setup?   {module, export?},      cleanup? {module},
 start?   {path, waitFor, settleMs},
 steps    [{say, caption?, actions[]}]}
```

- `say` is spoken; `caption` (default: the same text) is burned in. The first caption of every video is always `Narration: AI voice.`, added for you: never write it.
- Actions (`do`): `goto` (path), `scroll_to` (target, block), `scroll` (to: top|bottom), `point` (move the cursor to target), `highlight` (target, ms), `click`, `fill` (target, value), `press` (key), `wait` (ms), `wait_for` (target, state), `hook` (name, args).
- Checks (`check`): `visible`, `absent`, `count` (target, equals), `order` (within, texts), `text` (target, contains), `url` (matches). Each takes an optional `msg`. A failed check is a failed recording: fix the app or the spec, never the check.
- A target is exactly one of `css`, `text`, `role` (+`name`), `label`, `placeholder`, `testId`, plus optional `exact`, `within`, `nth`. Strings, or `{regex, flags}`.
- One hook module per spec (`setup.module`; `cleanup.module` must be the same file, and `hook` actions call its exports by name). Its path is relative to the spec's folder (mounted at `/spec` in the container). A hook is `exports.NAME = async (ctx, args) => {}` with `ctx = {page, context, browser, baseUrl, log, sleep, helpers, spec}`.
- `runner.network`: `host`, `bridge`, a network name, or `container:NAME` to share an app container's localhost. A local `baseUrl` needs one of these, or the recorder cannot see it.
- Each step is paced to its narration: the clip holds until the voice finishes, then `gap` seconds. A step whose actions run longer than its voice logs an overrun warning.

## Rules

- AI narration stays labelled `Narration: AI voice.` (automatic).
- Never sign in anywhere new. An app sign-in is the spec's own `setup` hook on a local demo stack, with an account made for the demo. The fleet browser (port 9333) belongs to the job lanes: this tool records in its own Playwright container.
- Docker is shared: a timeout is on every docker call already; heavy jobs use `--heavy`.
- Check the contact sheet before telling anyone the video is ready.
- The container freeze: on some WSL/Docker boxes any Chromium container freezes about 2.6 s every 33 s (reproduced with an idle bare Playwright script, so not the spec). The recorder logs these as `stalls`, `NAME-result.json` has `stalledSec` and `netOfStalls`, and a warning appears above 1 s. The video holds still then and is that much longer; run again if it matters.

## Install (no sudo)

`python3 ~/.claude/scripts/demo_video.py doctor` lists what is missing; `doctor --install` fixes what it can:

- Piper TTS: builds the Docker image `piper-tts-local` from `piper/Dockerfile` beside this file (python:3.12-slim, `pip install piper-tts`, the `en_US-lessac-medium` voice from the rhasspy/piper-voices repo on Hugging Face). Another voice: set `voice` in the spec and add its two files to the Dockerfile.
- Playwright: pulls `mcr.microsoft.com/playwright:v1.50.1-noble` and caches `playwright@1.50.1` into `~/.cache/demo-video` with npm inside that container (nothing installed on the host). The npm package version must match the image.
- ffmpeg on the host must have the `subtitles` filter (libass) and the DejaVu Sans font. `ffprobe` too.
- `powershell.exe` is only for `open` (WSL).
- Smoke test of the whole pipeline (about a minute, needs Docker): `bash ~/repos/Claude_Commandz/global/.claude/scripts/tests/run_demo_video_smoke.sh`.

## Getting the mp4 into Loom (finding from Loom's docs, 26 Oct 2026; no sign-in used)

- Web app only: New Video (top right of the video library), then Upload Video. Docs: https://support.atlassian.com/loom/docs/upload-or-import-videos/
- Accepts MP4, MOV, WebM, M4V, WMV, AVI; rejects over 4 GB, over 60 fps, over 4096x2160, over 12 hours, or no video track; up to 10 files at a time. H.264 + AAC is recommended (our output is).
- Uploads need a Business, Business+AI or Enterprise plan: free Starter and trials cannot upload.
- No open API ("Loom does not offer a open API at this time", https://support.atlassian.com/loom/docs/does-loom-have-an-open-api/). The Record SDK has no file upload, there is no official CLI, and the desktop app and extension document no import.
- The only possible automation is the Loom MCP `write_loom` toolset ("Upload, process, and publish new video recordings", https://support.atlassian.com/loom/docs/loom-mcp-tools/), which needs OAuth or an API token on an Atlassian Cloud site; whether it takes a file path is not documented. That is Jonathan's decision, so a lane builds no sign-in or upload flow: it hands over the mp4 paths.
- Loom auto-generates a transcript for uploads and an SRT can be downloaded from it, but your own subtitle file cannot be uploaded: the captions-only cut, with captions burned in, is what carries them.
