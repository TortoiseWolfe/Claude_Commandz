#!/usr/bin/env bash
# Set up the private Python venv that coop_scrubber_presidio.py runs in: Presidio plus spaCy's small English model.
# Lives outside every repo (~/.local/share/coop-scrubber/venv). Safe to run again; each step is capped by timeout.
#   coop_scrubber_install_presidio.sh            install or repair
#   coop_scrubber_install_presidio.sh --check    exit 0 when the venv can import Presidio and load the model
set -euo pipefail
VENV="${COOP_SCRUBBER_VENV:-$HOME/.local/share/coop-scrubber/venv}"
if [ "${1:-}" = "--check" ]; then
  timeout 120 "$VENV/bin/python" -c "import presidio_analyzer, spacy; spacy.load('en_core_web_sm')" 2>/dev/null
  exit $?
fi
[ -x "$VENV/bin/python" ] || { mkdir -p "$(dirname "$VENV")"; python3 -m venv "$VENV"; }
timeout 600 "$VENV/bin/python" -m pip install --quiet --upgrade pip
timeout 900 "$VENV/bin/python" -m pip install --quiet presidio-analyzer spacy
timeout 900 "$VENV/bin/python" -m spacy download en_core_web_sm --quiet
timeout 120 "$VENV/bin/python" -c "import presidio_analyzer, spacy; spacy.load('en_core_web_sm')"
echo "coop scrubber: Presidio venv ready at $VENV"
