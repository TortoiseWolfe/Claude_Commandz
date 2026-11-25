"""Tests for coop_call_transcript.py: the one command runs fetch, clean, absorb in order, prints counts only
(a stray line from a sibling tool never reaches stdout), stops on a blocked step and ends with a TIMING line.
A fake actions tool and a fake transcript server stand in for the real ones. Test text is invented."""
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..")
TOOL = os.path.join(SCRIPTS, "coop_call_transcript.py")
FAKE_SERVER = os.path.join(HERE, "fake_transcript_server.py")
VID = "abcDEF12345"
STRAY = "zebra marmalade stray caption words"

FAKE_ACTIONS = '''import os, sys
a = sys.argv[1:]
folder = a[a.index("--dir") + 1]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(" ".join(a) + "\\n")
clean = "--clean" in a
print("model fake")
print("STRAY: zebra marmalade stray caption words")
print("zebra marmalade without a prefix")
if clean and os.environ.get("FAKE_BLOCK") == "clean" or (not clean and os.environ.get("FAKE_BLOCK") == "absorb"):
    print("BLOCKED fake exit")
    sys.exit(0)
name = "transcript-clean.md" if clean else "actions.md"
fd = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, b"x\\n"); os.close(fd)
print("DONE clean cues 10" if clean else "DONE actions 3")
'''


@pytest.fixture
def tmp_path():  # pytest's own tmp_path leaves a pytest-of-<user> folder, which test_no_tmp_leak refuses
    with tempfile.TemporaryDirectory(prefix="cct-test-") as d:
        yield Path(d)


def run(tmp, args, **env):
    fake = tmp / "fake_actions.py"
    if not fake.exists():
        fake.write_text(FAKE_ACTIONS)
    e = dict(os.environ)
    for k in list(e):
        if k.startswith("FAKE_"):
            del e[k]
    e.update({"FETCH_CAPTIONS_SERVER_CMD": "%s %s" % (shlex.quote(sys.executable), shlex.quote(FAKE_SERVER)),
              "COOP_CALL_ACTIONS": str(fake), "FAKE_LOG": str(tmp / "log.txt")})
    e.update(env)
    return subprocess.run([sys.executable, "-I", TOOL] + args, capture_output=True, text=True, env=e, timeout=120)


def calls(tmp):
    p = tmp / "log.txt"
    return p.read_text().splitlines() if p.exists() else []


def test_full_chain_order_and_counts_only(tmp_path):
    call = tmp_path / "call"
    r = run(tmp_path, ["--dir", str(call), "--video", VID, "--timeout", "77"])
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout.splitlines()
    assert [ln for ln in out if ln.startswith("## ")] == ["## fetch", "## clean", "## absorb"]
    assert out.index("## fetch") < out.index("## clean") < out.index("## absorb")
    assert calls(tmp_path) == ["--dir %s --clean --timeout 77" % call, "--dir %s --timeout 77" % call]
    assert "DONE clean cues 10" in out and "DONE actions 3" in out and "model fake" in out
    assert "zebra" not in r.stdout + r.stderr and "invented" not in r.stdout
    assert out[-1].startswith("TIMING fetch ") and " clean " in out[-1] and " absorb " in out[-1] and " total " in out[-1]
    for n in ("transcript.md", "transcript.srt", "transcript.json", "transcript-clean.md", "actions.md"):
        assert stat.S_IMODE(os.stat(call / n).st_mode) == 0o600


def test_clean_skipped_when_current_then_reclean(tmp_path):
    call = tmp_path / "call"
    assert run(tmp_path, ["--dir", str(call), "--video", VID]).returncode == 0
    now = time.time()
    os.utime(call / "transcript.srt", (now - 100, now - 100))
    os.utime(call / "transcript-clean.md", (now, now))
    (tmp_path / "log.txt").unlink()
    r = run(tmp_path, ["--dir", str(call)])  # the id now comes from the folder's header
    assert r.returncode == 0, r.stdout
    assert "clean skipped: transcript-clean.md is current" in r.stdout and "fetch skipped" in r.stdout
    assert "TIMING fetch" in r.stdout.splitlines()[-1] and "clean 0m 0s" in r.stdout
    assert calls(tmp_path) == ["--dir %s --timeout 600" % call]
    (tmp_path / "log.txt").unlink()
    r = run(tmp_path, ["--dir", str(call), "--reclean"])
    assert "clean skipped" not in r.stdout
    assert [c.split()[2] for c in calls(tmp_path)] == ["--clean", "--timeout"]


def test_blocked_clean_stops_before_absorb(tmp_path):
    r = run(tmp_path, ["--dir", str(tmp_path / "call"), "--video", VID], FAKE_BLOCK="clean")
    assert r.returncode != 0
    assert "BLOCKED clean exit" in r.stdout and "## absorb" not in r.stdout and "TIMING" not in r.stdout
    assert len(calls(tmp_path)) == 1


def test_blocked_absorb_exits_nonzero(tmp_path):
    r = run(tmp_path, ["--dir", str(tmp_path / "call"), "--video", VID], FAKE_BLOCK="absorb")
    assert r.returncode != 0 and "BLOCKED absorb exit" in r.stdout and "TIMING" not in r.stdout


def test_not_ready_stops_before_clean(tmp_path):
    call = tmp_path / "call"
    r = run(tmp_path, ["--dir", str(call), "--video", VID], FAKE_TRUNCATE="1")
    assert r.returncode == 4
    assert any(ln.startswith("NOT-READY last_caption") for ln in r.stdout.splitlines())
    assert "## clean" not in r.stdout and calls(tmp_path) == [] and not call.exists()
    r = run(tmp_path, ["--dir", str(call), "--video", VID, "--force"], FAKE_TRUNCATE="1")
    assert r.returncode == 0


@pytest.mark.parametrize("env,code", [({"FETCH_CAPTIONS_SERVER_CMD": "true"}, 2), ({"FAKE_ERROR": "1"}, 3)])
def test_failed_fetch_stops_before_clean(tmp_path, env, code):
    call = tmp_path / "call"
    r = run(tmp_path, ["--dir", str(call), "--video", VID], **env)
    assert r.returncode == code and "BLOCKED fetch exit %d" % code in r.stdout
    assert "## clean" not in r.stdout and "TIMING" not in r.stdout
    assert calls(tmp_path) == [] and not call.exists()


def test_no_video_id_and_no_header_is_blocked(tmp_path):
    call = tmp_path / "call"
    call.mkdir()
    r = run(tmp_path, ["--dir", str(call)])
    assert r.returncode != 0 and "BLOCKED fetch: no video id" in r.stdout and calls(tmp_path) == []


def test_bad_arguments_exit_1(tmp_path):
    assert run(tmp_path, []).returncode == 1
