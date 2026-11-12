#!/usr/bin/env python3
"""Install the merged fleet scripts from origin/main into ~/.claude/scripts (Claude_Commandz issue 265).

A person runs this in their own terminal, never a model session: the files it replaces are the ones the fleet runs live, and a
session may not copy over them. One command (it fetches first, then runs the copy of this script that is on origin/main, so the
file you run is the file that was merged):

    git -C ~/repos/Claude_Commandz fetch -q origin main && python3 -I <(git -C ~/repos/Claude_Commandz show origin/main:global/.claude/scripts/install_scripts.py) --no-fetch

What it does, in order. Every file comes out of git (the commit), never the working tree.
  1. fetches origin main (unless --no-fetch) and reads the tree global/.claude/scripts at origin/main;
  2. compares each file with its live copy and prints a summary BEFORE changing anything:
       new      the live folder has no such file
       changed  the live copy equals an OLDER version of that file in origin/main's history, so replacing it loses nothing
       same     the live copy already equals origin/main (counted; --list-same names them)
       DRIFT    the live copy equals NO version in origin/main's history: a local edit nobody committed. It is listed and
                nothing is copied for that file (commit the edit or discard it, then run again)
       HELD     the file reads the wall from ~/.claude/no-ai-wall/ (lane.sh does) and that folder lacks one of the wall's three
                files: not copied, and the exact global/.claude/no-ai-wall/install.sh command to run first is printed
     Files that belong to the no-AI wall (any file whose name has "no_ai" or "no-ai" in it, the settings files) are never
     read, compared, copied or backed up: they are listed as skipped and left alone;
  3. asks "Install? [y/N]" (skip with --yes; --dry-run stops here);
  4. backs up every live file it will replace into ~/.claude/state/scripts-install-backups/<date>-<commit>/ with an undo.sh;
  5. copies the new and changed files (atomic replace, executable bit kept) and prints the last line:
       INSTALL OK <commit> new <n> changed <n> same <n> drift <n>
       INSTALL FAILED <reason> after undoing itself

Before anything else it also checks the one hook file the repo cannot hold (its header tells private history): the case block of the live
~/.claude/hooks/block-outbound.sh (the arms that hand Gmail drafts to draft-recipient-guard.py and refuse the send tools) must equal the case
block of global/.claude/hooks/tests/fixtures/block-outbound.case.sh at the ref, which is what CI tests. If they differ, or the live wrapper has
no case block, or the fixture cannot be read, it refuses and changes nothing (exit 2); it never prints the wrapper. With no live wrapper at all
it says "wrapper check: not run" and goes on, so a machine without the hook is not stopped but is told it was not checked.
The hand copy of the guard it hands drafts to is checked the same way: the live ~/.claude/hooks/draft-recipient-guard.py must be byte for
byte global/.claude/hooks/draft-recipient-guard.py at the ref (a hand copy someone forgot to update is the risk). A difference, an unreadable
repo copy, or a live wrapper with no live guard beside it refuses (exit 2) and changes nothing; with neither file it says "guard check: not run".
The guard is only compared, never printed and never installed here (hooks are still copied by a person).

Options: --repo DIR (default ~/repos/Claude_Commandz), --home DIR (default ~/.claude), --ref REF (default origin/main),
--no-fetch, --dry-run, --yes, --list-same. Exit codes: 0 done (or nothing to do), 1 done but some file drifted, 2 could not run
(nothing was changed), 3 not confirmed (nothing was changed).
"""
import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys
import time

TREE = "global/.claude/scripts"
WRAPPER = "hooks/block-outbound.sh"  # under --home
WRAPPER_FIXTURE = "global/.claude/hooks/tests/fixtures/block-outbound.case.sh"  # in the repo
GUARD = "hooks/draft-recipient-guard.py"  # under --home, the hand copy
GUARD_SRC = "global/.claude/hooks/draft-recipient-guard.py"  # in the repo
BACKUPS = "state/scripts-install-backups"


def die(msg, code=2):
    print(f"install_scripts: {msg}", file=sys.stderr)
    print("INSTALL FAILED " + msg + "; nothing had been changed yet")
    sys.exit(code)


