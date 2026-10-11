#!/bin/bash
# Claude Code status line.
#   HH:MM AM 🐢 user 🐺 @ host | context bar N% free (Nk) [C:n] [I:.. O:.. ~../d] | ~/dir (branch) | Model [style]
#
# Input : the status-line JSON Claude Code 2.1.x pipes to stdin (see the schema notes below).
# Output: exactly ONE line on stdout, nothing on stderr, always exit 0. A non-zero exit or an
#         empty line blanks the status line, so every failure path falls back to a default.
#
# Rewritten 2026-10-11. What was wrong with the old script (backup: statusline-command.sh.bak-2026-10-11):
#   1. Every session shared one weekly-usage.json read-modify-write through ONE shared
#      weekly-usage.json.tmp. Two concurrent sessions interleaved their writes and the file ended
#      "Extra data" (invalid JSON). jq then failed on every run, the mv never happened, and the weekly
#      figure sat frozen at "I:2.5M O:500k ~0/d" from 2026-07-25 on. Nothing ever repaired it.
#      Now: per-session state files (single writer) plus an append-only daily usage log (O_APPEND
#      writes of one short line are atomic). Nothing is read-modify-written by two processes, and every
#      replace is write-to-unique-tmp + mv, so being killed mid-run (Claude Code aborts the previous run
#      whenever a new refresh starts) can't leave a torn file.
#   2. context_window.total_input_tokens / total_output_tokens are no longer session totals: in 2.1.296
#      they are "input tokens currently in the context window" and "output tokens of the most recent
#      response". The old delta maths on them was meaningless and [C:n] could never fire.
#      Now: weekly I/O adds each NEW response's (input + cache-write) and output tokens; [C:n] counts
#      drops in context size (compactions).
#   3. Window maths assumed a 77.5% usable limit of the model window. The real auto-compact trigger is
#      (autoCompactWindow, or the model window, whichever is smaller) - 20000 summary reserve - 13000
#      buffer. With autoCompactWindow=400000 that is 367000. Read from CLAUDE_CODE_AUTO_COMPACT_WINDOW
#      or settings.json (cached; re-read only when settings.json changes).
#   4. Hardening: the single jq call now strips control characters (a directory name containing an ESC
#      sequence used to reach the terminal verbatim), no grep -oP JSON scraping, no echo -e
#      re-interpreting backslashes, no `source` of a cache file, no seq/sed/date/git forks, stderr muted.
#
# The script never opens transcript_path: its cost does not depend on session length.

exec 2>/dev/null
trap 'exit 0' PIPE TERM HUP INT

if ! command -v jq &>/dev/null; then
    echo "⚠ jq not found - install with: curl -sL https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-amd64 -o ~/.local/bin/jq && chmod +x ~/.local/bin/jq"
    exit 0
fi

# ---------- input ----------
IFS= read -r -d '' input
[ -z "$input" ] && exit 0

