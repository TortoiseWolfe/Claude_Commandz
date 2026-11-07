#!/usr/bin/env python3
"""One secret-scan command: gitleaks plus known_secrets_scan.py, names only. No model.

Before a push the rule is two checks: gitleaks (finds secrets by shape) and known_secrets_scan.py (finds this machine's real
values, so a bare password gets caught too). This runs both the same way every time and prints only the rule, the file and
the line, never the matched text.

  secret_scan.py                scan what is in the git index of the current repo (what a commit would contain)
  secret_scan.py --index        same, spelled out
  secret_scan.py PATH [PATH]    scan these files or folders as they are

Output, one line per finding:
  gitleaks  <rule>  <file>:<line>
  known     <env file> <KEY>: <path>        (known_secrets_scan.py prints key names, never values)
and a last line: "secret_scan: clean" or "secret_scan: N finding(s)".
Exit codes: 0 = clean; 1 = a finding; 2 = a check could not run (a check that did not run is never "clean").

gitleaks runs from the local program when there is one (PATH, then ~/.local/bin), else in Docker with a timeout and a read-only
mount. Reviewed false positives stay in the repo's .gitleaksignore, which gitleaks reads from the scanned folder.
Test hooks: GITLEAKS and KNOWN_SECRETS_SCAN name the two programs.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT = 600


def find_gitleaks():
    forced = os.environ.get("GITLEAKS")
    if forced:
        return [forced]
    found = shutil.which("gitleaks") or str(Path.home() / ".local/bin/gitleaks")
    return [found] if Path(found).exists() else None


def run_gitleaks(target, workdir):
    """Findings as (rule, file, line) from gitleaks run over TARGET (a folder or file); None when it could not run."""
    report = Path(workdir) / "gitleaks-report.json"
    local = find_gitleaks()
    flags = ["--no-banner", "--redact", "--report-format", "json"]
    t0 = Path(target).resolve()
    # Run from inside the scanned folder and name it ".", so a finding's file is a relative path: that is how the reviewed
    # false positives in .gitleaksignore are written ("<file>:<rule>:<line>"), and an absolute path would never match them.
    cwd = t0 if t0.is_dir() else t0.parent
    sub = "." if t0.is_dir() else t0.name
    if local:
        cmd = local + ["dir", sub] + flags + ["--report-path", str(report)]
    else:
        if not shutil.which("docker"):
            print("secret_scan: no gitleaks program and no docker", file=sys.stderr)
            return None
        cmd = ["timeout", str(TIMEOUT), "docker", "run", "--rm", "-w", "/scan", "-v", "%s:/scan:ro" % cwd,
               "-v", "%s:/out" % workdir, "zricethezav/gitleaks:latest", "dir", sub] + flags + ["--report-path", "/out/gitleaks-report.json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT + 30, cwd=str(cwd))
    except (OSError, subprocess.TimeoutExpired) as e:
        print("secret_scan: gitleaks did not run: %s" % e, file=sys.stderr)
        return None
    if r.returncode not in (0, 1) or not report.exists():
        # never echo its output: a failing run can quote the text it matched
        print("secret_scan: gitleaks failed (exit %d)" % r.returncode, file=sys.stderr)
        return None
    try:
        items = json.loads(report.read_text() or "[]")
    except ValueError:
        print("secret_scan: gitleaks wrote a report that is not JSON", file=sys.stderr)
        return None
    return [(str(i.get("RuleID", "?")), str(i.get("File", "?")), i.get("StartLine", 0)) for i in items]


def run_known(target):
    """Lines from known_secrets_scan.py (names only) and its exit code; None when it could not run."""
    prog = os.environ.get("KNOWN_SECRETS_SCAN") or str(Path.home() / ".claude/scripts/known_secrets_scan.py")
    if not Path(prog).exists():
        print("secret_scan: known_secrets_scan.py not found", file=sys.stderr)
        return None
    try:
        r = subprocess.run([sys.executable, "-I", prog, str(target)], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        print("secret_scan: known_secrets_scan.py did not run: %s" % e, file=sys.stderr)
        return None
    if r.returncode == 0:
        return []
    if r.returncode == 1:
        return [ln for ln in r.stdout.splitlines() if ln.strip()]
    print("secret_scan: known_secrets_scan.py failed (exit %d)" % r.returncode, file=sys.stderr)
    return None


def scan(target, workdir):
    g = run_gitleaks(target, workdir)
    k = run_known(target)
    if g is None or k is None:
        return 2
    for rule, f, line in g:
        print("gitleaks  %s  %s:%s" % (rule, f, line))
    for ln in k:
        print("known     %s" % ln)
    n = len(g) + len(k)
    print("secret_scan: clean" if n == 0 else "secret_scan: %d finding(s)" % n)
    return 1 if n else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--index", action="store_true")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="secret-scan-") as work:
        os.chmod(work, 0o755)  # the Docker route mounts it
        if a.paths and not a.index:
            worst = 0
            for p in a.paths:
                if not Path(p).exists():
                    print("secret_scan: no such path: %s" % p, file=sys.stderr)
                    return 2
                worst = max(worst, scan(p, work))
            return worst
        snap = Path(work) / "index"
        snap.mkdir()
        r = subprocess.run(["git", "checkout-index", "-a", "--prefix=%s/" % snap], capture_output=True, text=True)
        if r.returncode != 0:
            print("secret_scan: could not read the git index (is this a git repo?)", file=sys.stderr)
            return 2
        return scan(snap, work)


if __name__ == "__main__":
    sys.exit(main())
