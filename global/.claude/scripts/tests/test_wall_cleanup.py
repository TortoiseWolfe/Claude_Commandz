"""The no-AI wall's cleanup command (global/.claude/no-ai-wall/wall_cleanup.py), tried on fake wall folders,
each in a fresh temporary folder (the tmp_path fixture below, not pytest's own).

Each test copies the script into tmp_path/.claude/no-ai-wall beside stand-in manifest files and runs the copy with `python3 -I`. The real
~/.claude is never touched, and the script is never run from the repo.
"""
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
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
def tmp_path():
    """A fresh folder of this test's own, removed when the test ends. Never pytest's own tmp_path: that leaves a pytest-of-<user> folder
    behind and test_no_tmp_leak fails on it. The system's temporary folder is tried first; if a folder above it holds a .git entry (the
    script refuses to run under one, by design), the system's other temporary places are tried."""
    for top in (tempfile.gettempdir(), "/dev/shm", "/var/tmp"):
        if os.path.isdir(top) and os.access(top, os.W_OK) and not has_git_above(top):
            d = tempfile.mkdtemp(prefix="cc-test-wall-cleanup.", dir=top)
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


def test_in_flight_name_that_is_a_link_is_a_stray(tmp_path):
    wall = make_wall(tmp_path)
    target = os.path.join(str(tmp_path), "elsewhere.txt")
    with open(target, "wb") as fh:
        fh.write(b"outside bytes")
    name = ".no_ai_wall.py.new"
    os.symlink(target, os.path.join(wall, name))
    before = snapshot(tmp_path)
    r = run(wall)
    assert r.returncode == 0, r.stderr
    assert 'would move ".no_ai_wall.py.new" (link,' in r.stdout
    assert "left alone" not in r.stdout
    assert snapshot(tmp_path) == before
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    assert "left alone" not in r.stdout
    assert not os.path.lexists(os.path.join(wall, name))
    (hold,) = holding(wall)
    assert os.path.islink(os.path.join(hold, name)) and os.readlink(os.path.join(hold, name)) == target
    log = open(os.path.join(tmp_path, ".claude", "state", "no-ai-wall-cleanup.log"), encoding="utf-8").read()
    (line,) = log.split("\n")[:-1]
    fields = line.split("\t")
    assert fields[2] == json.dumps(name) and fields[3] == "link"
    assert open(target, "rb").read() == b"outside bytes"


@pytest.mark.parametrize("name", [".wall_cleanup.py.new", ".no_ai_wall.sh.new"])
def test_in_flight_name_that_is_a_folder_is_a_stray(tmp_path, name):
    wall = make_wall(tmp_path)
    os.makedirs(os.path.join(wall, name))
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    assert "left alone" not in r.stdout
    assert 'moved %s (folder,' % json.dumps(name) in r.stdout
    assert not os.path.lexists(os.path.join(wall, name))
    (hold,) = holding(wall)
    assert os.path.isdir(os.path.join(hold, name))


def test_in_flight_name_that_is_a_pipe_is_a_stray(tmp_path):
    # a pipe has one name, so only the kind test catches it (a folder usually has two or more names and is caught by the count as well)
    wall = make_wall(tmp_path)
    name = ".no_ai_wall.py.new"
    os.mkfifo(os.path.join(wall, name))
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    assert "left alone" not in r.stdout
    assert 'moved %s (other,' % json.dumps(name) in r.stdout
    assert not os.path.lexists(os.path.join(wall, name))


def test_in_flight_name_with_a_second_name_is_a_stray(tmp_path):
    wall = make_wall(tmp_path)
    name = ".no-ai-wall-guard.py.new"
    other = os.path.join(str(tmp_path), "second-name.txt")
    with open(os.path.join(wall, name), "wb") as fh:
        fh.write(b"shared bytes")
    os.link(os.path.join(wall, name), other)
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    assert "left alone" not in r.stdout
    assert 'moved %s (file,' % json.dumps(name) in r.stdout
    assert not os.path.lexists(os.path.join(wall, name))
    assert open(other, "rb").read() == b"shared bytes"


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


