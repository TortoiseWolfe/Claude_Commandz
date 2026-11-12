"""Put a pytest run inside the closed room (ticket 81). Called by a conftest.py before anything else is imported.

closed_room.sh makes the real home folder unreachable (the home path shows a throwaway folder), so a test cannot write into a developer's
live ~/.claude whatever it does with HOME or an emptied environment. This helper does one of three things:

  - already in a proven room: CLOSED_ROOM=1 AND the account-list home holds .closed-room. The variable alone is not trusted. It returns.
  - on Linux, not in the room: it starts the same pytest command again inside the room (os.execv) and never returns. If the room cannot be
    built, closed_room.sh says so and exits 97; there is no unprotected fallback on Linux.
  - not on Linux (no namespaces): HOME becomes a new temporary folder, one line on stderr says the room is not available, and the run goes on.

The room hides the real home, so two things are kept visible, read-only, when they are outside the checkout: the Python packages the run
imports from under the home (for example a user-level pytest), and a HOME the caller set to another folder (CI builds one with fixtures);
the contents of that HOME are copied into the room's throwaway home once the room is proven, so tests find them where they expect them.
"""
import os
import pwd
import shutil
import subprocess
import sys
import tempfile

SEED_VAR = "CLOSED_ROOM_SEED_HOME"
ROOM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "closed_room.sh")


def _account_home():
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def in_proven_room():
    return os.environ.get("CLOSED_ROOM") == "1" and os.path.exists(os.path.join(_account_home(), ".closed-room"))


def _under(path, folder):
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _checkout_top():
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    top = out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else os.getcwd()
    return os.path.realpath(top)


def _pytest_args(orig):
    """Everything after `-m pytest` (or after the pytest script) in the original command line."""
    if "-m" in orig[1:]:
        i = orig.index("-m", 1)
        return orig[i + 2:]
    return orig[2:]


def _plan(orig=None):
    """The closed_room.sh command line that repeats this pytest run inside the room."""
    orig = list(orig if orig is not None else sys.orig_argv)
    home, top = _account_home(), _checkout_top()
    cmd = [ROOM]
    seen = set()
    for p in sys.path:
        if not p or not os.path.isdir(p):
            continue
        rp = os.path.realpath(p)
        if _under(rp, home) and not _under(rp, top) and rp not in seen and rp != home:
            seen.add(rp)
            cmd += ["--ro", rp]
    # Tools installed under the home (a node from a version manager) are found through PATH; their folders stay visible, read-only.
    for p in os.environ.get("PATH", "").split(os.pathsep):
        rp = os.path.realpath(p) if p and os.path.isdir(p) else ""
        if (rp and _under(rp, home) and rp != home and not _under(rp, top) and rp not in seen
                and not any(_under(rp, os.path.join(home, d)) for d in (".claude", ".config", ".ssh", ".gnupg"))):
            seen.add(rp)
            cmd += ["--ro", rp]
    given = os.environ.get("HOME")
    if given and os.path.isdir(given) and os.path.realpath(given) != home:
        cmd += ["--ro", os.path.realpath(given)]
    return cmd + ["--", sys.executable, "-m", "pytest"] + _pytest_args(orig)


def _seed_home():
    seed = os.environ.pop(SEED_VAR, "")
    if seed and os.path.isdir(seed):
        shutil.copytree(seed, _account_home(), symlinks=True, dirs_exist_ok=True)


def enter():
    if in_proven_room():
        _seed_home()
        return
    if not sys.platform.startswith("linux"):
        os.environ["HOME"] = tempfile.mkdtemp(prefix="closed-room-home-")
        print("closed room: not available on this system; HOME is a temporary folder instead", file=sys.stderr)
        return
    if not os.path.isfile(ROOM):
        sys.stderr.write("closed room: closed_room.sh is missing; nothing was run\n")
        raise SystemExit(97)
    cmd = _plan()
    given = os.environ.get("HOME")
    if given and os.path.isdir(given) and os.path.realpath(given) != _account_home():
        os.environ[SEED_VAR] = os.path.realpath(given)
    _release_capture()
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(ROOM, cmd)


def _release_capture():
    """pytest already redirects fd 1 and 2 to temporary files while it imports conftest.py; a process started now would print into them.
    Stop that capture (it hands back the real fds) so the new pytest talks to the terminal or pipe the first one was started on."""
    import gc

    for obj in gc.get_objects():
        if type(obj).__name__ == "PytestPluginManager":
            cm = obj.get_plugin("capturemanager")
            if cm is not None:
                cm.stop_global_capturing()
            return
