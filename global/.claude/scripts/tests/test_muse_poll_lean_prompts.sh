#!/usr/bin/env bash
# 29 Oct 2026 (token study): the poller's claude calls run in a lean folder, so the workspace CLAUDE.md does not load and cannot give
# them its rules. The rules those passes act under are written into their own prompts; this holds the prompts to that, so an edit
# cannot quietly drop one. (The cwd and flags of the calls themselves are held by test_muse_poll_trim.sh.)
set -uo pipefail
D=$(cd -- "$(dirname -- "$0")/.." && pwd)
fails=0
need() {  # <file> <fixed text> <what>
  if grep -qF -- "$2" "$D/$1"; then printf 'PASS: %s\n' "$3"; else printf 'FAIL: %s (%s lacks: %s)\n' "$3" "$1" "$2" >&2; fails=$((fails+1)); fi
}
for f in muse_poll.handle.prompt muse_poll.client-handle.prompt; do
  need "$f" 'Standing rules.' "$f: carries the standing rules block"
  need "$f" 'Credentials never reach your context' "$f: credentials rule"
  need "$f" 'redact_view.py' "$f: the one way to inspect config"
  need "$f" 'Email is drafts only, never sent.' "$f: drafts only"
  need "$f" 'Use they/them for anyone whose pronouns they have not stated' "$f: pronouns"
  need "$f" 'Never download, keep, move or delete Jonathan' "$f: no unasked content handling"
  need "$f" 'Your working folder holds nothing' "$f: says the working folder is empty"
done
need muse_poll.handle.prompt 'hub/people.md' 'handle prompt: people index before guessing a handle or address'
need muse_poll.handle.prompt 'never a URL' 'handle prompt: no URLs in notes'
need muse_poll.check.prompt 'Standing rules.' 'check prompt: carries the standing rules line'
need muse_poll.check.prompt 'never sends, drafts, labels or deletes mail' 'check prompt: read-only'
need muse_poll.check.prompt 'credentials never reach your context' 'check prompt: credentials rule'
# The client prompt's placeholder is filled by muse_poll.sh; no stray home path may sit in it.
if grep -q '/home/' "$D/muse_poll.client-handle.prompt"; then echo 'FAIL: client prompt has a hard-coded home path' >&2; fails=$((fails+1)); else echo 'PASS: client prompt uses {{HOME}} only'; fi
# The check's last line must still be the NEW or NONE verdict contract.
need muse_poll.check.prompt 'NEW <space-separated ids>' 'check prompt: verdict contract intact'
[ "$fails" = 0 ] && echo "ok - the lean poller prompts carry their own rules" || { echo "$fails failed" >&2; exit 1; }
