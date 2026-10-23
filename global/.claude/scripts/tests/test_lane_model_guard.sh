#!/usr/bin/env bash
# Tests for the mail tier in lane.sh (his rule, 21 Oct 7:15 PM: "why are you burning Fable on emails!"): a Claude lane doing mail starts,
# resumes and switches on sonnet; fable and opus are refused unless --allow-high-tier "<reason>" is given (and logged). Also lane.sh --model,
# which switches a running lane and puts back the USER default model that /model saves. A fake tmux plays the lane window and a fake HOME
# holds settings.json, the index and the log: nothing here touches the live lanes or the live settings.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-modelguard.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; REPO="$TMP/repo"; ST="$TMP/fake"
LANES="$HOME_T/.claude/state/lanes"; INDEX="$LANES/index.tsv"; TLOG="$LANES/tier-overrides.log"
SETTINGS="$HOME_T/.claude/settings.json"
SLUG="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$REPO")"; PROJ="$HOME_T/.claude/projects/$SLUG"
mkdir -p "$BIN" "$REPO" "$ST" "$LANES" "$PROJ" "$HOME_T/.claude"
printf '%s\n' '⏺ Merged.' '✻ Worked for 4m 2s · done 1:05 PM' '❯ ' >"$ST/pane"
echo old-sess >"$ST/cur"

cat >"$BIN/sleep" <<'STUB'
#!/usr/bin/env bash
exec /bin/sleep 0.02
STUB
# Fake window: names in FAKE_WINDOWS exist; send-keys -l <text> types, C-m submits. A submitted "/model X" saves X as the user default
# model in settings.json and prints "Set model to ..." on the pane (never in the transcript), the way Claude Code does.
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  has-session) exit 0 ;;
  list-windows) printf "%s\n" mail-sw plain-sw site-sw nowrite-sw ;;
  capture-pane) cat "$FAKE_ST/pane" ;;
  send-keys)
    if [ "${4:-}" = -l ]; then printf '%s' "${6:-}" >"$FAKE_ST/pending"
    elif [ "${4:-}" = C-m ] && [ -s "$FAKE_ST/pending" ]; then
      python3 -I - "$FAKE_ST" "$FAKE_PROJ" "$(cat "$FAKE_ST/pending")" "$HOME/.claude/settings.json" <<'PY'
import json, os, sys
st, proj, text, settings = sys.argv[1:5]
open(os.path.join(st, "pending"), "w").close()
open(os.path.join(st, "sent.log"), "a").write(text + "\n")
cur = open(os.path.join(st, "cur")).read().strip()
# /model is a local command: Claude Code never writes it to the transcript, it only prints a confirmation line on the screen.
if text.startswith("/model "):
    if not os.path.exists(os.path.join(st, "no-pane-line")):
        arg = text.split(None, 1)[1]
        open(os.path.join(st, "pane"), "a").write("Set model to %s 5.5 and saved as your default for new sessions\n" % arg.capitalize())
else:
    with open(os.path.join(proj, cur + ".jsonl"), "a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
if text.startswith("/model ") and os.path.exists(settings) and not os.path.exists(os.path.join(st, "no-save")):
    d = json.load(open(settings))
    d["model"] = text.split(None, 1)[1]
    open(settings, "w").write(json.dumps(d, indent=2) + "\n")
PY
    fi ;;
esac
STUB
chmod +x "$BIN/sleep" "$BIN/tmux"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
assert_contains() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
assert_not_contains() { case "$1" in *"$2"*) fail "$3 (unexpected: $2)" ;; *) ok ;; esac; }
assert_exit() { [ "$2" = "$1" ] || fail "$3 (expected exit $1, got $2; output: $LAST_OUT)"; ok; }

