#!/usr/bin/env bash
# The body of a local lane (lane.sh --engine local): OpenCode (opencode.ai), headless, able to edit files AND run commands, on a model served by
# Ollama on this machine. Nothing leaves the machine: no prompt, file or key goes to a hosted API, and there is no quota to run out of.
#
#   local_lane.sh <dir> <hand-off-folder> <model> <prompt-file>
#
# <model> is ollama/<tag> (ollama/qwen3-coder:30b); lane.sh passes its default when the lane was given a Claude alias.
# The server is LOCAL_LANE_URL (default http://127.0.0.1:11434, the port the openclaw-local compose file publishes). The server's context
# length must be large enough for OpenCode's own prompt plus the task: start it with OLLAMA_CONTEXT_LENGTH=32768 or more.
#
# It never falls back to a hosted model. With no server, or the model not pulled, the run stops before OpenCode starts and out.log says
# "local: ..." (exit 69), so a lane meant to keep data on this machine cannot quietly send it elsewhere.
#
# Permissions are the same as opencode_lane.sh: everything allowed except paths outside the checkout; the lane folder and this repo's own
# worktrees (~/repos/.worktrees/<repo>--*, <repo>-wf-*) are the outside paths it may use. The config is written to the lane folder
# (OPENCODE_CONFIG), never into the repo. A wall-clock limit (LOCAL_LANE_TIMEOUT seconds, default 7200) stops a run that loops.
set -u

[ $# -eq 4 ] || { echo "usage: local_lane.sh <dir> <hand-off-folder> <model> <prompt-file>" >&2; exit 2; }
dir="$1"; hand="$2"; want="$3"; prompt_file="$4"

LIMIT="${LOCAL_LANE_TIMEOUT:-7200}"
URL="${LOCAL_LANE_URL:-http://127.0.0.1:11434}"
URL="${URL%/}"
# Strict shape only: scheme, one of three loopback names, optional port. A glob would let http://127.0.0.1:x@other-host through (userinfo).
[[ "$URL" =~ ^http://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]{1,5})?$ ]] || { echo "local: refusing LOCAL_LANE_URL $URL: a local lane talks only to this machine (127.0.0.1, localhost or ::1), written http://host[:port] with nothing after the port" >&2; exit 69; }
tag="${want#ollama/}"
[ -n "$tag" ] && [ "$tag" != "$want" ] || { echo "local: model must be ollama/<tag>, got $want" >&2; exit 2; }

# nvm's node is not always on PATH in a bare tmux window.
command -v opencode >/dev/null 2>&1 || for d in "$HOME"/.nvm/versions/node/*/bin; do [ -x "$d/opencode" ] && PATH="$d:$PATH"; done
command -v opencode >/dev/null 2>&1 || { echo "local: opencode is not installed (npm install -g opencode-ai)" >&2; exit 127; }

# The server must answer and hold the model, or nothing starts.
tags="$(curl -fsS --max-time 10 "$URL/api/tags" 2>/dev/null)" || { echo "local: no Ollama server answers at $URL (start it: docker compose -p openclaw-local up -d, or set LOCAL_LANE_URL)" >&2; exit 69; }
printf '%s' "$tags" | python3 -I -c 'import json,sys
want=sys.argv[1]
names={m.get("name") for m in json.load(sys.stdin).get("models",[])}
sys.exit(0 if want in names or want+":latest" in names else 1)' "$tag" || { echo "local: model $tag is not on the server at $URL (ollama pull $tag)" >&2; exit 69; }

repo_name="$(basename -- "$dir")"
# A lane started inside a linked worktree (<repo>--<topic>) names the repo, not the worktree: the rule below lets the lane use that repo's
# OTHER worktrees, and a rule built from the worktree's own name (<repo>--<topic>--*) matches none of them (issue 285).
_common="$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || _common=""
[ "$(basename -- "$_common")" = .git ] && repo_name="$(basename -- "$(dirname -- "$_common")")"
mkdir -p -- "$hand"
conf="$hand/opencode.json"
python3 -I - "$conf" "$hand" "$HOME" "$repo_name" "$URL" "$tag" <<'PY' || exit 2
import json, sys
conf, hand, home, repo, url, tag = sys.argv[1:7]
cfg = {
    "$schema": "https://opencode.ai/config.json", "autoupdate": False, "share": "disabled",
    "enabled_providers": ["ollama"],  # no hosted provider is even listed
    "provider": {"ollama": {"npm": "@ai-sdk/openai-compatible", "name": "Ollama (this machine)",
                            "options": {"baseURL": url + "/v1"}, "models": {tag: {"name": tag}}}},
    "permission": {"*": "allow", "external_directory": {
        "*": "deny", f"{hand}/**": "allow",
        f"{home}/repos/.worktrees/{repo}--*/**": "allow", f"{home}/repos/.worktrees/{repo}-wf-*/**": "allow"}},
}
json.dump(cfg, open(conf, "w"))
PY
export OPENCODE_CONFIG="$conf"

prompt="$(cat -- "$prompt_file")" || exit 2
cd -- "$dir" || exit 2
echo "local: model ollama/$tag on $URL"
timeout "$LIMIT" opencode run --model "ollama/$tag" --auto --dir "$dir" "$prompt"
exit $?
