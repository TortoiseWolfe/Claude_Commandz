#!/usr/bin/env python3
"""Install global/.claude/* from this repo into ~/.claude, one changed file at a time, never over a live edit.

The old install line (cp -a global/.claude/scripts/. ~/.claude/scripts/) copies every file, so it overwrites a live
file that was edited after its last commit, and one that is simply older than the repo for a reason nobody checked.
This copies only the files that changed between two commits, and refuses any file whose live copy matches neither
the old nor the new repo version (a live edit): it prints the names and leaves them alone.

  install_global.py                    files changed between the last installed commit and HEAD
  install_global.py --from A --to B    files changed between A and B (B defaults to HEAD)
  install_global.py --dry-run          print what would happen, change nothing
  install_global.py --status           list every live file that differs from HEAD: "stale" (it equals an earlier
                                       repo version, safe to reinstall) or "live edit" (commit it or discard it)
  install_global.py --include-stale    also reinstall the stale files that --status lists
  install_global.py --record [COMMIT]  set the last-installed commit without copying (default HEAD)

The last installed commit is kept in ~/.claude/state/install-global-commit. It moves forward only when nothing was
refused, so a refused file shows up again on the next run. __pycache__ and .pyc files are never copied, and a file
deleted in the repo is left alone in ~/.claude. Exit 0 = done, 1 = some file refused, 2 = could not run.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

TREES = ("scripts", "hooks", "workflows", "agents", "skills")  # global/.claude/<tree> -> ~/.claude/<tree>
STATE_NAME = "install-global-commit"


def die(msg):
    print(f"install_global: {msg}", file=sys.stderr)
    sys.exit(2)


GUARD_SRC = "global/.claude/hooks/draft-recipient-guard.py"
GATE_RE = re.compile(rb'^SELF\s*=\s*["\'][^"\']*@example\.(?:com|org|net)["\']', re.M)


def gate_check(repo, commit):
    """Exit 4 while the commit's draft guard still carries the placeholder address and does not read ~/.config/afa/identity-guard.json
    at run time (see install_scripts.py gate_check). INSTALL_GATE_OVERRIDE=1 is a person's switch, never a script's."""
    r = subprocess.run(["git", "-C", repo, "show", f"{commit}:{GUARD_SRC}"], capture_output=True)
    if r.returncode != 0 or not GATE_RE.search(r.stdout) or b"identity-guard.json" in r.stdout:
        return
    if os.environ.get("INSTALL_GATE_OVERRIDE") == "1":
        print(f"install_global: gate OVERRIDDEN (the draft guard at {commit[:7]} still has its placeholder address)", file=sys.stderr)
        return
    print(f"install_global: the scrubbed repo is not ready to install: {GUARD_SRC} at {commit[:7]} still carries a placeholder address "
          f"and does not read ~/.config/afa/identity-guard.json at run time. Land the run-time read first.", file=sys.stderr)
    sys.exit(4)


def git(repo, *args, raw=False):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    if r.returncode != 0:
        die(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout if raw else r.stdout.decode()


def resolve(repo, ref):
    return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def skip(path):
    return "__pycache__" in path.split("/") or path.endswith(".pyc")


def blob(repo, commit, path):
    """File content at a commit, or None when the commit has no such file."""
    r = subprocess.run(["git", "-C", repo, "show", f"{commit}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def live_path(home, repo_path):
    rel = repo_path[len("global/.claude/"):]
    return os.path.join(home, rel)


def read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def changed(repo, old, new):
    out = git(repo, "diff", "--name-only", "-z", "--diff-filter=ACMR", old, new, "--",
              *[f"global/.claude/{t}" for t in TREES], raw=True).decode()
    return sorted(p for p in out.split("\0") if p and not skip(p))


def tracked(repo, commit):
    out = git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--",
              *[f"global/.claude/{t}" for t in TREES], raw=True).decode()
    return sorted(p for p in out.split("\0") if p and not skip(p))


def earlier_versions(repo, path):
    """Every committed content of a file, as a set of blob ids."""
    ids = set()
    for c in git(repo, "log", "--format=%H", "--", path).split():
        r = subprocess.run(["git", "-C", repo, "rev-parse", f"{c}:{path}"], capture_output=True)  # a deleting commit has none
        if r.returncode == 0:
            ids.add(r.stdout.decode().strip())
    return ids


def blob_id(repo, data):
    r = subprocess.run(["git", "-C", repo, "hash-object", "--stdin"], input=data, capture_output=True)
    return r.stdout.decode().strip()


def copy(repo, commit, path, dest):
    data = blob(repo, commit, path)
    mode = git(repo, "ls-tree", commit, "--", path).split()[0]
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".install-new"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o755 if mode == "100755" else 0o644)
    os.replace(tmp, dest)


def status(repo, home, new):
    stale, edited = [], []
    for p in tracked(repo, new):
        live, want = read(live_path(home, p)), blob(repo, new, p)
        if live is None or live == want:
            continue
        (stale if blob_id(repo, live) in earlier_versions(repo, p) else edited).append(p)
    for p in stale:
        print(f"stale      {p}  (equals an earlier repo version; reinstall is safe)")
    for p in edited:
        print(f"live edit  {p}  (matches no repo version; commit it or discard it)")
    if not stale and not edited:
        print("every live file that exists matches the repo")
    return 1 if edited else 0


def main(argv=None):
    default_repo = os.path.expanduser("~/repos/Claude_Commandz")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=default_repo)
    ap.add_argument("--home", default=os.path.expanduser("~/.claude"), help="the folder to install into")
    ap.add_argument("--from", dest="old")
    ap.add_argument("--to", dest="new", default="HEAD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--include-stale", action="store_true")
    ap.add_argument("--record", nargs="?", const="HEAD", metavar="COMMIT")
    a = ap.parse_args(argv)
    repo = a.repo
    if not os.path.isdir(os.path.join(repo, ".git")) and not os.path.isfile(os.path.join(repo, ".git")):
        die(f"{repo} is not a git checkout (use --repo)")
    state = os.path.join(a.home, "state", STATE_NAME)

    if a.record is not None:
        sha = resolve(repo, a.record)
        os.makedirs(os.path.dirname(state), exist_ok=True)
        with open(state, "w") as f:
            f.write(sha + "\n")
        print(f"recorded last install: {sha[:7]}")
        return 0

    new = resolve(repo, a.new)
    if not a.status:
        gate_check(repo, new)
    if a.status:
        return status(repo, a.home, new)

    if a.old:
        old = resolve(repo, a.old)
    else:
        rec = (read(state) or b"").decode().strip()
        if not rec:
            die(f"no last install recorded in {state}: pass --from <commit>, or run --status then --record")
        old = resolve(repo, rec)

    paths = set(changed(repo, old, new) if old != new else [])
    if a.include_stale:
        for p in tracked(repo, new):
            live, want = read(live_path(a.home, p)), blob(repo, new, p)
            if live is not None and live != want and blob_id(repo, live) in earlier_versions(repo, p):
                paths.add(p)

    copied, same, refused = [], [], []
    for p in sorted(paths):
        dest, want, was = live_path(a.home, p), blob(repo, new, p), blob(repo, old, p)
        live = read(dest)
        if live == want:
            same.append(p)
        elif live is None or live == was or (a.include_stale and blob_id(repo, live) in earlier_versions(repo, p)):
            if not a.dry_run:
                copy(repo, new, p, dest)
            copied.append(p)
        else:
            refused.append(p)

    verb = "would copy" if a.dry_run else "copied"
    for p in copied:
        print(f"{verb}  {p}")
    for p in refused:
        print(f"REFUSED  {p}  (the live copy matches neither {old[:7]} nor {new[:7]}: a live edit; commit it or discard it, then rerun)")
    print(f"{old[:7]}..{new[:7]}: {len(copied)} {verb}, {len(same)} already current, {len(refused)} refused")
    if not a.dry_run and not refused:
        os.makedirs(os.path.dirname(state), exist_ok=True)
        with open(state, "w") as f:
            f.write(new + "\n")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