def git(repo, *args, data=None, check=True):
    r = subprocess.run(["git", "-C", repo, *args], input=data, capture_output=True)
    if check and r.returncode != 0:
        die(f"git {' '.join(args[:3])} failed: {r.stderr.decode(errors='replace').strip()[:200]}")
    return r


def is_wall_file(rel):
    """The wall's own files and the settings files: never touched, never read. Matched on the name, so a moved or renamed
    copy is still left alone."""
    base = os.path.basename(rel).lower()
    if "no_ai" in base or "no-ai" in base:
        return True
    return base.startswith("settings") and base.endswith(".json")


def unsafe_path(rel):
    """A path that could not be written into the tab-separated undo manifest or could climb out of the scripts folder."""
    if not rel or rel.startswith("/") or any(c in rel for c in "\n\r\t\0"):
        return True
    return any(part in ("", ".", "..") for part in rel.split("/"))


def skip_junk(rel):
    return "__pycache__" in rel.split("/") or rel.endswith(".pyc")


def blob_id(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def tree_files(repo, ref):
    """{path relative to the scripts folder: (mode, blob id)} at ref."""
    out = git(repo, "ls-tree", "-r", "-z", ref, "--", TREE + "/").stdout.decode()
    files = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, sha = meta.split()
        rel = path[len(TREE) + 1:]
        files[rel] = (mode, kind, sha)
    return files


def history_ids(repo, ref, rel):
    """Every content (blob id) the file has had on ref's history."""
    path = f"{TREE}/{rel}"
    commits = git(repo, "log", "--format=%H", ref, "--", path).stdout.decode().split()
    if not commits:
        return set()
    lines = "".join(f"{c}:{path}\n" for c in commits).encode()
    out = git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)", data=lines).stdout.decode().splitlines()
    return {ln.split()[0] for ln in out if ln.endswith(" blob")}


def read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        return b"<directory>"


WALL_HOME_FILES = ("no_ai_wall.py", "no_ai_wall.sh", "no-ai-wall-guard.py")
# A script that reads the wall from its home folder (lane.sh does: it refuses to start any lane without it).
WALL_USE = re.compile(rb"no-ai-wall/(no_ai_wall\.(sh|py)|no-ai-wall-guard\.py)")


def wall_home_missing(home):
    """The wall files that are not in <home>/no-ai-wall (empty list = the wall's home folder is complete)."""
    out = []
    for f in WALL_HOME_FILES:
        p = os.path.join(home, "no-ai-wall", f)
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            out.append(f)
    return out


def classify(repo, ref, home):
    live_dir = os.path.join(home, "scripts")
    plan = {"new": [], "changed": [], "same": [], "drift": [], "wall": [], "other": [], "held": []}
    missing = wall_home_missing(home)
    for rel, (mode, kind, sha) in sorted(tree_files(repo, ref).items()):
        if skip_junk(rel):
            continue
        if unsafe_path(rel):
            plan["other"].append(rel.encode("unicode_escape").decode())  # never copied, never written to the undo manifest
            continue
        if is_wall_file(rel):
            plan["wall"].append(rel)
            continue
        if kind != "blob" or mode not in ("100644", "100755"):
            plan["other"].append(rel)  # a symlink or submodule: not copied
            continue
        live = read(os.path.join(live_dir, rel))
        if live is None:
            kind_of = "new"
        elif blob_id(live) == sha:
            plan["same"].append(rel)
            continue
        elif blob_id(live) in history_ids(repo, ref, rel):
            kind_of = "changed"
        else:
            plan["drift"].append(rel)
            continue
        # A script that needs the wall's home folder is not installed while that folder is incomplete: lane.sh without it
        # refuses to start every lane (6 Nov, NX-01).
        if missing and WALL_USE.search(git(repo, "cat-file", "blob", sha).stdout):
            plan["held"].append(rel)
            continue
        plan[kind_of].append((rel, mode, sha))
    return plan


def names(items):
    return [i[0] if isinstance(i, tuple) else i for i in items]