def log_setup(tmp_path):
    """A wall with one stray, an existing state folder, and a file elsewhere that the log must never reach."""
    wall = make_wall(tmp_path)
    put_stray(wall)
    state = os.path.join(str(tmp_path), ".claude", "state")
    os.makedirs(state)
    target = os.path.join(str(tmp_path), "elsewhere.txt")
    with open(target, "w") as fh:
        fh.write("kept as it was\n")
    return wall, os.path.join(state, "no-ai-wall-cleanup.log"), target


def test_refused_log_is_a_symbolic_link(tmp_path):
    wall, log, target = log_setup(tmp_path)
    os.symlink(target, log)
    before = snapshot(tmp_path)
    r = run(wall, "--apply")
    assert refused(r), r.stdout + r.stderr
    assert snapshot(tmp_path) == before
    assert open(target).read() == "kept as it was\n"
    assert os.path.exists(os.path.join(wall, "stray.txt"))


def test_refused_log_is_a_link_to_nothing(tmp_path):
    wall, log, target = log_setup(tmp_path)
    missing = os.path.join(str(tmp_path), "not-there.txt")
    os.symlink(missing, log)
    before = snapshot(tmp_path)
    r = run(wall, "--apply")
    assert refused(r), r.stdout + r.stderr
    assert snapshot(tmp_path) == before
    assert not os.path.lexists(missing)


def test_refused_log_is_a_hard_link(tmp_path):
    wall, log, target = log_setup(tmp_path)
    os.link(target, log)
    before = snapshot(tmp_path)
    r = run(wall, "--apply")
    assert refused(r), r.stdout + r.stderr
    assert snapshot(tmp_path) == before
    assert open(target).read() == "kept as it was\n"
    assert os.path.exists(os.path.join(wall, "stray.txt"))


def test_refused_log_is_a_folder(tmp_path):
    wall, log, target = log_setup(tmp_path)
    os.mkdir(log)
    before = snapshot(tmp_path)
    r = run(wall, "--apply")
    assert refused(r), r.stdout + r.stderr
    assert snapshot(tmp_path) == before


def test_refused_holding_folder_is_a_file(tmp_path):
    wall, log, target = log_setup(tmp_path)
    with open(os.path.join(os.path.dirname(log), "no-ai-wall-strays"), "w") as fh:
        fh.write("not a folder\n")
    before = snapshot(tmp_path)
    r = run(wall, "--apply")
    assert refused(r), r.stdout + r.stderr
    assert "Traceback" not in r.stderr
    assert snapshot(tmp_path) == before


