"""muse_poll.sh runs the daily hub inbox archive (issue 29) after S and LOG are set.

The script runs under `set -u`, so a block that reads $S or $LOG before their assignment stops the whole poll.
This checks the order in the file itself; it never runs the poller.
"""
import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "muse_poll.sh"


def _line_of(lines, pattern):
    hits = [i for i, line in enumerate(lines) if re.search(pattern, line)]
    assert len(hits) == 1, f"expected exactly one line matching {pattern!r}, found {len(hits)}"
    return hits[0]


def test_archive_block_runs_after_S_and_LOG_are_set():
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    s_set = _line_of(lines, r'^S=\$HOME/\.claude/scripts$')
    log_set = _line_of(lines, r'^LOG=\$HOME/\.claude/state/muse-poll\.log$')
    archive_call = _line_of(lines, r'hub_inbox_archive\.py" --days 7')
    assert s_set < archive_call, "the archive call reads $S before it is set"
    assert log_set < archive_call, "the archive call writes to $LOG before it is set"


def test_archive_runs_once_a_day_with_seven_days():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'ARCHIVE_LAST=$STATE/muse-poll-archive-last.stamp' in text
    assert '>= 86400 - 15' in text
    assert '--days 7' in text