lane() {  # lane <args...>: run lane.sh against the fake home and window
  set +e
  LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" FAKE_ST="$ST" FAKE_PROJ="$PROJ" bash "$SCRIPT" "$@" 2>&1)"
  LAST_RC=$?
  set -e
}
P_ABSORB="$TMP/absorb.md"; printf '# Absorb lane: Acme wrote\n\nNew Gmail message IDs: 1\n' >"$P_ABSORB"
P_DRAFT="$TMP/draft.md"; printf 'Draft a reply to the email the client sent about the invoice and leave it in Gmail.\n' >"$P_DRAFT"
P_CODE="$TMP/code.md"; printf 'Fix the lint errors in the header component.\n' >"$P_CODE"
P_EMAILFEATURE="$TMP/emailfeature.md"; printf 'Add an email field to the signup form and validate it.\n' >"$P_EMAILFEATURE"
P_SITE="$TMP/site.md"; printf 'Fit the photos in data/sites/example-01 and email nobody.\n' >"$P_SITE"

# ---- start --------------------------------------------------------------------------------------------------------------------------
for hi in opus fable claude-fable-5-1 claude-opus-5-5 Opus; do
  lane --dry-run acme-mail "$REPO" "$hi" "$P_CODE"
  assert_exit 1 "$LAST_RC" "a -mail lane on $hi is refused"
  assert_contains "$LAST_OUT" "refusing mail model $hi" "the refusal names the model ($hi)"
  assert_contains "$LAST_OUT" '--allow-high-tier' "the refusal names the override ($hi)"
  assert_not_contains "$LAST_OUT" 'mkdir -p' "a refused lane makes no hand-off folder ($hi)"
done
for lo in sonnet haiku claude-sonnet-5-5; do
  lane --dry-run acme-mail "$REPO" "$lo" "$P_CODE"
  assert_exit 0 "$LAST_RC" "a -mail lane on $lo starts"
done
lane --dry-run client-work "$REPO" fable "$P_ABSORB"
assert_exit 1 "$LAST_RC" 'an absorb-template prompt on fable is refused whatever the lane is called'
lane --dry-run client-work "$REPO" opus "$P_DRAFT"
assert_exit 1 "$LAST_RC" 'a prompt about drafting a reply to an email on opus is refused'
lane --dry-run client-work "$REPO" sonnet "$P_DRAFT"
assert_exit 0 "$LAST_RC" 'the same prompt on sonnet starts'
# 22 Oct: opus and fable need a stated reason whatever the prompt; the mail tier is not involved for a code prompt.
lane --dry-run client-work "$REPO" opus "$P_CODE"
assert_exit 1 "$LAST_RC" 'a code prompt on opus is refused without a reason'
assert_contains "$LAST_OUT" 'opus and fable need a stated reason' 'the refusal says what is missing'
assert_not_contains "$LAST_OUT" 'refusing mail model' 'a code prompt is not treated as mail'
lane --dry-run client-work "$REPO" fable "$P_CODE"
assert_exit 1 "$LAST_RC" 'a code prompt on fable is refused without a reason'
lane --dry-run --allow-high-tier "needs the strongest tier for the design" client-work "$REPO" opus "$P_CODE"
assert_exit 0 "$LAST_RC" 'a code prompt on opus starts with a reason'
assert_contains "$LAST_OUT" 'would log the high-tier reason' 'a dry run says it would log the reason'
assert_not_contains "$LAST_OUT" 'mail-tier override' 'and it is not a mail override'
lane --dry-run client-work "$REPO" opus "$P_EMAILFEATURE"
assert_exit 1 "$LAST_RC" 'a code prompt that only mentions an email field is refused as high tier, not as mail'
assert_not_contains "$LAST_OUT" 'refusing mail model' 'an email field in a feature is not mail'
lane --dry-run client-work "$REPO" sonnet "$P_CODE"
assert_exit 0 "$LAST_RC" 'a code prompt on sonnet starts'
lane --dry-run acme-site-mail "$REPO" opus "$P_SITE"
assert_exit 0 "$LAST_RC" 'a site-model prompt keeps its own rule (opus allowed)'
lane --dry-run --engine codex acme-mail "$REPO" sonnet "$P_CODE"
assert_not_contains "$LAST_OUT" 'refusing mail model' 'the mail tier applies to Claude lanes only'

