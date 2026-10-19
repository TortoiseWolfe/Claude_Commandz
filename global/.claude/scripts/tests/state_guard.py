"""No test may write the live ~/.claude/state (19 Oct 2026, 5:48 PM: a test run left two fake posts in his chat outbox and 168 fake
errors in the live inject log). Imported first by conftest.py (pytest) and by mutate_openclaw_ask.py (unittest). Every write, rename,
delete or mkdir under the live state folder is refused and recorded; at exit the run fails if any was tried, even when the code under
test swallowed the error."""
import atexit
import os
import sys
from pathlib import Path

LIVE = os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude", "state"))
tried = []
_WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _live(p):
    try:
        p = os.fsdecode(p) if not isinstance(p, int) else None
    except TypeError:
        return False
    if not p:
        return False
    r = os.path.realpath(os.path.abspath(p))
    return r == LIVE or r.startswith(LIVE + os.sep)


def _hook(event, args):
    if event == "open":
        path, mode, flags = args
        writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (flags & _WRITE)
        bad = writes and _live(path)
    elif event in ("os.rename", "os.replace"):
        bad = _live(args[0]) or _live(args[1])
    elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.truncate", "os.utime", "os.chmod", "shutil.rmtree", "os.symlink", "os.link"):
        bad = _live(args[0])
    else:
        return
    if bad:
        tried.append("%s %r" % (event, args[:2]))
        raise PermissionError("test wrote the live %s: %s %r" % (LIVE, event, args[:2]))


def _report():
    if tried:
        sys.stderr.write("\nstate_guard: %d write(s) to the live ~/.claude/state were refused:\n  %s\n" % (len(tried), "\n  ".join(tried[:40])))
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(3)


if not getattr(sys, "_state_guard_on", False):
    sys._state_guard_on = True
    sys.addaudithook(_hook)
    atexit.register(_report)