USER_NAME="${USER:-$(whoami)}"
HOST_NAME="${HOSTNAME:-$(hostname -s)}"
HOME_DIR="${HOME:-/home/$USER_NAME}"
USER_NAME=${USER_NAME//[[:cntrl:]]/}
HOST_NAME=${HOST_NAME//[[:cntrl:]]/}

# ONE jq call for everything. Fields are joined with 0x1f; control characters are stripped from every value.
IFS=$'\x1f' read -r model_name current_dir output_style ctx_used ctx_limit session_id resp_in resp_out < <(
    jq -r '
        def clean: tostring | gsub("[\u0000-\u001f\u007f-\u009f]"; "");
        def num: if type == "number" then floor else 0 end;
        (.context_window.current_usage // null) as $u
        | [ (.model.display_name // "Claude"),
            (.workspace.current_dir // .cwd // "unknown"),
            (.output_style.name // ""),
            ( if $u then (($u.input_tokens | num) + ($u.cache_creation_input_tokens | num) + ($u.cache_read_input_tokens | num))
              else (.context_window.total_input_tokens | num) end ),
            (.context_window.context_window_size | num),
            (.session_id // "unknown"),
            ( if $u then (($u.input_tokens | num) + ($u.cache_creation_input_tokens | num)) else 0 end ),
            ( if $u then ($u.output_tokens | num) else (.context_window.total_output_tokens | num) end )
          ] | map(clean) | join("\u001f")
    ' <<<"$input"
)
[ -n "$model_name" ] || model_name="Claude"
[ -n "$current_dir" ] || current_dir="unknown"
[[ "$session_id" =~ ^[A-Za-z0-9_-]{1,80}$ ]] || session_id="unknown"
[[ "$ctx_used" =~ ^[0-9]+$ ]] || ctx_used=0
[[ "$ctx_limit" =~ ^[0-9]+$ ]] && [ "$ctx_limit" -gt 0 ] || ctx_limit=200000
[[ "$resp_in" =~ ^[0-9]+$ ]] || resp_in=0
[[ "$resp_out" =~ ^[0-9]+$ ]] || resp_out=0

now=${EPOCHSECONDS:-$(printf '%(%s)T' -1)}

# ---------- state dirs (all under ~/.claude/.statusline) ----------
SDIR="$HOME_DIR/.claude/.statusline"
SESS_DIR="$SDIR/s"     # one small file per session; only that session writes it
USAGE_DIR="$SDIR/u"    # one append-only file per day: "session_id input output" per new response
[ -d "$SESS_DIR" ] || mkdir -p "$SESS_DIR" "$USAGE_DIR"
[ -d "$USAGE_DIR" ] || mkdir -p "$USAGE_DIR"

# ---------- auto-compact window ----------
# 1) CLAUDE_CODE_AUTO_COMPACT_WINDOW  2) autoCompactWindow in settings.json (cached until settings.json changes)
parse_window() {   # "400000" | "400k" | "0.4m"-less ints | "400" (hundreds are thousands, as Claude Code reads it)
    local v=${1,,}
    v=${v// /}
    case "$v" in
        *k) [[ "${v%k}" =~ ^[0-9]+$ ]] && echo $(( ${v%k} * 1000 )) ;;
        *m) [[ "${v%m}" =~ ^[0-9]+$ ]] && echo $(( ${v%m} * 1000000 )) ;;
        *)  if [[ "$v" =~ ^[0-9]+$ ]]; then
                if [ "$v" -ge 100 ] && [ "$v" -le 1000 ]; then echo $(( v * 1000 )); else echo "$v"; fi
            fi ;;
    esac
}
auto_win=""
if [ -n "$CLAUDE_CODE_AUTO_COMPACT_WINDOW" ]; then
    auto_win=$(parse_window "$CLAUDE_CODE_AUTO_COMPACT_WINDOW")
fi
if [ -z "$auto_win" ]; then
    SETTINGS="$HOME_DIR/.claude/settings.json"
    SETTINGS_LOCAL="$HOME_DIR/.claude/settings.local.json"
    AUTOWIN_CACHE="$SDIR/autowin"
    if [ ! -f "$AUTOWIN_CACHE" ] || [ "$SETTINGS" -nt "$AUTOWIN_CACHE" ] || [ "$SETTINGS_LOCAL" -nt "$AUTOWIN_CACHE" ]; then
        raw=""
        for f in "$SETTINGS_LOCAL" "$SETTINGS"; do      # the local file overrides the shared one
            [ -f "$f" ] || continue
            raw=$(jq -r '(.autoCompactWindow // empty) | tostring' "$f" 2>/dev/null)
            [ -n "$raw" ] && break
        done
        printf '%s\n' "$raw" > "$AUTOWIN_CACHE.$$" && mv -f "$AUTOWIN_CACHE.$$" "$AUTOWIN_CACHE"
    fi
    IFS= read -r raw < "$AUTOWIN_CACHE"
    auto_win=$(parse_window "$raw")
fi
[[ "$auto_win" =~ ^[0-9]+$ ]] || auto_win=0

# Effective window = the smaller of the model window and the auto-compact window.
# Compaction fires at window - 20000 (summary reserve) - 13000 (buffer).
eff_window=$ctx_limit
[ "$auto_win" -ge 100000 ] && [ "$auto_win" -lt "$eff_window" ] && eff_window=$auto_win
usable_limit=$(( eff_window - 33000 ))
[ "$usable_limit" -lt 10000 ] && usable_limit=$(( eff_window * 775 / 1000 ))
[ "$usable_limit" -lt 1 ] && usable_limit=1

context_free=$(( usable_limit - ctx_used ))
over=0
if [ "$context_free" -lt 0 ]; then over=$(( -context_free )); context_free=0; fi
free_pct=$(( context_free * 100 / usable_limit ))

# ---------- progress bar ----------
used_pct=$(( 100 - free_pct ))
filled=$(( used_pct / 10 ))
empty=$(( 10 - filled ))
printf -v bar '%*s' "$filled" ''; bar=${bar// /█}
printf -v pad '%*s' "$empty" '';  pad=${pad// /░}
bar+=$pad

R=$'\033[0m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BLUE=$'\033[34m'
MAGENTA=$'\033[35m'; CYAN=$'\033[36m'

if   [ "$free_pct" -gt 30 ]; then bar_color=$GREEN
elif [ "$free_pct" -gt 15 ]; then bar_color=$YELLOW
else                              bar_color=$RED
fi

if   [ "$over" -gt 0 ];            then free_display="$(( over / 1000 ))k over"
elif [ "$context_free" -gt 1000 ]; then free_display="$(( context_free / 1000 ))k"
else                                    free_display="$context_free"
fi
token_info="${bar_color}${bar}${R} ${free_pct}% free (${free_display})"

# ---------- per-session state: prev_ctx compactions sig w_in w_out w_days w_epoch start_epoch in_lim out_lim ----------
SF="$SESS_DIR/$session_id"
prev_ctx=0 compactions=0 sig="" w_in=0 w_out=0 w_days=1 w_epoch=0 start_epoch=0 in_limit=2500000 out_limit=500000
new_session=0
if [ -f "$SF" ]; then
    read -r prev_ctx compactions sig w_in w_out w_days w_epoch start_epoch in_limit out_limit < "$SF"
else
    new_session=1
fi
for v in prev_ctx compactions w_in w_out w_days w_epoch start_epoch in_limit out_limit; do
    [[ "${!v}" =~ ^[0-9]+$ ]] || printf -v "$v" '%s' 0
done
[[ "$sig" =~ ^[0-9.]+$ ]] || sig=""
[ "$sig" = "0" ] && sig=""      # "0" is how an empty signature is stored
[ "$w_days" -ge 1 ] || w_days=1
[ "$in_limit" -gt 0 ] || in_limit=2500000
[ "$out_limit" -gt 0 ] || out_limit=500000
[ "$start_epoch" -gt 0 ] || start_epoch=$now

dirty=0
[ "$new_session" = 1 ] && dirty=1
recompute=0
printf -v today '%(%Y-%m-%d)T' "$now"

if [ "$ctx_used" -gt 0 ]; then
    # compaction / clear: context fell to well under its previous size
    if [ "$prev_ctx" -ge 40000 ] && [ $(( ctx_used * 10 )) -lt $(( prev_ctx * 6 )) ]; then
        compactions=$(( compactions + 1 )); dirty=1
    fi
    [ "$ctx_used" -ne "$prev_ctx" ] && { prev_ctx=$ctx_used; dirty=1; }

    # a new API response shows up as a new (input, context, output) tuple; first sighting is not counted
    new_sig="${resp_in}.${ctx_used}.${resp_out}"
    if [ "$new_sig" != "$sig" ]; then
        if [ -n "$sig" ]; then
            [ -d "$USAGE_DIR" ] && printf '%s %s %s\n' "$session_id" "$resp_in" "$resp_out" >> "$USAGE_DIR/$today"
            recompute=1
        fi
        sig=$new_sig; dirty=1
    fi
fi
{ [ -z "$sig" ] || [ "$w_epoch" -eq 0 ]; } && recompute=1
[ $(( now - w_epoch )) -gt 120 ] && recompute=1

# ---------- weekly totals (last 7 days, all sessions) ----------
if [ "$recompute" = 1 ]; then
    printf -v cutoff '%(%Y-%m-%d)T' $(( now - 7 * 86400 ))
    files=()
    for f in "$USAGE_DIR"/????-??-??; do
        [ -f "$f" ] || continue
        if [[ "${f##*/}" < "$cutoff" ]]; then rm -f "$f"; else files+=("$f"); fi
    done
    w_in=0 w_out=0
    if [ "${#files[@]}" -gt 0 ]; then
        read -r w_in w_out < <(awk 'NF >= 3 && $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/ { i += $2; o += $3 } END { print i + 0, o + 0 }' "${files[@]}")
        w_days=${#files[@]}
    else
        w_days=1
    fi
    [[ "$w_in" =~ ^[0-9]+$ ]] || w_in=0
    [[ "$w_out" =~ ^[0-9]+$ ]] || w_out=0
    # limits (optional ~/.claude/weekly-config.json: {"input_limit":N,"output_limit":N})
    WEEKLY_CONFIG="$HOME_DIR/.claude/weekly-config.json"
    if [ -f "$WEEKLY_CONFIG" ]; then
        a="" b=""
        read -r a b < <(jq -r '[(.input_limit // 2500000), (.output_limit // 500000)] | map(floor) | @tsv' "$WEEKLY_CONFIG" 2>/dev/null)
        [[ "$a" =~ ^[0-9]+$ ]] && [ "$a" -gt 0 ] && in_limit=$a
        [[ "$b" =~ ^[0-9]+$ ]] && [ "$b" -gt 0 ] && out_limit=$b
    fi
    w_epoch=$now; dirty=1
fi

# ---------- save session state (atomic: unique tmp + mv) ----------
if [ "$dirty" = 1 ]; then
    if [ "$new_session" = 1 ] && [ -d "$SESS_DIR" ]; then
        find "$SESS_DIR" -type f -name '[A-Za-z0-9_-]*' ! -name '*.*' -mtime +8 -delete 2>/dev/null   # forget sessions idle for 8+ days
        find "$SDIR" -type f -name '*.*' -mmin +60 -delete 2>/dev/null                               # temp files left by a SIGKILL
    fi
    printf '%s %s %s %s %s %s %s %s %s %s\n' "$prev_ctx" "$compactions" "${sig:-0}" "$w_in" "$w_out" "$w_days" "$w_epoch" "$start_epoch" "$in_limit" "$out_limit" \
        > "$SF.$$" && mv -f "$SF.$$" "$SF"
fi

# ---------- compression counter ----------
if   [ "$compactions" -eq 0 ]; then compress_info=""
elif [ "$compactions" -eq 1 ]; then compress_info=" ${YELLOW}[C:1]${R}"
else                                compress_info=" ${RED}[C:$compactions]${R}"
fi

# ---------- weekly display ----------
avg_daily_output=$(( w_out / w_days ))
if   [ "$avg_daily_output" -ge 1000000 ]; then daily_display="~$(( avg_daily_output / 1000000 )).$(( avg_daily_output % 1000000 / 100000 ))M/d"
elif [ "$avg_daily_output" -ge 1000 ];    then daily_display="~$(( avg_daily_output / 1000 ))k/d"
else                                           daily_display="~${avg_daily_output}/d"
fi
input_remaining=$(( in_limit - w_in ));   [ "$input_remaining" -lt 0 ]  && input_remaining=0
output_remaining=$(( out_limit - w_out )); [ "$output_remaining" -lt 0 ] && output_remaining=0
input_pct_left=$(( input_remaining * 100 / in_limit ))
output_pct_left=$(( output_remaining * 100 / out_limit ))
if [ "$input_remaining" -ge 1000000 ]; then input_display="$(( input_remaining / 1000000 )).$(( input_remaining % 1000000 / 100000 ))M"
else input_display="$(( input_remaining / 1000 ))k"; fi
if [ "$output_remaining" -ge 1000000 ]; then output_display="$(( output_remaining / 1000000 )).$(( output_remaining % 1000000 / 100000 ))M"
else output_display="$(( output_remaining / 1000 ))k"; fi
min_pct=$input_pct_left
[ "$output_pct_left" -lt "$min_pct" ] && min_pct=$output_pct_left
if   [ "$min_pct" -gt 30 ]; then weekly_color=$GREEN
elif [ "$min_pct" -gt 10 ]; then weekly_color=$YELLOW
else                             weekly_color=$RED
fi
weekly_info=" ${weekly_color}[I:${input_display} O:${output_display} ${daily_display}]${R}"

# ---------- clock: the session's first-render time (kept per session, as before) ----------
printf -v local_time '%(%I:%M %p)T' "$start_epoch"

# ---------- directory in ~ notation ----------
display_dir=$current_dir
if [ "$current_dir" = "$HOME_DIR" ]; then display_dir="~"
elif [[ "$current_dir" == "$HOME_DIR"/* ]]; then display_dir="~${current_dir#"$HOME_DIR"}"
fi

# ---------- git branch: read .git/HEAD directly (no git process, no timeout flakiness) ----------
git_branch=""
gitdir=""
d=$current_dir
for _ in {1..40}; do
    [[ "$d" == /* ]] || break
    if [ -d "$d/.git" ]; then gitdir="$d/.git"; break
    elif [ -f "$d/.git" ]; then              # worktree / submodule: ".git" is a file "gitdir: <path>"
        IFS= read -r line < "$d/.git"
        gd=${line#gitdir: }
        [[ "$gd" == /* ]] || gd="$d/$gd"
        gitdir=$gd; break
    fi
    [ "$d" = "/" ] && break
    d=${d%/*}; [ -n "$d" ] || d=/
done
if [ -n "$gitdir" ] && [ -f "$gitdir/HEAD" ]; then
    IFS= read -r head < "$gitdir/HEAD"
    case "$head" in
        "ref: refs/heads/"*) branch_name=${head#ref: refs/heads/}; branch_name=${branch_name//[[:cntrl:]]/}
                             [ -n "$branch_name" ] && git_branch=" ${YELLOW}(${branch_name})${R}" ;;
    esac
fi

# ---------- docker indicator ----------
docker_icon=""
if [ -f /.dockerenv ]; then docker_icon=" ${MAGENTA}🐳${R}"
elif [ -r /proc/1/cgroup ]; then
    cg=$(< /proc/1/cgroup)
    [[ "$cg" =~ /docker|/lxc ]] && docker_icon=" ${MAGENTA}🐳${R}"
fi

# ---------- output style ----------
style_info=""
[ -n "$output_style" ] && [ "$output_style" != "default" ] && style_info=" [${CYAN}${output_style}${R}]"

printf '%s\n' "${YELLOW}${local_time}${R} 🐢 ${USER_NAME} 🐺 @ ${BLUE}${HOST_NAME}${R}${docker_icon} | ${token_info}${compress_info}${weekly_info} | ${CYAN}${display_dir}${R}${git_branch} | ${MAGENTA}${model_name}${R}${style_info}"
exit 0
