"""Tests for fetch_captions.py: duration and id parsing, the readiness gate, the three files (mode 600 in a 0700 folder),
and that stdout never holds caption text. A fake server stands in for the transcript server. Test text is invented."""
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..")
sys.path.insert(0, SCRIPTS)
import fetch_captions as fc  # noqa: E402

TOOL = os.path.join(SCRIPTS, "fetch_captions.py")
FAKE = os.path.join(HERE, "fake_transcript_server.py")
VID = "abcDEF12345"
CAPTION_WORDS = ["lantern", "orchard", "pebble", "harbor", "violet", "quarry", "meadow", "copper", "thistle", "ember"]


@pytest.fixture
def tmp_path():  # pytest's own tmp_path leaves a pytest-of-<user> folder, which test_no_tmp_leak refuses
    with tempfile.TemporaryDirectory(prefix="fc-test-") as d:
        yield Path(d)


def run(args, **env):
    e = dict(os.environ)
    for k in list(e):
        if k.startswith("FAKE_"):
            del e[k]
    e["FETCH_CAPTIONS_SERVER_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(FAKE))
    e.update(env)
    return subprocess.run([sys.executable, "-I", TOOL] + args, capture_output=True, text=True, env=e, timeout=60)


def fetch(folder, *extra, **env):
    return run(["fetch", "--video", VID, "--dir", str(folder)] + list(extra), **env)


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.mark.parametrize("text,want", [
    ("19 seconds", 19), ("1 hour, 2 minutes, 3 seconds", 3723), ("2 hours", 7200), ("1 minute", 60),
    ("1:02:03", 3723), ("12:30", 750), ("junk", None), ("", None), ("3 fortnights", None), (None, None)])
def test_parse_duration(text, want):
    assert fc.parse_duration(text) == want


@pytest.mark.parametrize("text", [VID, "https://www.youtube.com/watch?v=" + VID, "https://youtu.be/" + VID,
                                  "https://www.youtube.com/watch?feature=x&v=" + VID])
def test_video_id_forms(text):
    assert fc.video_id(text) == VID


def test_video_id_rejects_junk():
    assert fc.video_id("not an id") is None and fc.video_id("") is None


def test_full_fetch_over_two_pages(tmp_path):
    folder = tmp_path / "call"
    r = fetch(folder, "--date", "2099-01-02", "--label", "test copy")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "page 0 items 6 more True" in r.stdout and "page 1 items 4 more False" in r.stdout
    assert "cues 10 words 50 first 00:00:00 last 00:00:45 last_end 00:00:50 video_length 00:00:50" in r.stdout
    assert mode(folder) == 0o700
    for n in ("transcript.md", "transcript.srt", "transcript.json"):
        assert mode(folder / n) == 0o600
        assert n in r.stdout
    srt = (folder / "transcript.srt").read_text()
    assert srt.startswith("1\n00:00:00,000 --> 00:00:05,000\ninvented lantern line number 0\n")
    assert len(json.loads((folder / "transcript.json").read_text())) == 10
    md = (folder / "transcript.md").read_text()
    assert "https://youtu.be/%s" % VID in md and "test copy" in md and "Date: 2099-01-02" in md
    assert "Sensitive: call data. Local model only." in md and "Duration: 00:00:50" in md
    assert "[00:00:05] invented orchard line number 1" in md
    assert fc.video_id_from_folder(str(folder)) == VID
    assert fc.video_id_from_folder(str(tmp_path)) is None


def test_ms_offsets_are_converted(tmp_path):
    r = fetch(tmp_path / "c", FAKE_MS="1")
    assert r.returncode == 0, r.stdout
    assert "last 00:00:45 last_end 00:00:50" in r.stdout
    assert "00:00:45,000 --> 00:00:50,000" in (tmp_path / "c" / "transcript.srt").read_text()


def test_stdout_and_stderr_hold_counts_only(tmp_path):
    for r in (fetch(tmp_path / "a"), fetch(tmp_path / "b", FAKE_TRUNCATE="1"), run(["probe"]),
              run(["info", "--video", VID]), fetch(tmp_path / "c", FAKE_ERROR="1")):
        for w in CAPTION_WORDS + ["Invented Title", "Invented description"]:
            assert w not in r.stdout + r.stderr


def test_not_ready_on_truncated_captions_writes_nothing(tmp_path):
    folder = tmp_path / "call"
    r = fetch(folder, FAKE_TRUNCATE="1")
    assert r.returncode == 4
    assert r.stdout.strip().splitlines()[-1].startswith(
        "NOT-READY last_caption 00:00:15 video_length 01:02:03 short_by 3708s")
    assert not folder.exists()
    assert fetch(folder, "--tolerance", "4000", FAKE_TRUNCATE="1").returncode == 0


def test_force_fetches_anyway(tmp_path):
    folder = tmp_path / "call"
    r = fetch(folder, "--force", FAKE_TRUNCATE="1")
    assert r.returncode == 0 and "NOT-READY" not in r.stdout
    assert (folder / "transcript.srt").exists()


@pytest.mark.parametrize("env", [{"FAKE_DURATION": "none"}, {"FAKE_DURATION": "soon"}])
def test_unreadable_duration_skips_gate(tmp_path, env):
    folder = tmp_path / "call"
    r = fetch(folder, FAKE_TRUNCATE="1", **env)
    assert r.returncode == 0, r.stdout
    assert "gate skipped: video length unreadable" in r.stdout and "video_length unknown" in r.stdout
    assert "Duration: unknown" in (folder / "transcript.md").read_text()


def test_ts_carries_rounding_into_the_second(tmp_path):
    assert fc.ts(7.999999999999999, True) == "00:00:08,000"
    assert fc.ts(3723.5, True) == "01:02:03,500" and fc.ts(59.9996) == "00:01:00"


def test_srt_end_time_never_prints_1000_ms():
    files, _ = fc.build_files([{"start": 0.0, "end": 7.999999999999999, "text": "a b"}], VID, None, None, None)
    assert "00:00:00,000 --> 00:00:08,000" in files["transcript.srt"]


def test_no_info_reply_exits_2_and_writes_nothing(tmp_path):
    folder = tmp_path / "call"
    r = fetch(folder, FAKE_NO_INFO="1")
    assert r.returncode == 2 and "no reply from get_video_info, rc" in r.stdout
    assert not folder.exists()
    assert run(["info", "--video", VID], FAKE_NO_INFO="1").returncode == 2


@pytest.mark.parametrize("name", ["transcript.md", "transcript.json"])
def test_any_existing_transcript_file_skips_fetch(tmp_path, name):
    folder = tmp_path / "call"
    folder.mkdir()
    (folder / name).write_text("keep\n")
    r = fetch(folder)
    assert r.returncode == 0 and "fetch skipped: %s exists" % name in r.stdout
    assert [p.name for p in folder.iterdir()] == [name] and (folder / name).read_text() == "keep\n"


def test_existing_srt_is_kept_until_refetch(tmp_path):
    folder = tmp_path / "call"
    assert fetch(folder).returncode == 0
    before = {n: (folder / n).read_bytes() for n in ("transcript.md", "transcript.srt", "transcript.json")}
    r = fetch(folder, FAKE_MS="1")
    assert r.returncode == 0 and "fetch skipped: transcript.srt exists, 10 cues" in r.stdout
    assert before == {n: (folder / n).read_bytes() for n in before}
    (folder / "transcript.srt").write_text("old\n")
    r = fetch(folder, "--refetch")
    assert r.returncode == 0 and "fetch skipped" not in r.stdout
    assert (folder / "transcript.srt").read_bytes() == before["transcript.srt"]


def test_is_error_reply_exits_3(tmp_path):
    r = fetch(tmp_path / "call", FAKE_ERROR="1")
    assert r.returncode == 3 and not (tmp_path / "call").exists()


def test_no_reply_exits_2(tmp_path):
    r = fetch(tmp_path / "call", FETCH_CAPTIONS_SERVER_CMD="true")
    assert r.returncode == 2 and not (tmp_path / "call").exists()


def test_bad_arguments_exit_1(tmp_path):
    assert run(["fetch", "--video", "nope", "--dir", str(tmp_path / "x")]).returncode == 1
    assert run(["fetch"]).returncode == 1


def test_probe_lists_names_only():
    r = run(["probe"])
    assert r.returncode == 0
    assert r.stdout.split() == ["get_video_info", "get_transcript", "get_timed_transcript", "get_video_info_extra"]


def test_info_prints_numbers_only():
    assert run(["info", "--video", VID], FAKE_DURATION="1 hour, 2 minutes, 3 seconds").stdout.strip() == "duration_seconds 3723"
    assert run(["info", "--video", VID], FAKE_DURATION="none").stdout.strip() == "duration unreadable"
