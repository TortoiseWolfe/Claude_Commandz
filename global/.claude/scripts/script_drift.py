#!/usr/bin/env python3
"""script_drift.py: compare the scripts and hooks installed in ~/.claude with the ones on main in the Claude_Commandz repo. Read-only.

Use when: the hub page says installed scripts differ from main, or before an install, to see which installed files hold changes the
repo does not (the next install would overwrite them) and which repo files were never installed.

  script_drift.py [--repo DIR] [--ref REF] [--live DIR] [--json] [--write FILE]

For each folder (scripts, hooks) it looks at the files directly inside it (no subfolders, no tests) and reports:
  differs     the file is in both places with different bytes (installed copy may hold unsaved work, or the repo is newer);
  only live   an installed file the repo has no copy of (private scripts live here on purpose, so this is a count, not an alarm);
  only repo   a file on main that was never installed.
It reads main with git (git show REF:path), never the checkout's working files, so a checkout on a side branch cannot hide drift.
--write FILE saves the summary as JSON (the hub page reads it: hub_digest.py drift_line); the daily run is openclaw_status_refresh.sh.
Exit code 0 always when the comparison ran, 2 when git could not read the ref.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
DIRS = ("scripts", "hooks")
SUFFIXES = {".py", ".sh", ".js", ".cjs", ".prompt", ".ref"}
SKIP_NAMES = {"TOOLS.md"}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def repo_files(repo, ref, sub):
    """-> {name: bytes} for the files directly inside global/.claude/<sub> on ref; raises RuntimeError when git cannot read it."""
    folder = f"global/.claude/{sub}"
    listed = git(repo, "ls-tree", "--name-only", f"{ref}:{folder}")
    if listed.returncode != 0:
        raise RuntimeError(f"git cannot read {ref}:{folder}")
    out = {}
    for name in listed.stdout.decode().splitlines():
        if Path(name).suffix not in SUFFIXES or name in SKIP_NAMES:
            continue
        blob = git(repo, "show", f"{ref}:{folder}/{name}")
        if blob.returncode == 0:   # a subfolder named like a script would fail here and is skipped
            out[name] = blob.stdout
    return out


def live_files(live, sub):
    folder = Path(live) / sub
    out = {}
    if not folder.is_dir():
        return out
    for p in sorted(folder.iterdir()):
        if p.is_file() and not p.name.startswith(".") and p.suffix in SUFFIXES and p.name not in SKIP_NAMES:
            try:
                out[p.name] = p.read_bytes()
            except OSError:
                continue
    return out


def compare(repo, ref, live):
    """-> {"ref": ref, "checked": N, "differs": [...], "only_live": [...], "only_repo": [...]}; names are "scripts/x.py"."""
    result = {"ref": ref, "checked": 0, "differs": [], "only_live": [], "only_repo": []}
    for sub in DIRS:
        in_repo = repo_files(repo, ref, sub)
        on_disk = live_files(live, sub)
        for name in sorted(set(in_repo) | set(on_disk)):
            tag = f"{sub}/{name}"
            if name in in_repo and name in on_disk:
                result["checked"] += 1
                if in_repo[name] != on_disk[name]:
                    result["differs"].append(tag)
            elif name in on_disk:
                result["only_live"].append(tag)
            else:
                result["only_repo"].append(tag)
    return result


def summary_line(result):
    d = result["differs"]
    shown = ", ".join(d[:5]) + (f" and {len(d) - 5} more" if len(d) > 5 else "")
    head = f"{len(d)} of {result['checked']} installed files differ from {result['ref']}"
    tail = f"; {len(result['only_live'])} installed files have no copy in the repo, {len(result['only_repo'])} repo files are not installed"
    return head + (f" ({shown})" if d else "") + tail


def main(argv=None):
    p = argparse.ArgumentParser(prog="script_drift.py", description=(__doc__ or "").split("\n")[0])
    p.add_argument("--repo", default=str(HOME / "repos/Claude_Commandz"))
    p.add_argument("--ref", default="origin/main")
    p.add_argument("--live", default=str(HOME / ".claude"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", metavar="FILE")
    a = p.parse_args(argv)
    try:
        result = compare(a.repo, a.ref, a.live)
    except RuntimeError as exc:
        print(f"script_drift: {exc}", file=sys.stderr)
        return 2
    result["at"] = int(time.time())
    if a.write:
        tmp = Path(a.write + ".tmp")
        tmp.write_text(json.dumps(result))
        tmp.replace(a.write)
    print(json.dumps(result) if a.json else summary_line(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