def print_wall_help(sha):
    print()
    print("The no-AI wall's home folder is incomplete, so every script that reads it was held back (a lane.sh without it refuses to")
    print("start any lane). A person installs the wall first, in their own terminal, with this command:")
    print()
    print(f"    bash <(git -C ~/repos/Claude_Commandz show {sha}:global/.claude/no-ai-wall/install.sh) {sha}")
    print()
    print("What it changes: it puts the wall's three files in ~/.claude/no-ai-wall/, points the wall's hook entry in")
    print("~/.claude/settings.json at that folder (only the path changes), removes the three old copies from ~/.claude/hooks and")
    print("~/.claude/scripts, brings the other global files up to date with the repo's usual install step, then runs a smoke test and")
    print("undoes itself if that fails (backup and undo.sh under ~/.claude/state/no-ai-wall-backups/). Run this script again after it.")


def print_summary(plan, short, subject, list_same, home="~/.claude"):
    wall_missing = wall_home_missing(home)
    home_label = "~/.claude"
    print(f"origin/main {short}: {subject}")
    print()
    for rel in names(plan["new"]):
        print(f"new      {rel}")
    for rel in names(plan["changed"]):
        print(f"changed  {rel}  (the live copy is an older version from the repo's history)")
    for rel in plan["drift"]:
        print(f"DRIFT    {rel}  (the live copy matches no version in the repo's history: a local edit; copy nothing, commit or discard it)")
    for rel in plan["held"]:
        print(f"HELD     {rel}  (it reads the no-AI wall from {home_label}/no-ai-wall, which is missing {', '.join(wall_missing)}; not copied)")
    if list_same:
        for rel in plan["same"]:
            print(f"same     {rel}")
    for rel in plan["wall"]:
        print(f"skipped  {rel}  (a no-AI wall or settings file: never touched)")
    for rel in plan["other"]:
        print(f"skipped  {rel}  (not a plain file)")
    print()
    print(f"{len(plan['new'])} new, {len(plan['changed'])} changed, {len(plan['same'])} same"
          f"{'' if list_same else ' (--list-same names them)'}, {len(plan['drift'])} drifted (left alone), "
          f"{len(plan['wall']) + len(plan['other'])} skipped, {len(plan['held'])} held")


UNDO = """#!/usr/bin/env bash
# Undo of the script install of {sha} made {ts}. Run it in your own terminal: bash {bk}/undo.sh
set -u
LIVE={live}
BK={bk}
rc=0
while IFS=$'\\t' read -r act rel; do
  [ -n "$rel" ] || continue
  case "$act" in
    restore) mkdir -p "$(dirname "$LIVE/$rel")" && cp -p "$BK/files/$rel" "$LIVE/$rel" || {{ echo "could not restore $rel" >&2; rc=1; }} ;;
    remove) rm -f "$LIVE/$rel" || rc=1 ;;
  esac
done <"$BK/manifest.tsv"
[ "$rc" = 0 ] && echo "undone: the scripts folder is as it was before the install" || echo "undo finished with errors" >&2
exit "$rc"
"""


def backup(home, plan, sha):
    ts = time.strftime("%Y%m%d-%H%M%S")
    bk = os.path.join(home, BACKUPS, f"{ts}-{sha[:7]}")
    n = 1
    while os.path.exists(bk):
        n += 1
        bk = os.path.join(home, BACKUPS, f"{ts}-{sha[:7]}-{n}")
    rows = []
    for item in plan["new"] + plan["changed"]:
        rel = item[0]
        live = os.path.join(home, "scripts", rel)
        key = f"scripts/{rel}"
        if os.path.lexists(live):
            dest = os.path.join(bk, "files", key)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(live, "rb") as f, open(dest, "wb") as g:
                g.write(f.read())
            os.chmod(dest, os.stat(live).st_mode & 0o777)
            rows.append(("restore", key))
        else:
            rows.append(("remove", key))
    os.makedirs(bk, exist_ok=True)
    with open(os.path.join(bk, "manifest.tsv"), "w") as f:
        for act, key in rows:
            f.write(f"{act}\t{key}\n")
    undo = os.path.join(bk, "undo.sh")
    with open(undo, "w") as f:
        f.write(UNDO.format(sha=sha, ts=ts, bk=shlex.quote(bk), live=shlex.quote(home)))
    os.chmod(undo, 0o755)
    return bk, rows