# --allow-high-tier takes a reason, and a start with it is allowed.
lane --dry-run --allow-high-tier
assert_exit 1 "$LAST_RC" '--allow-high-tier with nothing after it is refused'
lane --dry-run --allow-high-tier "  " acme-mail "$REPO" opus "$P_CODE"
assert_exit 1 "$LAST_RC" '--allow-high-tier with a blank reason is refused'
lane --dry-run --allow-high-tier "claim-check pass" acme-mail "$REPO" opus "$P_CODE"
assert_exit 0 "$LAST_RC" '--allow-high-tier "<reason>" lets opus start'
assert_contains "$LAST_OUT" 'would log the mail-tier override' 'a dry run says it would log the override'
[ ! -e "$TLOG" ] || fail 'a dry run writes no override log'; ok
lane --help
assert_contains "$LAST_OUT" '--allow-high-tier' 'help documents the mail override'
assert_contains "$LAST_OUT" '--model <name> <model>' 'help documents --model'

# ---- resume -------------------------------------------------------------------------------------------------------------------------
row() {  # row <name> <model> <prompt>
  printf '%s\t%s\t%s\t%s\tsubscription\t%s\t%s\told-sess\t-\thigh\toff\n' "$(date +%F)" "$1" "$REPO" "$2" "$3" "$TMP/$1-report.md" >>"$INDEX"
}
row res-ok sonnet "$P_CODE"
row res-opus-plain opus "$P_CODE"
row res-absorb opus "$P_ABSORB"
lane --dry-run --resume res-absorb
assert_exit 1 "$LAST_RC" 'resuming an absorb-prompt lane recorded on opus is refused'
assert_contains "$LAST_OUT" 'refusing mail model opus' 'the resume refusal names the model'
lane --dry-run --allow-high-tier "needs the claim check" --resume res-absorb
assert_exit 0 "$LAST_RC" '--allow-high-tier lets it resume'
lane --dry-run --resume res-ok
assert_exit 0 "$LAST_RC" 'a sonnet lane resumes'
lane --dry-run --resume res-opus-plain
assert_exit 1 "$LAST_RC" 'an opus lane with no recorded reason does not resume without one'
lane --dry-run --allow-high-tier "design review" --resume res-opus-plain
assert_exit 0 "$LAST_RC" 'a non-mail opus lane resumes with a reason'
printf '%s\t%s\t%s\t%s\tsubscription\t%s\t%s\told-sess\t-\thigh\toff\t-\t%s\n' "$(date +%F)" res-opus-ent "$REPO" opus "$P_CODE" "$TMP/res-opus-ent-report.md" "design review" >>"$INDEX"
lane --dry-run --resume res-opus-ent
assert_exit 0 "$LAST_RC" 'an opus lane whose row holds a reason resumes without being told again'
row res-site-opus opus "$P_SITE"
lane --dry-run --resume res-site-opus
assert_exit 0 "$LAST_RC" 'a site-model opus lane resumes with no reason'

# ---- --model: the lane switches, the user default does not ----------------------------------------------------------------------------
row mail-sw sonnet "$P_ABSORB"
row plain-sw sonnet "$P_CODE"
row site-sw opus "$P_SITE"
row nowrite-sw sonnet "$P_CODE"
orig_settings() { printf '{\n  "model": "claude-opus-5-5",\n  "env": {\n    "KEY": "do-not-print"\n  },\n  "theme": "dark"\n}\n' >"$SETTINGS"; }
orig_settings; ORIG="$(cat "$SETTINGS")"
: >"$ST/sent.log"

