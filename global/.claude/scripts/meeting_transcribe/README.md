# Private meeting capture and offline transcription

Record client meetings locally and turn them into speaker-labelled transcripts on this PC.
Nothing is streamed, uploaded or sent to an outside model.

**Why it exists (2026-10-01).** A client agreed to her meetings being *recorded*, not *broadcast*.
The only transcript route we had was to stream to Twitch, export to YouTube, then pull YouTube's
captions. That put a client meeting in public. This replaces that route.

**Status:** built and verified 2026-10-01. A two-track smoke test labelled both speakers
correctly on the GPU, and `large-v3` fits (peak about 6.0 GB on the RTX 3070 Ti). Not yet done:
- the OBS two-track setup on the real machine (below)
- a first real meeting
- a test with a real `/mnt/c` path containing spaces
- the out-of-memory fallback to `medium`, which has never been triggered

## The rules (they come before the tool)

1. **Client meetings are recorded, never streamed.** Use OBS **Start Recording**, never
   **Start Streaming**. No VODs, no YouTube exports.
2. **Transcripts stay in `~/meetings/<client>/`** (mode 700). Never in a git repo:
   `run.sh` refuses anything under `~/repos` or inside any git work tree. TranScripts in
   particular is a **public** repo.
3. **Client transcripts are client-class.** They never go to the free review panel or to Muse,
   and only to Claude like any other client material. Don't paste their contents into notes or
   issues.
4. **Recording needs the other person's consent.** Recording consent is not broadcast consent.

## One-time setup

**1. Download the model** (the only step that touches the network; it fills a Docker volume):

```bash
~/.claude/scripts/meeting_transcribe/run.sh --download-model large-v3
```

**2. OBS: record with two audio tracks.** Your mic goes on track 1 and the meeting audio on
track 2, which is how the transcriber tells you from them without an extra AI model.
- Settings → Output → Output Mode **Advanced** → **Recording** tab:
  - Recording Path: a local folder that is not cloud-synced.
  - Recording Format: **mkv**. OBS recommends it because an interrupted mkv isn't lost.
  - Audio Track: tick **1** and **2**.
- Per source, assign tracks in **Advanced Audio Properties**: Mic/Aux → track 1 only,
  Desktop Audio → track 2 only.
  - **Check these labels against OBS's own docs before relying on them.** The track-assignment
    step comes from OBS's Knowledge Base article "Advanced Recording Guide And Multi Track
    Audio", which wasn't re-read word for word when this was written.
- OBS guides: https://obsproject.com/kb/standard-recording-output-guide and https://obsproject.com/kb

**3. Test:** record one minute of you talking while a video plays, then run it (below). You
should get alternating `Jon:` and `Other:` turns.

## Every meeting

```bash
/transcribe-recording "<path to the .mkv>" --client <client> --title <slug> --speakers "Jon,<their name>"
# or directly:
~/.claude/scripts/meeting_transcribe/run.sh "<file>" --client <client> --title <slug> --speakers "Jon,<name>"
```

Output in `~/meetings/<client>/<YYYY-MM-DD>-<slug>/`:
- `transcript.md`: timestamped turns, `**[hh:mm:ss] Name:** text`
- `transcript.srt`, `transcript.json`
- From the command: `transcript-clean.md` (the `/clean-transcript` rules) and `actions.md`
  (decisions, action items with owners, open questions)

A single-track file, such as an old download, works too; it just comes out unlabelled as `Speaker`.

## How it works

- `Dockerfile`: CUDA 12.4 + cuDNN 9, `faster-whisper==1.1.1`. Models live in the Docker
  volume `meeting-whisper-models`, not in the image.
- `run.sh`:
  - makes the output folder (umask 077) and refuses `~/repos` and git trees
  - runs the container with `--gpus all --network none`, the input folder mounted read-only
    and the output folder writable
- `transcribe.py`:
  - ffprobe finds the audio tracks; ffmpeg splits them to 16 kHz mono
  - each track is transcribed with voice activity detection, and segments are labelled by track
  - segments are merged by start time and consecutive segments from the same speaker are joined
- Tests: `~/.claude/scripts/tests/test_meeting_transcribe.py`. The Docker smoke test runs with
  `MEETING_SMOKE=1`.

## Known quirks

- Offline runs print a harmless "corrupted tree cache … Permission denied" warning, because the
  models volume is mounted read-only.
- More than two people on the meeting side all land on track 2 as one speaker. Per-person
  labels would need a diarization model (pyannote, which needs a Hugging Face token). Not added.

Backed up publicly in Claude_Commandz (`global/.claude/scripts/meeting_transcribe/`). It's
generic: no client names.