def put(repo, home, rel, mode, sha):
    r = git(repo, "cat-file", "blob", sha, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"cannot read {rel} from git")
    data = r.stdout
    dest = os.path.join(home, "scripts", rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".install-new"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o755 if mode == "100755" else 0o644)
    os.replace(tmp, dest)


def undo(home, bk, rows):
    ok = True
    for act, key in rows:
        live = os.path.join(home, key)
        try:
            if act == "restore":
                with open(os.path.join(bk, "files", key), "rb") as f:
                    data = f.read()
                with open(live, "wb") as g:
                    g.write(data)
            elif os.path.lexists(live):
                os.remove(live)
        except OSError:
            ok = False
    return ok


def case_block(text):
    """The `case "$tool" in ... esac` arm list of a block-outbound hook (the part that hands Gmail drafts to the guard), or None."""
    a = text.find('case "$tool" in')
    if a < 0:
        return None
    b = text.find("esac", a)
    return None if b < 0 else text[a:b + 4]


def wrapper_check(repo, sha, home):
    """The line to print, or None to stop: refuses (dies) when the live wrapper's case block is not the fixture's. Never prints the wrapper."""
    live = os.path.join(home, WRAPPER)
    if not os.path.isfile(live):
        return f"wrapper check: not run ({live} does not exist, so nothing was compared with the fixture)"
    r = git(repo, "show", f"{sha}:{WRAPPER_FIXTURE}", check=False)
    if r.returncode != 0:
        die(f"{live} exists but {WRAPPER_FIXTURE} cannot be read at {sha[:7]}, so the two cannot be compared; fix the repo first")
    want = case_block(r.stdout.decode(errors="replace"))
    try:
        with open(live, errors="replace") as f:
            have = case_block(f.read())
    except OSError as e:
        die(f"cannot read {live}: {e.strerror}")
    if want is None:
        die(f"{WRAPPER_FIXTURE} at {sha[:7]} has no case block; fix the fixture first")
    if have is None:
        die(f"the live {live} has no case block, so it does not hand Gmail drafts to the guard; fix it before installing")
    if have != want:
        die(f"the case block of the live {live} differs from {WRAPPER_FIXTURE} at {sha[:7]}: CI tests the fixture, so make them the same "
            f"(edit the fixture in a PR, or the live hook) before installing")
    return f"wrapper check: ok (the live {WRAPPER} case block equals the fixture at {sha[:7]})"


GATE_RE = re.compile(rb'^SELF\s*=\s*["\'][^"\']*@example\.(?:com|org|net)["\']', re.M)
GATE_ENV = "INSTALL_GATE_OVERRIDE"  # never set by a script; a person sets it to install a known-placeholder tree on purpose


def gate_check(repo, sha):
    """Refuse (exit 4) while the ref's draft guard still carries the placeholder address the history scrub left and does not
    read the private identity file at run time: installing it would put a guard into ~/.claude that refuses his own drafts.
    Lifts itself once the ref's guard reads ~/.config/afa/identity-guard.json. Never prints the guard."""
    r = git(repo, "show", f"{sha}:{GUARD_SRC}", check=False)
    if r.returncode != 0:
        return "gate: not run (no draft guard at this ref)"
    if GATE_RE.search(r.stdout) and b"identity-guard.json" not in r.stdout:
        if os.environ.get(GATE_ENV) == "1":
            return f"gate: OVERRIDDEN by {GATE_ENV}=1 (the draft guard at {sha[:7]} still has its placeholder address)"
        die(f"the scrubbed repo is not ready to install: {GUARD_SRC} at {sha[:7]} still carries a placeholder address and does not read "
            f"~/.config/afa/identity-guard.json at run time, so the installed guard would refuse his own drafts. "
            f"Land the run-time read first (and the LiveHook draft-recipient test), then install", code=4)
    return f"gate: ok (the draft guard at {sha[:7]} reads its private values at run time)"