lane --model plain-sw opus
assert_exit 1 "$LAST_RC" '--model to opus without a reason is refused'
assert_contains "$LAST_OUT" 'opus and fable need a stated reason' 'the refusal says what is missing'
[ ! -s "$ST/sent.log" ] || fail 'a refused switch typed nothing'; ok
lane --allow-high-tier "model guard test" --model plain-sw opus
assert_exit 0 "$LAST_RC" '--model switches a running lane'
assert_contains "$(cat "$ST/sent.log")" '/model opus' '--model typed /model into the window'
[ "$(cat "$SETTINGS")" = "$ORIG" ] || fail "--model left the settings file as it found it, got: $(cat "$SETTINGS")"; ok
assert_contains "$LAST_OUT" 'user default model is still claude-opus-5-5' '--model says the user default is unchanged'
assert_not_contains "$LAST_OUT" 'do-not-print' '--model never prints the rest of settings.json'
idx="$(awk -F'\t' '$2=="plain-sw"{m=$4} END{print m}' "$INDEX")"
[ "$idx" = opus ] || fail "the index row follows the switch, got: $idx"; ok
other="$(awk -F'\t' '$2=="nowrite-sw"{m=$4} END{print m}' "$INDEX")"
[ "$other" = sonnet ] || fail "another lane's row is untouched, got: $other"; ok
cols="$(awk -F'\t' '$2=="plain-sw"{print NF}' "$INDEX" | tail -1)"
[ "$cols" = 13 ] || fail "the rewritten row has the reason in column 13 (column 12 padded), got $cols columns"; ok
tier="$(awk -F'\t' '$2=="plain-sw"{r=$12 "|" $13} END{print r}' "$INDEX")"
[ "$tier" = "-|model guard test" ] || fail "the reason is written to the row, got: $tier"; ok
assert_contains "$(cat "$TLOG")" 'model guard test' 'the high-tier reason is logged'
lane --tier plain-sw
assert_contains "$LAST_OUT" "opus	model guard test" '--tier prints the model and the reason'
lane --tier nobody-sw
assert_contains "$LAST_OUT" "?	-" '--tier of an unknown lane prints ? and -'
lane --tier site-sw
assert_contains "$LAST_OUT" "opus	site-model" '--tier names a site-model lane by its prompt'

# A user with no model key: the key /model adds is removed again.
printf '{\n  "theme": "dark"\n}\n' >"$SETTINGS"; ORIG="$(cat "$SETTINGS")"
lane --model plain-sw haiku
assert_exit 0 "$LAST_RC" '--model works with no model key set'
[ "$(cat "$SETTINGS")" = "$ORIG" ] || fail "the key /model added is removed, got: $(cat "$SETTINGS")"; ok
assert_contains "$LAST_OUT" 'user default model is still -' 'it reports no default'

# Claude Code that saves nothing: still fine.
orig_settings; ORIG="$(cat "$SETTINGS")"; touch "$ST/no-save"
lane --model plain-sw sonnet
assert_exit 0 "$LAST_RC" '--model works when /model saves nothing'
[ "$(cat "$SETTINGS")" = "$ORIG" ] || fail 'settings untouched when /model saves nothing'; ok
rm -f "$ST/no-save"

# 22 Oct: /model never lands in the transcript, so the switch is confirmed by the pane's "Set model to" line. With that line missing the
# switch is not confirmed: exit 1, the index row stays, and the user default is still put back.
orig_settings; ORIG="$(cat "$SETTINGS")"; touch "$ST/no-pane-line"
before_idx="$(awk -F'\t' '$2=="plain-sw"{m=$4} END{print m}' "$INDEX")"
LANE_SEND_CONFIRM_SECS=1 lane --allow-high-tier "model guard test" --model plain-sw fable
assert_exit 1 "$LAST_RC" '--model without a "Set model to" line on the pane is not confirmed'
assert_contains "$LAST_OUT" 'NOT CONFIRMED' 'the failure says the pane did not confirm'
assert_not_contains "$LAST_OUT" 'NOT IN ITS TRANSCRIPT' '--model no longer looks for /model in the transcript'
after_idx="$(awk -F'\t' '$2=="plain-sw"{m=$4} END{print m}' "$INDEX")"
[ "$after_idx" = "$before_idx" ] || fail "an unconfirmed switch leaves the index row, got: $after_idx"; ok
[ "$(cat "$SETTINGS")" = "$ORIG" ] || fail 'an unconfirmed switch still restores the user default'; ok
rm -f "$ST/no-pane-line"
# The pane keeps the old "Set model to" lines: only a new one counts.
lane --model plain-sw sonnet
assert_exit 0 "$LAST_RC" 'a switch is confirmed by a new "Set model to" line even when older ones are on the pane'
assert_contains "$LAST_OUT" 'confirmed on its screen' ''
lane --model plain-sw sonnet
assert_exit 0 "$LAST_RC" 'switching to the model it is already on is confirmed again'

