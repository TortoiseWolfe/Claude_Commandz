#!/usr/bin/env python3
"""The no-AI wall's own cleanup command: finds files that do not belong in ~/.claude/no-ai-wall and moves them out.

Why it exists: a script can drop files into the wall's folder unseen, and no session may write there to take them out (the wall refuses
every change in that folder). A person runs this command in a terminal instead.

What it does: looks at the direct entries of its own folder only. The wall's own files (the manifest below) are left alone. Anything
else is a stray. A dry run lists the strays; with --apply each stray is moved (never deleted) into
~/.claude/state/no-ai-wall-strays/<date-time>/ and one line per move is added to ~/.claude/state/no-ai-wall-cleanup.log (name, kind,
size and, for a small regular file, a sha256; never file content).

What it will never do: touch a manifest file, the no-AI list, or any file outside its own folder; delete anything; start another
program; take a path, a folder or a file name from the caller; read the HOME setting. It refuses to run anywhere but an installed wall
(a folder named no-ai-wall inside a folder named .claude, holding all manifest files, with no .git entry in it or above it).

Run: python3 -I ~/.claude/no-ai-wall/wall_cleanup.py            (dry run: list only, change nothing)
     python3 -I ~/.claude/no-ai-wall/wall_cleanup.py --apply    (move the strays)

The holding folder and the log are opened before the first move, each by its own name inside the open folder above it and never through
a symbolic link; the log must be an ordinary file with one name. If any of that fails, the run is refused and no stray is moved.

Exit codes: 0 clean, or all moved, or dry run; 1 a warning or a failed move; 2 usage; 3 refused (nothing moved).
The last line on stdout is always one summary line starting with WALL.
"""
import os
import sys

# Nothing but `os` and `sys` (both already loaded by Python itself) is imported before this point. A script that imported anything else
# from a search path holding a folder a session can write (the working folder, ~/.claude/scripts, PYTHONPATH, the user's site
# folder) would run a planted json.py or re.py inside the wall. Isolated mode (-I) drops the script folder, the working folder,
# PYTHONPATH and the user site from the search path; the standard library is all that is left. Run it as
# `python3 -I <this file>` as well, so the first start-up is isolated too.
if not sys.flags.isolated:
    try:
        os.execv(sys.executable, [sys.executable, "-I", os.path.realpath(__file__)] + sys.argv[1:])
    except (OSError, ValueError):  # cannot re-start: keep going, with the search path cut down to the standard library
        _here = os.path.dirname(os.path.realpath(__file__))
        sys.path[:] = [p for p in sys.path if p and os.path.realpath(p) != _here and "site-packages" not in p and "dist-packages" not in p]

import hashlib  # noqa: E402  (standard library, imported only after the search path is safe)
import json  # noqa: E402
import stat  # noqa: E402
import time  # noqa: E402

MANIFEST = ("no-ai-wall-guard.py", "no_ai_wall.py", "no_ai_wall.sh", "wall_cleanup.py")
IN_FLIGHT = tuple("." + n + ".new" for n in MANIFEST)
USAGE = "usage: python3 -I wall_cleanup.py [--apply]\n"
HASH_LIMIT = 1024 * 1024
STRAYS_NAME = "no-ai-wall-strays"
LOG_NAME = "no-ai-wall-cleanup.log"
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
LOG_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def refuse(why):
    sys.stderr.write("wall cleanup: refused: %s\n" % why)
    return 3


def kind_of(mode):
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "folder"
    if stat.S_ISLNK(mode):
        return "link"
    return "other"


def sha256_of(path, st):
    """sha256 of a regular file up to 1 MB, opened without following a link; '-' otherwise."""
    if not stat.S_ISREG(st.st_mode) or st.st_size > HASH_LIMIT:
        return "-"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return "-"
    try:
        h = hashlib.sha256()
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return "-"
    finally:
        if fd >= 0:
            os.close(fd)


def has_git_entry(wall):
    cur = wall
    while True:
        if os.path.lexists(os.path.join(cur, ".git")):
            return True
        up = os.path.dirname(cur)
        if up == cur:
            return False
        cur = up


def open_folder(name, parent_fd, make=False):
    """Open the folder `name` that sits directly inside the already-open folder `parent_fd`. A name that is a symbolic link, a file or
    anything else but a real folder raises OSError. With make, the folder is created first when it is missing."""
    if make:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    return os.open(name, DIR_FLAGS, dir_fd=parent_fd)


