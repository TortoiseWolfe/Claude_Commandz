"""The no-AI wall's cleanup command (global/.claude/no-ai-wall/wall_cleanup.py), tried on fake wall folders under pytest's tmp_path.

Each test copies the script into tmp_path/.claude/no-ai-wall beside stand-in manifest files and runs the copy with `python3 -I`. The real
~/.claude is never touched, and the script is never run from the repo.
"""
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WALL_SRC = os.path.normpath(os.path.join(HERE, "..", "..", "no-ai-wall"))
SCRIPT_SRC = os.path.join(WALL_SRC, "wall_cleanup.py")
MANIFEST = ("no-ai-wall-guard.py", "no_ai_wall.py", "no_ai_wall.sh", "wall_cleanup.py")


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def make_wall(root, wall_name="no-ai-wall", parent=".claude"):
    wall = os.path.join(str(root), parent, wall_name)
    os.makedirs(wall)
    for n in MANIFEST:
        if n == "wall_cleanup.py":
            shutil.copy(SCRIPT_SRC, os.path.join(wall, n))
        else:
            with open(os.path.join(wall, n), "w") as fh:
                fh.write("# stand-in %s\n" % n)
    return wall


def run(wall, *args, home=None, cwd=None):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home or os.path.dirname(os.path.dirname(wall)))}
    return subprocess.run([sys.executable, "-I", os.path.join(wall, "wall_cleanup.py"), *args], capture_output=True, text=True,
                          cwd=str(cwd or os.path.dirname(os.path.dirname(wall))), env=env, timeout=60)


def snapshot(top):
    out = {}
    for d, dirs, files in os.walk(str(top)):
        for n in dirs + files:
            p = os.path.join(d, n)
            out[p] = ("link", os.readlink(p)) if os.path.islink(p) else (("dir",) if os.path.isdir(p) else ("file", sha(p)))
    return out


def last(r):
    return r.stdout.rstrip("\n").split("\n")[-1]


def holding(wall):
    root = os.path.join(os.path.dirname(wall), "state", "no-ai-wall-strays")
    return [os.path.join(root, d) for d in sorted(os.listdir(root))]


@pytest.fixture
def tmp_path(tmp_path_factory):
    """pytest's own temporary folder, unless a folder above it holds a .git entry (the script refuses to run under one, by design); then a
    fresh folder from the system's other temporary places that has no .git above it."""
    p = tmp_path_factory.mktemp("wc")
    if not has_git_above(str(p)):
        yield p
        return
    for top in ("/dev/shm", "/var/tmp"):
        if os.path.isdir(top) and os.access(top, os.W_OK) and not has_git_above(top):
            d = tempfile.mkdtemp(prefix="wall-cleanup-test.", dir=top)
            try:
                yield pathlib.Path(d)
            finally:
                shutil.rmtree(d, True)
            return
    pytest.fail("no temporary folder without a .git above it")


def has_git_above(path):
    while True:
        if os.path.lexists(os.path.join(path, ".git")):
            return True
        up = os.path.dirname(path)
        if up == path:
            return False
        path = up


def test_clean_folder(tmp_path):
    wall = make_wall(tmp_path)
    before = snapshot(tmp_path)
    r = run(wall)
    assert (r.returncode, last(r)) == (0, "WALL CLEAN"), r.stdout + r.stderr
    r = run(wall, "--apply")
    assert (r.returncode, last(r)) == (0, "WALL CLEAN")
    assert snapshot(tmp_path) == before
    assert not os.path.exists(os.path.join(tmp_path, ".claude", "state"))


def test_dry_run_lists_and_moves_nothing(tmp_path):
    wall = make_wall(tmp_path)
    with open(os.path.join(wall, "stray.txt"), "w") as fh:
        fh.write("hello")
    with open(os.path.join(wall, "odd\nname"), "w") as fh:
        fh.write("x")
    before = snapshot(tmp_path)
    r = run(wall)
    assert r.returncode == 0, r.stderr
    assert 'would move "stray.txt" (file, 5 bytes)' in r.stdout
    assert 'would move "odd\\nname" (file, 1 bytes)' in r.stdout
    assert last(r) == "WALL STRAYS 2 found, nothing moved (dry run; add --apply)"
    assert snapshot(tmp_path) == before