# Refusals type nothing and touch nothing.
: >"$ST/sent.log"; orig_settings; ORIG="$(cat "$SETTINGS")"
lane --model mail-sw fable
assert_exit 1 "$LAST_RC" 'a mail lane cannot be switched to fable'
assert_contains "$LAST_OUT" 'refusing mail model fable' 'the switch refusal says why'
lane --model mail-sw opus
assert_exit 1 "$LAST_RC" 'a mail lane cannot be switched to opus'
lane --model site-sw sonnet
assert_exit 1 "$LAST_RC" 'a site-model lane cannot be switched to sonnet'
lane --model plain-sw gpt-5
assert_exit 1 "$LAST_RC" 'a non-Claude model name is refused'
lane --model nobody-sw sonnet
assert_exit 1 "$LAST_RC" 'an unknown lane is refused'
[ ! -s "$ST/sent.log" ] || fail "a refused switch typed nothing, got: $(cat "$ST/sent.log")"; ok
[ "$(cat "$SETTINGS")" = "$ORIG" ] || fail 'a refused switch leaves settings alone'; ok
lane --model mail-sw sonnet
assert_exit 0 "$LAST_RC" 'a mail lane switches to sonnet'

# A forced mail switch to opus is allowed with a reason, and logged.
lane --allow-high-tier "claim-check pass" --model mail-sw opus
assert_exit 0 "$LAST_RC" '--allow-high-tier lets a mail lane switch to opus'
[ -s "$TLOG" ] || fail 'the override is logged'; ok
assert_contains "$(cat "$TLOG")" 'mail-sw' 'the log names the lane'
assert_contains "$(cat "$TLOG")" 'opus' 'the log names the model'
assert_contains "$(cat "$TLOG")" 'claim-check pass' 'the log holds the reason'

# A settings file that will not round-trip is not touched, and nothing is typed.
: >"$ST/sent.log"; printf '{"model":"claude-opus-5-5",   "theme": "dark"}' >"$SETTINGS"; ORIG="$(cat "$SETTINGS")"
lane --model plain-sw sonnet
assert_exit 1 "$LAST_RC" '--model refuses when the user default cannot be restored safely'
[ ! -s "$ST/sent.log" ] || fail 'nothing typed when the default cannot be restored'; ok
[ "$(cat "$SETTINGS")" = "$ORIG" ] || fail 'the odd settings file is untouched'; ok

# A dead window: the send fails, the default is still intact, the index row does not move.
orig_settings; ORIG="$(cat "$SETTINGS")"
row ghost sonnet "$P_CODE"
lane --allow-high-tier "model guard test" --model ghost opus
assert_exit 1 "$LAST_RC" 'a lane with no window cannot be switched'
[ "$(cat "$SETTINGS")" = "$ORIG" ] || fail 'a failed switch leaves settings alone'; ok
g="$(awk -F'\t' '$2=="ghost"{m=$4} END{print m}' "$INDEX")"
[ "$g" = sonnet ] || fail "a failed switch leaves the index row, got: $g"; ok

# --dry-run types nothing.
: >"$ST/sent.log"
lane --dry-run --allow-high-tier "model guard test" --model plain-sw opus
assert_exit 0 "$LAST_RC" '--model --dry-run exits 0'
assert_contains "$LAST_OUT" "would type '/model opus'" 'the dry run says what it would type'
[ ! -s "$ST/sent.log" ] || fail 'the dry run typed nothing'; ok

printf 'ok - %s checks passed\n' "$checks"