def guard_check(repo, sha, home):
    """The line to print: refuses (dies) when the hand copy of the draft guard is not the repo's, or is missing beside a live wrapper.
    Never prints the guard."""
    live = os.path.join(home, GUARD)
    if not os.path.isfile(live):
        if os.path.isfile(os.path.join(home, WRAPPER)):
            die(f"{os.path.join(home, WRAPPER)} exists but {live} does not, so every Gmail draft would be refused; copy {GUARD_SRC} there first")
        return f"guard check: not run ({live} does not exist, so nothing was compared with the repo)"
    r = git(repo, "show", f"{sha}:{GUARD_SRC}", check=False)
    if r.returncode != 0:
        die(f"{live} exists but {GUARD_SRC} cannot be read at {sha[:7]}, so the two cannot be compared; fix the repo first")
    try:
        with open(live, "rb") as f:
            have = f.read()
    except OSError as e:
        die(f"cannot read {live}: {e.strerror}")
    if have != r.stdout:
        die(f"the hand copy {live} differs from {GUARD_SRC} at {sha[:7]}: copy the repo's file over it (cp, by hand) before installing")
    return f"guard check: ok (the hand copy {GUARD} equals the repo's at {sha[:7]})"


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--repo", default=os.path.expanduser("~/repos/Claude_Commandz"))
    ap.add_argument("--home", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--list-same", action="store_true")
    a = ap.parse_args(argv)
    repo, home = a.repo, a.home

    if not os.path.exists(os.path.join(repo, ".git")):
        die(f"{repo} is not a git checkout (use --repo)")
    if git(repo, "rev-parse", "--show-object-format", check=False).stdout.decode().strip() not in ("sha1", ""):
        die("this repo does not use sha1 object ids")
    if not a.no_fetch and a.ref.startswith("origin/"):
        r = git(repo, "fetch", "-q", "origin", a.ref.split("/", 1)[1], check=False)
        if r.returncode != 0:
            die("could not fetch origin: " + r.stderr.decode(errors="replace").strip()[:200])
    r = git(repo, "rev-parse", "--verify", "--quiet", f"{a.ref}^{{commit}}", check=False)
    if r.returncode != 0:
        die(f"unknown ref {a.ref}")
    sha = r.stdout.decode().strip()
    subject = git(repo, "log", "-1", "--format=%s", sha).stdout.decode().strip()

    print(gate_check(repo, sha))
    print(wrapper_check(repo, sha, home))
    print(guard_check(repo, sha, home))
    plan = classify(repo, sha, home)
    print_summary(plan, sha[:7], subject, a.list_same, home)
    if plan["held"]:
        print_wall_help(sha)
    todo = plan["new"] + plan["changed"]
    drift = len(plan["drift"]) + len(plan["held"])  # both leave something uninstalled: exit 1
    tail = (f"new {len(plan['new'])} changed {len(plan['changed'])} same {len(plan['same'])} "
            f"drift {len(plan['drift'])} held {len(plan['held'])}")

    if not todo:
        print(f"INSTALL OK {sha[:7]} {tail} (nothing to copy)")
        return 1 if drift else 0
    if a.dry_run:
        print(f"DRY RUN {sha[:7]} {tail}: nothing was changed")
        return 1 if drift else 0
    if not a.yes:
        try:
            answer = input("Install? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("not confirmed: nothing was changed")
            return 3

    bk, rows = backup(home, plan, sha)
    try:
        for rel, mode, blob in todo:
            put(repo, home, rel, mode, blob)
    except Exception as e:  # any failure: put every file back, then say so
        ok = undo(home, bk, rows)
        print("INSTALL FAILED " + f"{type(e).__name__}: {e}"[:160] + (" after undoing itself" if ok else f"; THE UNDO ALSO FAILED, run: bash {bk}/undo.sh"))
        return 2
    print(f"backup:  {bk}")
    print(f"undo:    bash {bk}/undo.sh")
    print(f"INSTALL OK {sha[:7]} {tail}")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
