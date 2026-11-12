#!/usr/bin/env bash
# ps_redacted.sh: run a process listing with secrets masked in the output (wrapper for redact_process.py); use it instead of plain ps.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 "$SCRIPT_DIR/redact_process.py" "$@"