def test_apply_moves_strays_and_logs(tmp_path):
    wall = make_wall(tmp_path)
    for n, body in (("stray.txt", b"hello"), ("odd\nname", b"\x00\x01bytes")):
        with open(os.path.join(wall, n), "wb") as fh:
            fh.write(body)
    mani = {n: sha(os.path.join(wall, n)) for n in MANIFEST}
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    assert re.fullmatch(r"WALL STRAYS 2 moved to state/no-ai-wall-strays/\d{8}-\d{6}", last(r)), last(r)
    assert sorted(os.listdir(wall)) == sorted(MANIFEST)
    assert {n: sha(os.path.join(wall, n)) for n in MANIFEST} == mani
    (hold,) = holding(wall)
    assert open(os.path.join(hold, "stray.txt"), "rb").read() == b"hello"
    assert open(os.path.join(hold, "odd\nname"), "rb").read() == b"\x00\x01bytes"
    log = open(os.path.join(tmp_path, ".claude", "state", "no-ai-wall-cleanup.log"), encoding="utf-8").read()
    lines = log.split("\n")[:-1]
    assert len(lines) == 2
    by_name = {l.split("\t")[2]: l.split("\t") for l in lines}
    f = by_name['"stray.txt"']
    assert f[1] == "moved" and f[3] == "file" and f[4] == "5" and f[5] == hashlib.sha256(b"hello").hexdigest()
    assert f[6] == "state/no-ai-wall-strays/" + os.path.basename(hold)
    g = by_name['"odd\\nname"']
    assert g[5] == hashlib.sha256(b"\x00\x01bytes").hexdigest()
    assert "hello" not in log


def test_folder_and_link_strays_move_whole(tmp_path):
    wall = make_wall(tmp_path)
    nolist = os.path.join(str(tmp_path), "elsewhere", "no-ai-paths.txt")
    os.makedirs(os.path.dirname(nolist))
    with open(nolist, "w") as fh:
        fh.write("/made/up/entry\n")
    ino, body = os.stat(nolist).st_ino, open(nolist, "rb").read()
    os.makedirs(os.path.join(wall, "adir", "sub"))
    with open(os.path.join(wall, "adir", "sub", "f.txt"), "w") as fh:
        fh.write("inside")
    os.symlink(nolist, os.path.join(wall, "listlink"))
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    assert 'moved "adir" (folder,' in r.stdout and 'moved "listlink" (link,' in r.stdout
    (hold,) = holding(wall)
    assert open(os.path.join(hold, "adir", "sub", "f.txt")).read() == "inside"
    assert os.path.islink(os.path.join(hold, "listlink")) and os.readlink(os.path.join(hold, "listlink")) == nolist
    assert os.stat(nolist).st_ino == ino and open(nolist, "rb").read() == body
    assert sorted(os.listdir(wall)) == sorted(MANIFEST)
    log = open(os.path.join(tmp_path, ".claude", "state", "no-ai-wall-cleanup.log")).read()
    assert all(l.split("\t")[5] == "-" for l in log.split("\n")[:-1])


def test_manifest_name_that_is_a_link_is_left_alone(tmp_path):
    wall = make_wall(tmp_path)
    target = os.path.join(str(tmp_path), "real.sh")
    shutil.move(os.path.join(wall, "no_ai_wall.sh"), target)
    os.symlink(target, os.path.join(wall, "no_ai_wall.sh"))
    r = run(wall, "--apply")
    assert r.returncode == 1
    assert 'WARNING manifest name "no_ai_wall.sh" is not a regular file' in r.stdout
    assert os.path.islink(os.path.join(wall, "no_ai_wall.sh"))
    assert last(r) == "WALL CLEAN"


def test_in_flight_temp_name_is_left_alone(tmp_path):
    wall = make_wall(tmp_path)
    with open(os.path.join(wall, ".no_ai_wall.py.new"), "w") as fh:
        fh.write("half")
    r = run(wall, "--apply")
    assert r.returncode == 0
    assert 'left alone ".no_ai_wall.py.new" (an install may be running)' in r.stdout
    assert os.path.exists(os.path.join(wall, ".no_ai_wall.py.new"))
    assert last(r) == "WALL CLEAN"


def put_stray(wall):
    with open(os.path.join(wall, "stray.txt"), "w") as fh:
        fh.write("s")


def refused(r):
    return r.returncode == 3 and r.stderr.startswith("wall cleanup: refused:") and r.stderr.count("\n") == 1