def open_log(state_fd, make=False):
    """Open the log for adding lines. The name is never followed if it is a symbolic link, and the opened thing must be an ordinary file
    with exactly one name (a file that also has a name somewhere else is refused), so a line can only ever land in the log itself. With
    make, the log must not exist yet and is created; without it, a missing log raises FileNotFoundError and nothing is created."""
    fd = os.open(LOG_NAME, LOG_FLAGS | ((os.O_CREAT | os.O_EXCL) if make else 0), 0o600, dir_fd=state_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OSError("the log is not an ordinary file with one name")
    except OSError:
        os.close(fd)
        raise
    return fd


def open_holding(base):
    """Get everything a move needs, before anything is moved: a new holding folder under state/no-ai-wall-strays and the open log.
    Returns (holding folder descriptor, log descriptor, holding folder name). Raises OSError when any piece is a link or the wrong kind.

    It looks first and creates second. Every piece that already exists is opened and checked before a single folder or file is made, so
    a refusal over something that was already there leaves the disk as it was. Each piece is opened by its name inside the open folder
    above it, never by a full path, so swapping a folder for a link after the check cannot send a move or a log line somewhere else."""
    held = []
    keep = []
    try:
        base_fd = os.open(base, DIR_FLAGS)
        held.append(base_fd)
        state_fd = strays_fd = log_fd = -1
        try:
            state_fd = open_folder("state", base_fd)
            held.append(state_fd)
        except FileNotFoundError:
            pass
        if state_fd >= 0:
            try:
                strays_fd = open_folder(STRAYS_NAME, state_fd)
                held.append(strays_fd)
            except FileNotFoundError:
                pass
            try:
                log_fd = open_log(state_fd)
                keep.append(log_fd)
            except FileNotFoundError:
                pass
        if state_fd < 0:
            state_fd = open_folder("state", base_fd, make=True)
            held.append(state_fd)
        if strays_fd < 0:
            strays_fd = open_folder(STRAYS_NAME, state_fd, make=True)
            held.append(strays_fd)
        if log_fd < 0:
            log_fd = open_log(state_fd, make=True)
            keep.append(log_fd)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        n = 1
        while True:
            label = stamp if n == 1 else "%s-%d" % (stamp, n)
            try:
                os.mkdir(label, 0o700, dir_fd=strays_fd)
                break
            except FileExistsError:
                n += 1
        dest_fd = open_folder(label, strays_fd)
        return dest_fd, log_fd, label
    except OSError:
        for fd in keep:
            os.close(fd)
        raise
    finally:
        for fd in held:
            os.close(fd)


def main(argv):
    if argv not in ([], ["--apply"]):
        sys.stderr.write(USAGE)
        return 2
    apply_ = argv == ["--apply"]

    wall = os.path.dirname(os.path.realpath(__file__))
    base = os.path.dirname(wall)
    if os.path.basename(wall) != "no-ai-wall" or os.path.basename(base) != ".claude":
        return refuse("this is not an installed wall folder")
    for name in MANIFEST:
        if not os.path.lexists(os.path.join(wall, name)):
            return refuse("a wall file is missing, so this is not an installed wall")
    if has_git_entry(wall):
        return refuse("this folder sits in a source checkout")
    state = os.path.join(base, "state")
    # An early, plain answer for the common case. The real protection is in open_holding, which opens each folder without following a link.
    if apply_ and (os.path.islink(state) or os.path.islink(os.path.join(state, STRAYS_NAME))):
        return refuse("a state folder is a symbolic link")

    code = 0
    strays = []
    with os.scandir(wall) as it:
        entries = sorted(it, key=lambda e: e.name)
    for e in entries:
        name = e.name
        shown = json.dumps(name)
        if name in MANIFEST:
            try:
                st = os.lstat(e.path)
                ok = stat.S_ISREG(st.st_mode)
            except OSError:
                ok = False
            if not ok:
                print("WARNING manifest name %s is not a regular file" % shown)
                code = 1
            continue
        if name in IN_FLIGHT:
            print("left alone %s (an install may be running)" % shown)
            continue
        try:
            st = os.lstat(e.path)
        except OSError as exc:
            print("WARNING %s could not be looked at (%s); it may have gone while this ran" % (shown, type(exc).__name__))
            code = 1
            continue
        strays.append((name, shown, kind_of(st.st_mode), st.st_size, st))

    if not strays:
        print("WALL CLEAN")
        return code

    moved = failed = 0
    rel = ""
    dest_fd = log_fd = -1
    if apply_:
        try:
            dest_fd, log_fd, stamp = open_holding(base)
        except OSError as exc:
            return refuse("the holding folder or the log cannot be used safely (%s), so nothing was moved" % type(exc).__name__)
        rel = "state/%s/%s" % (STRAYS_NAME, stamp)

    for name, shown, kind, size, st in strays:
        if not apply_:
            print("would move %s (%s, %d bytes)" % (shown, kind, size))
            continue
        digest = sha256_of(os.path.join(wall, name), st)
        try:
            os.rename(os.path.join(wall, name), name, dst_dir_fd=dest_fd)
        except OSError as exc:
            sys.stderr.write("could not move %s: %s\n" % (shown, type(exc).__name__))
            failed += 1
            code = 1
            continue
        moved += 1
        print("moved %s (%s, %d bytes)" % (shown, kind, size))
        line = "\t".join((time.strftime("%Y-%m-%dT%H:%M:%S"), "moved", shown, kind, str(size), digest, rel))
        try:
            os.write(log_fd, (line + "\n").encode("utf-8"))
        except OSError as exc:
            sys.stderr.write("could not write the log: %s\n" % type(exc).__name__)
            code = 1

    if not apply_:
        print("WALL STRAYS %d found, nothing moved (dry run; add --apply)" % len(strays))
    else:
        tail = ", %d could not be moved" % failed if failed else ""
        print("WALL STRAYS %d moved to %s%s" % (moved, rel, tail))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