def test_a_refused_apply_leaves_stdout_empty_and_keeps_the_notes_on_stderr(tmp_path):
    """Exit 3 writes nothing to stdout: the 'left alone' line held back during the scan goes to stderr, before the reason."""
    wall = make_wall(tmp_path)
    put_stray(wall)
    with open(os.path.join(wall, ".no_ai_wall.py.new"), "w") as fh:
        fh.write("half")
    state = os.path.join(os.path.dirname(wall), "state")
    os.makedirs(state)
    with open(os.path.join(state, "no-ai-wall-strays"), "w") as fh:
        fh.write("not a folder\n")
    r = run(wall, "--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert r.stdout == ""
    assert 'left alone ".no_ai_wall.py.new" (an install may be running)' in r.stderr
    assert r.stderr.index("left alone") < r.stderr.index("wall cleanup: refused:")
    assert "nothing was moved" in r.stderr
    assert os.path.exists(os.path.join(wall, "stray.txt"))


def test_a_move_that_fails_is_counted_and_not_logged(tmp_path):
    """A wall folder nobody may write to: the stray cannot leave it, so the run says so, exits 1 and logs no move."""
    assert os.geteuid() != 0, "this test needs an ordinary user: root can write to a read-only folder"
    wall = make_wall(tmp_path)
    put_stray(wall)
    os.chmod(wall, 0o555)
    try:
        r = run(wall, "--apply")
    finally:
        os.chmod(wall, 0o755)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr and "could not move" in r.stderr
    assert re.fullmatch(r"WALL STRAYS 0 moved to state/no-ai-wall-strays/\d{8}-\d{6}, 1 could not be moved", last(r)), last(r)
    assert os.path.exists(os.path.join(wall, "stray.txt"))
    (hold,) = holding(wall)
    assert os.listdir(hold) == []
    assert open(os.path.join(str(tmp_path), ".claude", "state", "no-ai-wall-cleanup.log")).read() == ""


@pytest.mark.parametrize("args", [("--apply", "x"), ("x",), ("/etc",), ("--dry",), ("--apply", "--apply"), ("--apply=1",)])
def test_usage_errors(tmp_path, args):
    wall = make_wall(tmp_path)
    put_stray(wall)
    before = snapshot(tmp_path)
    r = run(wall, *args)
    assert r.returncode == 2 and r.stderr.startswith("usage:")
    assert snapshot(tmp_path) == before


def test_a_taken_folder_name_gets_a_number_suffix(tmp_path):
    """The holding folder is named for the second it was made. The first run gives the clock the command sees; the test then takes the
    plain name of every second in the next minute, so the second run must land on a taken name and add -2, and the third (with every -2
    taken as well) must add -3. Nothing here depends on two runs falling in the same second."""
    wall = make_wall(tmp_path)
    put_stray(wall)
    assert run(wall, "--apply").returncode == 0
    (first,) = holding(wall)
    root = os.path.dirname(first)
    start = datetime.datetime.strptime(os.path.basename(first), "%Y%m%d-%H%M%S")
    names = [(start + datetime.timedelta(seconds=i)).strftime("%Y%m%d-%H%M%S") for i in range(61)]
    for n in names[1:]:
        os.mkdir(os.path.join(root, n))

    put_stray(wall)
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    second = [d for d in os.listdir(root) if d not in names]
    assert len(second) == 1 and second[0] in [n + "-2" for n in names], second
    assert last(r) == "WALL STRAYS 1 moved to state/no-ai-wall-strays/" + second[0]
    assert os.listdir(os.path.join(root, second[0])) == ["stray.txt"]

    for n in names:
        if n + "-2" != second[0]:
            os.mkdir(os.path.join(root, n + "-2"))
    put_stray(wall)
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    third = [d for d in os.listdir(root) if d not in names and not d.endswith("-2")]
    assert len(third) == 1 and third[0] in [n + "-3" for n in names], third
    assert os.listdir(os.path.join(root, third[0])) == ["stray.txt"]
    assert all(os.listdir(os.path.join(root, n)) == [] for n in names[1:])


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


def test_source_opens_files_only_the_careful_way():
    """No plain open() anywhere in the script: every file and folder it opens goes through os.open with a no-follow flag, so a name
    that was swapped for a link is refused instead of followed."""
    src = open(SCRIPT_SRC).read()
    assert re.search(r"(?<![.\w])open\(", src) is None
    flags = dict(re.findall(r"^(\w+_FLAGS) = (.+)$", src, re.M))
    assert set(flags) == {"DIR_FLAGS", "LOG_FLAGS", "HASH_FLAGS"}
    for name, value in flags.items():
        assert "os.O_NOFOLLOW" in value and "os.O_CLOEXEC" in value, name
    assert "os.O_NONBLOCK" in flags["LOG_FLAGS"] and "os.O_NONBLOCK" in flags["HASH_FLAGS"]
    assert "O_TRUNC" not in src
    opens = [line for line in src.split("\n") if "os.open(" in line]
    assert len(opens) == 5, opens
    for line in opens:
        assert re.search(r"os\.open\(\w+, (DIR_FLAGS|LOG_FLAGS|HASH_FLAGS)\b", line), line
    # the wall's own folder is reached only through the open descriptor: no scan, look or move by path text
    assert "os.scandir(wall)" not in src and "e.path" not in src and "os.path.join(wall" not in src
    assert "src_dir_fd=" in src and "os.scandir(wall_fd)" in src


CALL = """
import importlib.util, json, os, stat, sys
spec = importlib.util.spec_from_file_location("wall_cleanup_under_test", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
what = sys.argv[2]
if what == "sha":
    dfd = os.open(os.path.dirname(sys.argv[3]), os.O_RDONLY | os.O_DIRECTORY)
    print(json.dumps(mod.sha256_of(os.path.basename(sys.argv[3]), os.lstat(sys.argv[4]), dfd)))
elif what == "move":
    fd = os.open(sys.argv[5], os.O_RDONLY | os.O_DIRECTORY)
    sfd = os.open(sys.argv[3], os.O_RDONLY | os.O_DIRECTORY)
    try:
        mod.move_out(sfd, sys.argv[4], fd)
        print(json.dumps("moved"))
    except OSError as exc:
        print(json.dumps(type(exc).__name__))
"""


def call(*args):
    """Call one function of the script in a separate isolated Python (loaded that way the script does not restart itself)."""
    r = subprocess.run([sys.executable, "-I", "-c", CALL, SCRIPT_SRC, *args], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def log_fields(tmp_path):
    text = open(os.path.join(str(tmp_path), ".claude", "state", "no-ai-wall-cleanup.log"), encoding="utf-8").read()
    return [line.split("\t") for line in text.split("\n")[:-1]]


def test_an_existing_log_is_added_to_never_emptied(tmp_path):
    wall, log, _ = log_setup(tmp_path)
    with open(log, "w") as fh:
        fh.write("an older line\n")
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    lines = open(log).read().split("\n")[:-1]
    assert lines[0] == "an older line" and len(lines) == 2 and "\tmoved\t" in lines[1]


def test_a_stray_over_the_size_limit_is_moved_with_a_dash_for_its_checksum(tmp_path):
    wall = make_wall(tmp_path)
    with open(os.path.join(wall, "big.bin"), "wb") as fh:
        fh.write(b"x" * (1024 * 1024 + 1))
    with open(os.path.join(wall, "edge.bin"), "wb") as fh:
        fh.write(b"y" * (1024 * 1024))
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    by_name = {f[2]: f for f in log_fields(tmp_path)}
    assert by_name['"big.bin"'][3:6] == ["file", str(1024 * 1024 + 1), "-"]
    assert by_name['"edge.bin"'][5] == hashlib.sha256(b"y" * (1024 * 1024)).hexdigest()
    (hold,) = holding(wall)
    assert os.path.getsize(os.path.join(hold, "big.bin")) == 1024 * 1024 + 1


def test_a_pipe_stray_is_moved_as_kind_other_without_waiting_on_it(tmp_path):
    wall = make_wall(tmp_path)
    os.mkfifo(os.path.join(wall, "a-pipe"))
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    (f,) = log_fields(tmp_path)
    assert f[2] == '"a-pipe"' and f[3] == "other" and f[5] == "-"
    (hold,) = holding(wall)
    assert stat.S_ISFIFO(os.lstat(os.path.join(hold, "a-pipe")).st_mode)
    assert sorted(os.listdir(wall)) == sorted(MANIFEST)


def test_refused_log_is_a_pipe(tmp_path):
    wall, log, target = log_setup(tmp_path)
    os.mkfifo(log)
    r = run(wall, "--apply")
    assert refused(r), r.stdout + r.stderr
    assert stat.S_ISFIFO(os.lstat(log).st_mode)
    assert os.path.exists(os.path.join(wall, "stray.txt"))
    assert not os.path.exists(os.path.join(str(tmp_path), ".claude", "state", "no-ai-wall-strays"))
    assert open(target).read() == "kept as it was\n"


def test_manifest_name_that_is_a_folder_warns_and_is_left_alone(tmp_path):
    wall = make_wall(tmp_path)
    os.remove(os.path.join(wall, "no_ai_wall.sh"))
    os.mkdir(os.path.join(wall, "no_ai_wall.sh"))
    with open(os.path.join(wall, "no_ai_wall.sh", "inside.txt"), "w") as fh:
        fh.write("kept")
    before = snapshot(tmp_path)
    r = run(wall, "--apply")
    assert r.returncode == 1, r.stdout + r.stderr
    assert 'WARNING manifest name "no_ai_wall.sh" is not a regular file' in r.stdout
    assert last(r) == "WALL CLEAN"
    assert snapshot(tmp_path) == before


def test_dry_run_still_lists_when_the_state_folder_is_a_link(tmp_path):
    wall = make_wall(tmp_path)
    put_stray(wall)
    real = os.path.join(str(tmp_path), "elsewhere")
    os.makedirs(real)
    os.symlink(real, os.path.join(str(tmp_path), ".claude", "state"))
    before = snapshot(tmp_path)
    r = run(wall)
    assert r.returncode == 0, r.stderr
    assert 'would move "stray.txt" (file, 1 bytes)' in r.stdout
    assert last(r) == "WALL STRAYS 1 found, nothing moved (dry run; add --apply)"
    assert snapshot(tmp_path) == before and os.listdir(real) == []


def test_checksum_of_a_name_swapped_for_a_pipe_gives_a_dash_and_does_not_wait(tmp_path):
    """The first look said "small regular file"; by the time it is opened the name is a pipe with nobody writing to it."""
    small = os.path.join(str(tmp_path), "small.txt")
    with open(small, "w") as fh:
        fh.write("s")
    pipe = os.path.join(str(tmp_path), "now-a-pipe")
    os.mkfifo(pipe)
    assert call("sha", pipe, small) == "-"
    assert call("sha", small, small) == hashlib.sha256(b"s").hexdigest()


def test_checksum_of_a_name_swapped_for_a_bigger_file_or_a_link_gives_a_dash(tmp_path):
    small = os.path.join(str(tmp_path), "small.txt")
    with open(small, "w") as fh:
        fh.write("s")
    big = os.path.join(str(tmp_path), "big.bin")
    with open(big, "wb") as fh:
        fh.write(b"x" * (1024 * 1024 + 1))
    link = os.path.join(str(tmp_path), "a-link")
    os.symlink(small, link)
    assert call("sha", big, small) == "-"
    assert call("sha", link, small) == "-"


SWAP = """
import importlib.util, os, sys
sys.dont_write_bytecode = True
wall = sys.argv[1]
spec =importlib.util.spec_from_file_location("wall_cleanup_under_test", os.path.join(wall, "wall_cleanup.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
real = mod.open_holding
def swapping(base):
    result = real(base)
    os.rename(wall, wall + "-moved")
    os.symlink(sys.argv[2], wall)
    return result
mod.open_holding = swapping
sys.exit(mod.main(["--apply"]))
"""


def test_a_wall_folder_swapped_mid_run_moves_nothing_from_the_other_folder(tmp_path):
    """Between the listing and the move the wall folder is renamed away and a link to another folder takes its place."""
    wall = make_wall(tmp_path)
    with open(os.path.join(wall, "stray.txt"), "w") as fh:
        fh.write("from the wall")
    decoy = os.path.join(str(tmp_path), "decoy")
    os.mkdir(decoy)
    with open(os.path.join(decoy, "stray.txt"), "w") as fh:
        fh.write("from the decoy")
    r = subprocess.run([sys.executable, "-I", "-c", SWAP, wall, decoy], capture_output=True, text=True, timeout=30,
                       cwd=str(tmp_path), env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert open(os.path.join(decoy, "stray.txt")).read() == "from the decoy"
    (hold,) = holding(wall + "-moved")
    assert open(os.path.join(hold, "stray.txt")).read() == "from the wall"


def test_a_stray_with_a_second_name_is_moved_with_a_dash_for_its_checksum(tmp_path):
    wall = make_wall(tmp_path)
    outside = os.path.join(str(tmp_path), "outside.txt")
    with open(outside, "w") as fh:
        fh.write("not the wall's")
    os.link(outside, os.path.join(wall, "second-name"))
    r = run(wall, "--apply")
    assert r.returncode == 0, r.stderr
    (f,) = log_fields(tmp_path)
    assert f[2] == '"second-name"' and f[5] == "-"
    assert open(outside).read() == "not the wall's"
    (hold,) = holding(wall)
    assert os.path.exists(os.path.join(hold, "second-name"))


def test_a_move_never_writes_over_a_name_already_in_the_holding_folder(tmp_path):
    src = os.path.join(str(tmp_path), "from")
    dst = os.path.join(str(tmp_path), "to")
    os.makedirs(src)
    os.makedirs(dst)
    for folder, body in ((src, "the stray"), (dst, "already there")):
        with open(os.path.join(folder, "same-name"), "w") as fh:
            fh.write(body)
    with open(os.path.join(src, "free-name"), "w") as fh:
        fh.write("moves")
    assert call("move", src, "same-name", dst) == "FileExistsError"
    assert open(os.path.join(src, "same-name")).read() == "the stray"
    assert open(os.path.join(dst, "same-name")).read() == "already there"
    assert call("move", src, "free-name", dst) == "moved"
    assert open(os.path.join(dst, "free-name")).read() == "moves" and not os.path.exists(os.path.join(src, "free-name"))