def test_refused_wrong_folder_name(tmp_path):
    wall = make_wall(tmp_path, wall_name="other-wall")
    put_stray(wall)
    before = snapshot(tmp_path)
    r = run(wall, "--apply")
    assert refused(r), r.stderr
    assert snapshot(tmp_path) == before


def test_refused_wrong_parent_name(tmp_path):
    wall = make_wall(tmp_path, parent="dotclaude")
    put_stray(wall)
    before = snapshot(tmp_path)
    r = run(wall, "--apply")
    assert refused(r), r.stderr
    assert snapshot(tmp_path) == before


def test_refused_manifest_file_missing(tmp_path):
    wall = make_wall(tmp_path)
    put_stray(wall)
    os.remove(os.path.join(wall, "no_ai_wall.sh"))
    before = snapshot(tmp_path)
    r = run(wall, "--apply")
    assert refused(r), r.stderr
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("where", ["wall", "claude", "two_up"])
def test_refused_git_entry(tmp_path, where):
    wall = make_wall(tmp_path)
    put_stray(wall)
    folder = {"wall": wall, "claude": os.path.dirname(wall), "two_up": str(tmp_path)}[where]
    os.mkdir(os.path.join(folder, ".git"))
    before = snapshot(tmp_path)
    r = run(wall)
    assert refused(r), r.stderr
    assert snapshot(tmp_path) == before


def test_refused_strays_folder_is_a_link(tmp_path):
    wall = make_wall(tmp_path)
    put_stray(wall)
    state = os.path.join(str(tmp_path), ".claude", "state")
    os.makedirs(state)
    away = os.path.join(str(tmp_path), "away")
    os.mkdir(away)
    os.symlink(away, os.path.join(state, "no-ai-wall-strays"))
    before = snapshot(tmp_path)
    r = run(wall, "--apply")
    assert refused(r), r.stderr
    assert snapshot(tmp_path) == before
    assert os.listdir(away) == []


def test_refused_state_folder_is_a_link(tmp_path):
    wall = make_wall(tmp_path)
    put_stray(wall)
    away = os.path.join(str(tmp_path), "away")
    os.mkdir(away)
    os.symlink(away, os.path.join(str(tmp_path), ".claude", "state"))
    before = snapshot(tmp_path)
    r = run(wall, "--apply")
    assert refused(r), r.stderr
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("args", [("--apply", "x"), ("x",), ("/etc",), ("--dry",), ("--apply", "--apply"), ("--apply=1",)])
def test_usage_errors(tmp_path, args):
    wall = make_wall(tmp_path)
    put_stray(wall)
    before = snapshot(tmp_path)
    r = run(wall, *args)
    assert r.returncode == 2 and r.stderr.startswith("usage:")
    assert snapshot(tmp_path) == before


def test_two_runs_in_one_second_make_two_folders(tmp_path):
    wall = make_wall(tmp_path)
    put_stray(wall)
    assert run(wall, "--apply").returncode == 0
    put_stray(wall)
    assert run(wall, "--apply").returncode == 0
    holds = holding(wall)
    assert len(holds) == 2 and len(set(holds)) == 2
    assert all(os.path.exists(os.path.join(h, "stray.txt")) for h in holds)


def test_never_reads_home(tmp_path):
    wall = make_wall(tmp_path / "real")
    other = tmp_path / "other"
    other_wall = make_wall(other)
    put_stray(other_wall)
    before = snapshot(other)
    r = run(wall, "--apply", home=other, cwd=tmp_path)
    assert r.returncode == 0 and last(r) == "WALL CLEAN"
    assert snapshot(other) == before


def test_manifest_matches_the_other_lists():
    src = open(SCRIPT_SRC).read()
    mine = set(re.search(r"^MANIFEST = \(([^)]*)\)", src, re.M).group(1).replace('"', "").replace(" ", "").split(","))
    wall_py = open(os.path.join(WALL_SRC, "no_ai_wall.py")).read()
    theirs = set(re.search(r"^_WALL_FILES = \(([^)]*)\)", wall_py, re.M).group(1).replace('"', "").replace(" ", "").split(","))
    inst = open(os.path.join(WALL_SRC, "install.sh")).read()
    bash = set(re.search(r"^WALL_FILES=\(([^)]*)\)", inst, re.M).group(1).split())
    assert mine == theirs == bash == set(MANIFEST)


def test_source_deletes_and_starts_nothing():
    src = open(SCRIPT_SRC).read()
    for word in ("subprocess", "os.system", "shutil.rmtree", "os.remove", "os.unlink"):
        assert word not in src, word
