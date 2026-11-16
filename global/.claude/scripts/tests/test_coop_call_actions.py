"""Tests for coop_call_actions.py: the checker keeps only quotes that are in the transcript word for word, the tool talks only to
a local Ollama, and the whole flow (stub server, retries, skipped window, mode 600, no quote text on stdout) holds.

The test text is invented. No real call is read."""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..")
sys.path.insert(0, SCRIPTS)
import coop_call_actions as cca  # noqa: E402

TOOL = os.path.join(SCRIPTS, "coop_call_actions.py")


def test_whole_flow_selftest_passes():
    r = subprocess.run([sys.executable, "-I", TOOL, "--selftest"], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SELFTEST OK" in r.stdout


def test_normalise_ignores_case_punctuation_and_spacing():
    assert cca.normalise("  Don't   STOP, now!  ") == "don t stop now"


def test_srt_cues_and_windows_overlap_with_whole_cues():
    srt = "".join("%d\n00:00:%02d,000 --> 00:00:%02d,500\ncue number %d has some words in it\n\n" % (i + 1, i, i, i) for i in range(20))
    cues = cca.parse_srt(srt)
    assert len(cues) == 20 and cues[3]["start"] == 3.0
    wins = cca.build_windows(cues, 120, 60)
    assert wins[0][0] == 0 and wins[-1][1] == 20
    for (a, b), (c, d) in zip(wins, wins[1:]):
        assert c > a and c < b  # moves on, and overlaps the window before


def test_checker_keeps_true_drops_paraphrase_and_short():
    cues = cca.parse_srt("1\n00:00:00,000 --> 00:00:04,000\nso the backup copies get deleted on friday\n\n"
                         "2\n00:00:05,000 --> 00:00:09,000\nGreg will send the policy draft to everybody tomorrow\n\n")
    win = cca.Joined(cues)
    counts = {"bad_shape": 0, "too_short": 0, "not_in_window": 0, "not_in_transcript": 0, "duplicate": 0}
    items = [
        {"kind": "owes", "quote": "Greg will send the policy draft to everybody tomorrow."},
        {"kind": "owes", "quote": "Greg promised to send the draft to the whole team"},
        {"kind": "owes", "quote": "Greg will send"},
        {"kind": "nonsense", "quote": "Greg will send the policy draft to everybody tomorrow"},
    ]
    kept = cca.check_items(items, win, win.padded, None, counts)
    assert len(kept) == 1 and kept[0]["start"] == 5.0
    assert counts["not_in_window"] == 1 and counts["too_short"] == 1 and counts["bad_shape"] == 1


@pytest.mark.parametrize("url", ["http://192.0.2.1:11434", "https://127.0.0.1:11434", "http://127.0.0.1@evil.example", "http://example.com"])
def test_non_local_urls_are_refused(url):
    with pytest.raises(SystemExit):
        cca.check_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434"])
def test_local_urls_pass(url):
    assert cca.check_url(url) == url
