#!/usr/bin/env bash
# Host wrapper for the private meeting transcriber.
#   run.sh INPUT --client NAME --title SLUG [--speakers "A,B"] [--model M] [--language L]
#   run.sh --download-model NAME     (the ONLY mode with network access)
# MEETINGS_ROOT overrides ~/meetings (used by tests; same refusals apply).
set -euo pipefail
umask 077
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="meeting-transcribe:latest"
VOLUME="meeting-whisper-models"
ROOT="${MEETINGS_ROOT:-$HOME/meetings}"

die() { echo "run.sh: $*" >&2; exit 1; }

ensure_image() {
  docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" "$HERE" >&2
}

# Refuse a path under ~/repos or inside any git work tree.
check_out_path() {
  local p; p="$(realpath -m "$1")"
  case "$p/" in
    "$HOME"/repos/*) die "refusing output path under ~/repos: $p" ;;
  esac
  local probe="$p"
  while [ ! -d "$probe" ]; do probe="$(dirname "$probe")"; done
  if git -C "$probe" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "refusing output path inside a git work tree: $p"
  fi
}

if [ "${1:-}" = "--download-model" ]; then
  [ -n "${2:-}" ] || die "--download-model needs a model name"
  ensure_image
  docker volume create "$VOLUME" >/dev/null
  docker run --rm --gpus all -e HF_HOME=/models -v "$VOLUME":/models \
    --entrypoint python3 "$IMAGE" -c \
    'import sys; from faster_whisper.utils import download_model as d; print(d(sys.argv[1]))' "$2"
  exit 0
fi

INPUT="${1:-}"; [ -n "$INPUT" ] || die "usage: run.sh INPUT --client NAME --title SLUG [--speakers ...] [--model ...]"
shift
CLIENT=""; TITLE=""; SPEAKERS=""; MODEL="large-v3"; LANG_ARG=()
while [ $# -gt 0 ]; do
  case "$1" in
    --client) CLIENT="${2:?}"; shift 2 ;;
    --title) TITLE="${2:?}"; shift 2 ;;
    --speakers) SPEAKERS="${2:?}"; shift 2 ;;
    --model) MODEL="${2:?}"; shift 2 ;;
    --language) LANG_ARG=(--language "${2:?}"); shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$CLIENT" ] && [ -n "$TITLE" ] || die "--client and --title are required"
case "$CLIENT$TITLE" in */*|*..*) die "client/title must not contain / or .." ;; esac

OUT="$ROOT/$CLIENT/$(date +%F)-$TITLE"
check_out_path "$OUT"
[ -f "$INPUT" ] || die "input not found: $INPUT"

mkdir -p "$OUT"; chmod 700 "$ROOT" "$ROOT/$CLIENT" "$OUT" 2>/dev/null || true
ensure_image
IN_DIR="$(dirname "$(realpath "$INPUT")")"
IN_BASE="$(basename "$INPUT")"

docker run --rm --gpus all --network none \
  --user "$(id -u):$(id -g)" -e HF_HUB_OFFLINE=1 \
  -v "$IN_DIR":/input:ro -v "$OUT":/out -v "$VOLUME":/models:ro \
  "$IMAGE" "/input/$IN_BASE" --out /out --speakers "$SPEAKERS" \
  --model "$MODEL" ${LANG_ARG[@]+"${LANG_ARG[@]}"}
echo "Transcript written to: $OUT"
