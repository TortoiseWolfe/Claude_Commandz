# Transcribe Recording (private, offline)

Transcribe a meeting recording locally on the GPU, then clean it and extract actions.

## Input
$ARGUMENTS

Expected form: `INPUT_FILE --client NAME --title SLUG [--speakers "Jon,Other"] [--model large-v3]`.
If the file, client or title is missing, ask. OBS files live under /mnt/c/Users/<user>/Videos
(quote paths with spaces).

## Steps

1. Run `~/.claude/scripts/meeting_transcribe/run.sh $ARGUMENTS` (quote the input path).
   Output lands in `~/meetings/<client>/<YYYY-MM-DD>-<title>/` as `transcript.md`,
   `transcript.srt`, `transcript.json`. Report which model ran (it falls back to `medium`
   on CUDA out-of-memory). First-time setup: `run.sh --download-model large-v3`.
2. Apply the rules in `~/.claude/commands/clean-transcript.md` to `transcript.md` and write
   the result as `transcript-clean.md` in the same folder. Keep speaker labels and timestamps.
3. Write `actions.md` in the same folder with three sections: **Decisions**,
   **Action items** (each with an owner and, if stated, a due date), **Open questions**.

## Privacy rules (non-negotiable)

- Client meetings stay in `~/meetings`. Never copy them into any git repo.
- TranScripts is PUBLIC: never put a client meeting there.
- Never send transcripts or notes to the free review panel, to Muse, or to any other
  external service. Work on them only in this session.
- Do not paste transcript content into commit messages, issues, or emails without being asked.
