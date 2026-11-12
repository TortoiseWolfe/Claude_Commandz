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

Exit codes: 0 clean, or all moved, or dry run; 1 a warning or a failed move; 2 usage; 3 refused (nothing changed).
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
    strays_root = os.path.join(state, "no-ai-wall-strays")
    if apply_ and (os.path.islink(state) or os.path.islink(strays_root)):
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
        except OSError:
            continue
        strays.append((name, shown, kind_of(st.st_mode), st.st_size, st))

    if not strays:
        print("WALL CLEAN")
        return code

    moved = failed = 0
    stamp = rel = dest_dir = None
    if apply_:
        os.makedirs(strays_root, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        n = 1
        while True:
            label = stamp if n == 1 else "%s-%d" % (stamp, n)
            try:
                os.mkdir(os.path.join(strays_root, label), 0o700)
                break
            except FileExistsError:
                n += 1
        stamp = label
        dest_dir = os.path.join(strays_root, stamp)
        rel = "state/no-ai-wall-strays/" + stamp
        log_path = os.path.join(state, "no-ai-wall-cleanup.log")

    for name, shown, kind, size, st in strays:
        if not apply_:
            print("would move %s (%s, %d bytes)" % (shown, kind, size))
            continue
        digest = sha256_of(os.path.join(wall, name), st)
        try:
            os.rename(os.path.join(wall, name), os.path.join(dest_dir, name))
        except OSError as exc:
            sys.stderr.write("could not move %s: %s\n" % (shown, type(exc).__name__))
            failed += 1
            code = 1
            continue
        moved += 1
        print("moved %s (%s, %d bytes)" % (shown, kind, size))
        line = "\t".join((time.strftime("%Y-%m-%dT%H:%M:%S"), "moved", shown, kind, str(size), digest, rel))
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
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
