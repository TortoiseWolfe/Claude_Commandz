#!/bin/bash

# Exit quickly if interrupted
trap 'exit 0' SIGPIPE

# Cache static values
USER_NAME="${USER:-$(whoami)}"
HOST_NAME="${HOSTNAME:-$(hostname -s)}"

# Read JSON input from stdin
input=$(cat)

# Early exit if no input
[ -z "$input" ] && exit 0

# Extract basic data using bash regex (no jq needed for these)
model_name=$(echo "$input" | grep -oP '"display_name"\s*:\s*"\K[^"]+' || echo "Claude")
current_dir=$(echo "$input" | grep -oP '"current_dir"\s*:\s*"\K[^"]+')
[ -z "$current_dir" ] && current_dir=$(echo "$input" | grep -oP '"cwd"\s*:\s*"\K[^"]+' || echo "unknown")
output_style=$(echo "$input" | grep -oP '"output_style"\s*:\s*\{[^}]*"name"\s*:\s*"\K[^"]+' || echo "")

# SINGLE jq call for all numeric values from stdin (was 7 separate calls)
read -r cache_read cache_create input_tokens context_limit session_id session_input session_output < <(
    echo "$input" | jq -r '[
        (.context_window.current_usage.cache_read_input_tokens // 0),
        (.context_window.current_usage.cache_creation_input_tokens // 0),
        (.context_window.current_usage.input_tokens // 0),
        (.context_window.context_window_size // 200000),
        (.session_id // "unknown"),
        (.context_window.total_input_tokens // 0),
        (.context_window.total_output_tokens // 0)
    ] | @tsv' 2>/dev/null
)

# Validate numeric values
[[ "$cache_read" =~ ^[0-9]+$ ]] || cache_read=0
[[ "$cache_create" =~ ^[0-9]+$ ]] || cache_create=0
[[ "$input_tokens" =~ ^[0-9]+$ ]] || input_tokens=0
[[ "$context_limit" =~ ^[0-9]+$ ]] || context_limit=200000
[[ "$session_input" =~ ^[0-9]+$ ]] || session_input=0
[[ "$session_output" =~ ^[0-9]+$ ]] || session_output=0

# Calculate context usage
context_used=$((cache_read + cache_create + input_tokens))
usable_limit=$((context_limit * 775 / 1000))
context_free=$((usable_limit - context_used))
[ "$context_free" -lt 0 ] && context_free=0

# Calculate free percentage
if [ "$usable_limit" -gt 0 ]; then
    free_pct=$((context_free * 100 / usable_limit))
else
    free_pct=0
fi

# Build progress bar
used_pct=$((100 - free_pct))
filled=$((used_pct / 10))
empty=$((10 - filled))
bar=""
for i in $(seq 1 $filled 2>/dev/null); do bar+="█"; done
for i in $(seq 1 $empty 2>/dev/null); do bar+="░"; done

# Color based on remaining space
if [ "$free_pct" -gt 30 ]; then
    bar_color="\033[32m"
elif [ "$free_pct" -gt 15 ]; then
    bar_color="\033[33m"
else
    bar_color="\033[31m"
fi

# Format free space
if [ "$context_free" -gt 1000 ]; then
    free_display="$((context_free / 1000))k"
else
    free_display="$context_free"
fi

token_info="${bar_color}${bar}\033[0m ${free_pct}% free (${free_display})"

# Weekly tracking files
WEEKLY_STATE=~/.claude/weekly-usage.json
WEEKLY_CONFIG=~/.claude/weekly-config.json
WEEKLY_CACHE=~/.claude/.weekly-cache

# Read cached weekly info (fast path - no jq needed)
cached_weekly_info=""
cached_compress_info=""
cached_prev_input=0
cached_prev_output=0
cached_session_id=""
if [ -f "$WEEKLY_CACHE" ]; then
    source "$WEEKLY_CACHE" 2>/dev/null
fi

# Calculate delta to see if we need to update
delta_input=$((session_input - cached_prev_input))
delta_output=$((session_output - cached_prev_output))

# Only do expensive operations if tokens changed OR session changed
if [ "$delta_input" -gt 0 ] || [ "$delta_output" -gt 0 ] || [ -z "$cached_weekly_info" ] || [ "$session_id" != "$cached_session_id" ]; then

    # Initialize state file if missing or old format
    if [ ! -f "$WEEKLY_STATE" ] || grep -q '"usage"' "$WEEKLY_STATE" 2>/dev/null; then
        echo '{"daily":{},"sessions":{}}' > "$WEEKLY_STATE"
    fi

    # Read config
    if [ -f "$WEEKLY_CONFIG" ]; then
        read -r input_limit output_limit < <(
            jq -r '[(.input_limit // 2500000), (.output_limit // 500000)] | @tsv' "$WEEKLY_CONFIG" 2>/dev/null
        )
    fi
    [[ "$input_limit" =~ ^[0-9]+$ ]] || input_limit=2500000
    [[ "$output_limit" =~ ^[0-9]+$ ]] || output_limit=500000

    today=$(date +%Y-%m-%d)
    cutoff_date=$(date -d "7 days ago" +%Y-%m-%d 2>/dev/null || date -v-7d +%Y-%m-%d 2>/dev/null || echo "1970-01-01")

    # Read state: session baseline, prev values, and weekly totals from daily aggregates
    read -r baseline prev_input prev_output weekly_input weekly_output days_with_data < <(
        jq -r --arg sid "$session_id" --arg cutoff "$cutoff_date" '
            [
                (.sessions[$sid].baseline // 0),
                (.sessions[$sid].input // 0),
                (.sessions[$sid].output // 0),
                ([.daily | to_entries[] | select(.key >= $cutoff) | .value.input] | add // 0),
                ([.daily | to_entries[] | select(.key >= $cutoff) | .value.output] | add // 0),
                ([.daily | to_entries[] | select(.key >= $cutoff)] | length)
            ] | @tsv
        ' "$WEEKLY_STATE" 2>/dev/null
    )

    # Validate
    [[ "$baseline" =~ ^[0-9]+$ ]] || baseline=0
    [[ "$prev_input" =~ ^[0-9]+$ ]] || prev_input=0
    [[ "$prev_output" =~ ^[0-9]+$ ]] || prev_output=0
    [[ "$weekly_input" =~ ^[0-9]+$ ]] || weekly_input=0
    [[ "$weekly_output" =~ ^[0-9]+$ ]] || weekly_output=0
    [[ "$days_with_data" =~ ^[0-9]+$ ]] || days_with_data=1
    [ "$days_with_data" -eq 0 ] && days_with_data=1

    # Handle new session: set baseline BEFORE calculating delta
    # This prevents recording cumulative as delta
    is_new_session=false
    if [ "$baseline" -eq 0 ] && [ "$session_input" -gt 0 ]; then
        baseline=$session_input
        is_new_session=true
    fi

    # Recalculate delta with actual prev values from state file
    delta_input=$((session_input - prev_input))
    delta_output=$((session_output - prev_output))

    # For new sessions, don't count initial cumulative as delta
    if [ "$is_new_session" = true ]; then
        delta_input=0
        delta_output=0
    fi

    # Prevent negative deltas (can happen on session restart)
    [ "$delta_input" -lt 0 ] && delta_input=0
    [ "$delta_output" -lt 0 ] && delta_output=0

    # Only write if there's actual new usage
    if [ "$delta_input" -gt 0 ] || [ "$delta_output" -gt 0 ] || [ "$is_new_session" = true ]; then
        jq --arg today "$today" \
           --arg cutoff "$cutoff_date" \
           --argjson dinput "$delta_input" \
           --argjson doutput "$delta_output" \
           --arg sid "$session_id" \
           --argjson sinput "$session_input" \
           --argjson soutput "$session_output" \
           --argjson base "$baseline" \
           '
           # Prune old daily entries
           .daily = (.daily | to_entries | map(select(.key >= $cutoff)) | from_entries)
           # Update today (add to existing or create new)
           | .daily[$today].input = ((.daily[$today].input // 0) + $dinput)
           | .daily[$today].output = ((.daily[$today].output // 0) + $doutput)
           # Update session tracking
           | .sessions[$sid] = {input: $sinput, output: $soutput, baseline: $base}
           # Prune old sessions (keep only those active in last 7 days)
           | .sessions = (.sessions | to_entries | map(select(.value.input > 0)) | from_entries)
           ' "$WEEKLY_STATE" > "$WEEKLY_STATE.tmp" 2>/dev/null && mv "$WEEKLY_STATE.tmp" "$WEEKLY_STATE"

        # Update weekly totals after write
        weekly_input=$((weekly_input + delta_input))
        weekly_output=$((weekly_output + delta_output))
    fi

    # Calculate compression counter
    tokens_since_start=$((session_input - baseline))
    [ "$tokens_since_start" -lt 0 ] && tokens_since_start=0
    compress_count=$((tokens_since_start / usable_limit))

    if [ "$compress_count" -eq 0 ]; then
        compress_info=""
    elif [ "$compress_count" -eq 1 ]; then
        compress_info=" \033[33m[C:1]\033[0m"
    else
        compress_info=" \033[31m[C:$compress_count]\033[0m"
    fi

    # Calculate daily average
    avg_daily_output=$((weekly_output / days_with_data))

    # Format daily average
    if [ "$avg_daily_output" -ge 1000000 ]; then
        daily_display="~$((avg_daily_output / 1000000)).$((avg_daily_output % 1000000 / 100000))M/d"
    elif [ "$avg_daily_output" -ge 1000 ]; then
        daily_display="~$((avg_daily_output / 1000))k/d"
    else
        daily_display="~${avg_daily_output}/d"
    fi

    # Calculate remaining tokens
    input_remaining=$((input_limit - weekly_input))
    output_remaining=$((output_limit - weekly_output))
    [ "$input_remaining" -lt 0 ] && input_remaining=0
    [ "$output_remaining" -lt 0 ] && output_remaining=0

    # Calculate remaining percentages
    input_pct_left=$((input_remaining * 100 / input_limit))
    output_pct_left=$((output_remaining * 100 / output_limit))

    # Format remaining display
    if [ "$input_remaining" -ge 1000000 ]; then
        input_display="$((input_remaining / 1000000)).$((input_remaining % 1000000 / 100000))M"
    else
        input_display="$((input_remaining / 1000))k"
    fi
    if [ "$output_remaining" -ge 1000000 ]; then
        output_display="$((output_remaining / 1000000)).$((output_remaining % 1000000 / 100000))M"
    else
        output_display="$((output_remaining / 1000))k"
    fi

    # Color based on lowest remaining percentage
    min_pct=$input_pct_left
    [ "$output_pct_left" -lt "$min_pct" ] && min_pct=$output_pct_left
    if [ "$min_pct" -gt 30 ]; then
        weekly_color="\033[32m"
    elif [ "$min_pct" -gt 10 ]; then
        weekly_color="\033[33m"
    else
        weekly_color="\033[31m"
    fi

    weekly_info=" ${weekly_color}[I:${input_display} O:${output_display} ${daily_display}]\033[0m"

    # Cache the computed values for next time (fast path)
    cat > "$WEEKLY_CACHE" << EOF
cached_weekly_info='$weekly_info'
cached_compress_info='$compress_info'
cached_prev_input=$session_input
cached_prev_output=$session_output
cached_session_id='$session_id'
EOF
else
    # Use cached values (no jq calls needed!)
    weekly_info="$cached_weekly_info"
    compress_info="$cached_compress_info"
fi

# Convert path to tilde notation
home_dir="/home/${USER_NAME}"
display_dir=$(echo "$current_dir" | sed "s|^$home_dir|~|")

# Cache time per session (PID stored in env by Claude Code)
TIME_CACHE=~/.claude/.time-cache
if [ -f "$TIME_CACHE" ]; then
    read -r cached_time cached_session < "$TIME_CACHE"
    if [ "$cached_session" = "$session_id" ]; then
        local_time="$cached_time"
    else
        local_time=$(date +"%I:%M %p")
        echo "$local_time $session_id" > "$TIME_CACHE"
    fi
else
    local_time=$(date +"%I:%M %p")
    echo "$local_time $session_id" > "$TIME_CACHE"
fi

# Get git branch with timeout
git_branch=""
if [ -d "$current_dir/.git" ] || timeout 0.1 git -C "$current_dir" rev-parse --git-dir >/dev/null 2>&1; then
    branch_name=$(timeout 0.1 git -C "$current_dir" branch --show-current 2>/dev/null)
    [ -n "$branch_name" ] && git_branch=" \033[33m($branch_name)\033[0m"
fi

# Docker indicator
docker_icon=""
grep -qE '/docker|/lxc' /proc/1/cgroup 2>/dev/null && docker_icon=" \033[35m🐳\033[0m"

# Output style indicator
style_info=""
[ -n "$output_style" ] && [ "$output_style" != "default" ] && style_info=" [\033[36m$output_style\033[0m]"

# Output the statusline
echo -e "\033[33m${local_time}\033[0m 🐢 ${USER_NAME} 🐺 @ \033[34m${HOST_NAME}\033[0m${docker_icon} | ${token_info}${compress_info}${weekly_info} | \033[36m${display_dir}\033[0m${git_branch} | \033[35m${model_name}\033[0m${style_info}"
