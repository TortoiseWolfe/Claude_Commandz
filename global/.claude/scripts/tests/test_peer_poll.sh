#!/usr/bin/env bash
# Tests for peer_poll.sh (the notes poller on the second tower), peer_poll_record.sh and the two prompts.
# 29 Oct 2026, triage items 99 and 129: show windows cap the backoff at 5 minutes, [MUSE>CC] notes that sit
# unhandled 30+ minutes are queued for the hub (never answered), and the claude calls run lean.
# Everything runs in a temp HOME with a fake claude; the real poller, real claude and real mail are never touched.
set -uo pipefail
D=$(cd -- "$(dirname -- "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok()   { printf 'PASS: %s\n' "$1"; }
bad()  { printf 'FAIL: %s\n' "$1" >&2; fails=$((fails+1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# --- a fake claude: records cwd and arguments, answers the check with $FAKE_CHECK ---
cat >"$T/claude" <<'EOF'
#!/usr/bin/env bash
a="$*"; echo "$PWD|${a//$'\n'/ }" >>"$FAKE_LOG"
case "$*" in *"--model haiku"*) echo "${FAKE_CHECK:-NONE}" ;; *) echo "handled" ;; esac
EOF
chmod +x "$T/claude"

# run_poll <name> [VAR=value ...]: a fresh state folder; prints nothing, leaves $T/<name>/ behind
run_poll() {
  local n=$1; shift
  local R=$T/$n; mkdir -p "$R/home/.claude/state" "$R/home/.config/afa" "$R/lean"
  : >"$R/calls"
  env -i PATH="$PATH" HOME="$R/home" PEER_SCRIPTS="$D" PEER_POLL_LOCK="$R/lock" PEER_CLAUDE="$T/claude" \
    PEER_STATE="$R/home/.claude/state" AFA_LEAN_DIR="$R/lean" FAKE_LOG="$R/calls" \
    PEER_POLL_HOUR=12 PEER_SHOW_WINDOWS="$R/home/.config/afa/show-windows.tsv" "$@" \
    bash "$D/peer_poll.sh" >/dev/null 2>&1
  return 0
}
# Seed a state folder before a run: quiet <minutes> since the last note, last check <minutes> ago.
seed() {  # <name> <quiet-min> <since-check-min>
  local S=$T/$1/home/.claude/state; mkdir -p "$S"
  touch -d "$2 minutes ago" "$S/peer-active.stamp"; touch -d "$3 minutes ago" "$S/peer-poll-last-check.stamp"
}
ran() { [ -s "$T/$1/calls" ]; }

# Quiet 120 min means a 30 min wait. Last check 10 min ago: not due outside a window, due (cap 5 min) inside one.
mkdir -p "$T/a/home/.config/afa"; seed a 120 10
run_poll a
check "outside a show window: 10 min after the last check at 30 min backoff is not due" '! ran a'

mkdir -p "$T/b/home/.config/afa"; seed b 120 10
printf '# comment\n*\t00:00\t23:59\n' >"$T/b/home/.config/afa/show-windows.tsv"
run_poll b
check "inside a show window: the 5 min cap makes it due" 'ran b'
check "show window: the log says so" 'grep -q "show window" "$T/b/home/.claude/state/peer-poll.log"'

mkdir -p "$T/c/home/.config/afa"; seed c 120 3
printf '*\t00:00\t23:59\n' >"$T/c/home/.config/afa/show-windows.tsv"
run_poll c
check "inside a show window: 3 min after the last check is still not due" '! ran c'

mkdir -p "$T/d/home/.config/afa"; seed d 120 10
printf 'Mon,Tue\t19:00\t23:00\n' >"$T/d/home/.config/afa/show-windows.tsv"
run_poll d PEER_POLL_DOW=Wed PEER_POLL_HM=20:00
check "window on other days does not apply" '! ran d'
mkdir -p "$T/d2/home/.config/afa"; seed d2 120 10
printf 'Mon,Tue\t19:00\t23:00\n' >"$T/d2/home/.config/afa/show-windows.tsv"
run_poll d2 PEER_POLL_DOW=Tue PEER_POLL_HM=20:00
check "window on a listed day applies" 'ran d2'
mkdir -p "$T/d3/home/.config/afa"; seed d3 120 10
printf 'Mon,Tue\t19:00\t23:00\n' >"$T/d3/home/.config/afa/show-windows.tsv"
run_poll d3 PEER_POLL_DOW=Tue PEER_POLL_HM=23:30
check "after the window ends it does not apply" '! ran d3'

mkdir -p "$T/e/home/.config/afa"; seed e 120 10
printf '*\t22:00\t02:00\n' >"$T/e/home/.config/afa/show-windows.tsv"
run_poll e PEER_POLL_HM=01:00
check "a window past midnight applies after midnight" 'ran e'
mkdir -p "$T/e2/home/.config/afa"; seed e2 120 10
printf '*\t22:00\t02:00\n' >"$T/e2/home/.config/afa/show-windows.tsv"
run_poll e2 PEER_POLL_HM=23:00
check "a window past midnight applies before midnight" 'ran e2'
mkdir -p "$T/e3/home/.config/afa"; seed e3 120 10
printf '*\t22:00\t02:00\n' >"$T/e3/home/.config/afa/show-windows.tsv"
run_poll e3 PEER_POLL_HM=12:00
check "a window past midnight does not apply at noon" '! ran e3'

mkdir -p "$T/f/home/.config/afa"; seed f 120 10
printf 'garbage line\n*\t25\n' >"$T/f/home/.config/afa/show-windows.tsv"
run_poll f
check "a malformed windows file means no window" '! ran f'

mkdir -p "$T/g/home/.config/afa"; seed g 120 10
printf '*\t00:00\t23:59\n' >"$T/g/home/.config/afa/show-windows.tsv"
run_poll g PEER_POLL_SHOW_CAP=1200
check "PEER_POLL_SHOW_CAP overrides the 5 min cap" '! ran g'

# A short backoff is left alone by the cap (quiet 8 min = 2 min wait; last check 1 min ago: not due even in a window).
mkdir -p "$T/h/home/.config/afa"; seed h 8 1
printf '*\t00:00\t23:59\n' >"$T/h/home/.config/afa/show-windows.tsv"
run_poll h
check "the cap never lengthens a short wait" '! ran h'

# Lean calls: cwd is the lean folder and --disable-slash-commands is passed to both calls.
mkdir -p "$T/i/home/.config/afa"; seed i 120 40
run_poll i FAKE_CHECK="NEW r123456 cc:r789012"
check "check call runs in a lean folder (hub_lean.py or AFA_LEAN_DIR), not in the repos folder" 'grep -q "^$T/i/[^|]*lean|" "$T/i/calls"'
check "both claude calls get --disable-slash-commands" '[ "$(grep -c -- "--disable-slash-commands" "$T/i/calls")" = 2 ]'
check "a plain id in NEW starts the handler (two calls)" '[ "$(wc -l <"$T/i/calls")" = 2 ]'
check "a plain id in NEW marks the exchange live" '[ "$(stat -c %Y "$T/i/home/.claude/state/peer-active.stamp")" -gt "$(date -d "5 minutes ago" +%s)" ]'

mkdir -p "$T/j/home/.config/afa"; seed j 120 40
run_poll j FAKE_CHECK="NEW cc:r789012"
check "a cc-only NEW still starts the handler" '[ "$(wc -l <"$T/j/calls")" = 2 ]'
check "a cc-only NEW does not speed the poll up" '[ "$(stat -c %Y "$T/j/home/.claude/state/peer-active.stamp")" -lt "$(date -d "60 minutes ago" +%s)" ]'

mkdir -p "$T/k/home/.config/afa"; seed k 120 40
run_poll k FAKE_CHECK=NONE
check "NONE runs only the check" '[ "$(wc -l <"$T/k/calls")" = 1 ]'

# The record script.
R=$T/rec; mkdir -p "$R"
rec() { PEER_STATE="$R" bash "$D/peer_poll_record.sh" "$@"; }
rec ccseen r789012; rec ccseen r789012
check "ccseen appends the id once" '[ "$(grep -cxF r789012 "$R/peer-muse-cc-seen.txt")" = 1 ]'
check "ccseen refuses a bad id" '! rec ccseen "bad id;rm" 2>/dev/null'
check "ccseen does not touch the processed record" '[ ! -e "$R/peer-notes-processed.txt" ]'
rec inbox "[MUSE>CC] note unhandled 30+ min (NX-01 may be down): s | g | r789012"
check "inbox queues the line" 'grep -q "NX-01 may be down" "$R/peer-inbox.md"'
rec inbox "[MUSE>CC] note unhandled 30+ min (NX-01 may be down): s | g | r789012"
check "the same note id queued twice writes one line" '[ "$(grep -c "| r789012\$" "$R/peer-inbox.md")" = 1 ]'
check "a repeat says it was already queued" '[ "$(rec inbox "x | y | r789012")" = "already queued: r789012" ]'
rec inbox "another note | what | r555555"
check "a different id is still queued" '[ "$(grep -c "| r555555\$" "$R/peer-inbox.md")" = 1 ]'
rec inbox "a line with no id field"
rec inbox "a line with no id field"
check "a line with no id-shaped last field is always added" '[ "$(grep -c "a line with no id field" "$R/peer-inbox.md")" = 2 ]'
rec processed r111111
check "processed still works" 'grep -qxF r111111 "$R/peer-notes-processed.txt"'
check "an unknown verb is refused" '! rec nonsense 2>/dev/null'

# The prompts.
need() { grep -qF -- "$2" "$D/$1" && ok "$3" || bad "$3 ($1 lacks: $2)"; }
need peer_poll.check.prompt 'subject:"MUSE>CC"' 'check prompt: looks for [MUSE>CC] notes'
need peer_poll.check.prompt '30 or more minutes' 'check prompt: only old ones'
need peer_poll.check.prompt 'peer-muse-cc-seen.txt' 'check prompt: skips ones already queued'
need peer_poll.check.prompt 'cc:<id>' 'check prompt: marks them cc:<id>'
need peer_poll.check.prompt 'NONE' 'check prompt: NONE verdict kept'
need peer_poll.handle.prompt '"[CC>MUSE] NCC-74656: <topic>" to Hatch' 'handle prompt: the tower writes to Hatch under the [CC>MUSE] tag (Hatch wakes only on it)'
need peer_poll.check.prompt 'starts with "[CC>MUSE] NCC-74656:"' 'check prompt: skips its own [CC>MUSE] NCC-74656: notes (step 3 matches them)'
check "tower prompts no longer tell it to write [NCC-74656>MUSE]" '! grep -q "\"\[NCC-74656>MUSE\] <topic>\"" "$D/peer_poll.handle.prompt"'
need peer_poll.handle.prompt 'NEVER answers one' 'handle prompt: never answers a [MUSE>CC] note'
need peer_poll.handle.prompt 'peer_poll_record.sh ccseen' 'handle prompt: records the cc note as seen'
need peer_poll.handle.prompt 'no reply, no draft' 'handle prompt: no reply or draft for cc'
need peer_poll.handle.prompt 'Never call label_message' 'handle prompt: no labels'
check "check prompt no longer says to never look for [MUSE>CC]" '! grep -q "Never look for" "$D/peer_poll.check.prompt"'

# The public-repo rule: no mailbox address or home-network IP in anything this test covers.
for f in peer_poll.sh peer_poll.check.prompt peer_poll.handle.prompt peer_poll_record.sh; do
  check "$f: no email address or LAN IP" '! grep -Eq "[A-Za-z0-9._-]+@[A-Za-z0-9-]+\.[a-z]{2,}|192\.168\." "$D/$f"'
done

[ "$fails" = 0 ] && echo "ok - peer poller" || { echo "$fails failed" >&2; exit 1; }
