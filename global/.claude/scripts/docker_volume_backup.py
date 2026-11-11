#!/usr/bin/env python3
"""docker_volume_backup.py: copy Docker's named volumes to a folder before anything restarts Docker Desktop or WSL.

Why: on 11 Nov 2026 the engine's volume store was empty after a restart and nothing could be recovered, because Docker Desktop's own
volume-backup schedule was empty and no copy existed anywhere. This script is the copy. It is called by wsl_root_check.py right before it
restarts Docker Desktop, and can be run by hand before any other restart.

    docker_volume_backup.py [--dest DIR] [--docker docker|docker.exe] [--image IMAGE] [--keep N] [--budget SECONDS]
                            [--skip REGEX] [--windows-paths] [--dry-run]

What it does:
- Lists volumes with `docker volume ls`. Anonymous volumes (a 64-character hex name) and cache volumes are skipped (DEFAULT_SKIP: node_modules,
  pnpm stores, hf_cache, model downloads, the runner's nested Docker store; add more with --skip, a regular expression on the name).
- Copies each remaining volume to <dest>/<stamp>/<name>.tgz with a throwaway container that mounts the volume READ-ONLY and runs tar. It uses
  an image that is already on the machine (the first of --image, alpine, busybox that `docker image inspect` finds), so it never pulls.
- Each container gets a memory and CPU cap and a timeout, and the whole run has a time budget, so a restart is never held up for long.
- Nothing is deleted, pruned, stopped or moved except this script's own older backup sets: only the newest --keep sets (default 3) stay.
- A live database volume is copied while its server runs, so the copy is crash-consistent (what the database would see after a power cut),
  not a clean dump.

Exit codes, for the caller: 0 = backed up, nothing to back up, or the engine did not answer (there is nothing to read; the reason is printed);
1 = at least one volume could not be copied (the caller should not restart on top of that); 2 = bad arguments.
Standard library only. The last line printed is a one-line summary the caller can log.
"""
import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
import time

ANON = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_SKIP = r"(node_modules|[_-]pnpm|pnpm[_-]store|[_-]cache$|^hf_cache$|^ollama-data$|^openclaw-local-models$|dind-data$)"
IMAGE_CANDIDATES = ["alpine", "busybox"]
MIN_FREE_BYTES = 5 * 1024 ** 3
PER_VOLUME_S = 600
DEFAULT_BUDGET_S = 1200
DEFAULT_KEEP = 3


def default_dest():
    return os.path.join(os.path.expanduser("~"), ".cache", "docker-volume-backups")


def run(argv, timeout):
    """-> (returncode, text). A missing program or a timeout is (127, '') / (124, '')."""
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, cwd="/")
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""


def list_volumes(docker, runner=run):
    """-> (names, error). error is a short reason when the engine does not answer."""
    rc, out = runner(["timeout", "20", docker, "volume", "ls", "-q"], 30)
    if rc != 0:
        return [], "engine did not answer (exit %d)" % rc
    return [l.strip() for l in out.splitlines() if l.strip()], ""


def pick_image(docker, wanted, runner=run):
    for img in ([wanted] if wanted else []) + IMAGE_CANDIDATES:
        rc, _ = runner(["timeout", "20", docker, "image", "inspect", img], 30)
        if rc == 0:
            return img
    return ""


def wanted_volumes(names, skip_re):
    keep, skipped = [], []
    for n in names:
        if ANON.match(n) or re.search(skip_re, n):
            skipped.append(n)
        else:
            keep.append(n)
    return keep, skipped


def host_path(path, windows):
    """The folder as the Docker client wants to see it: a Windows path for docker.exe."""
    if not windows:
        return path
    rc, out = run(["wslpath", "-w", path], 10)
    return out.strip() if rc == 0 and out.strip() else path


def backup_one(docker, image, name, set_dir, windows, runner=run, timeout=PER_VOLUME_S):
    """Copy one volume. -> (ok, detail)."""
    target = os.path.join(set_dir, name + ".tgz")
    part = target + ".part"
    argv = [docker, "run", "--rm", "--memory", "512m", "--cpus", "2", "--network", "none",
            "-v", name + ":/v:ro", "-v", host_path(set_dir, windows) + ":/b",
            image, "sh", "-c", "tar czf /b/%s.tgz.part -C /v . && echo ok" % name]
    rc, out = runner(["timeout", str(timeout), *argv], timeout + 30)
    if rc == 0 and os.path.exists(part):
        os.replace(part, target)
        return True, "%d bytes" % os.path.getsize(target)
    try:
        os.remove(part)
    except OSError:
        pass
    return False, "exit %d %s" % (rc, " ".join(out.split())[-120:])


def prune_old_sets(dest, keep):
    """Remove this script's own oldest sets (folders named YYYYmmdd-HHMMSS) past --keep. Returns the names removed."""
    try:
        sets = sorted(d for d in os.listdir(dest) if re.fullmatch(r"\d{8}-\d{6}", d) and os.path.isdir(os.path.join(dest, d)))
    except OSError:
        return []
    gone = sets[:-keep] if keep > 0 and len(sets) > keep else []
    for d in gone:
        shutil.rmtree(os.path.join(dest, d), ignore_errors=True)
    return gone


def main(argv=None, runner=run, now=time.time, out=sys.stdout):
    ap = argparse.ArgumentParser(description="Back up Docker's named volumes before a restart.")
    ap.add_argument("--dest", default=default_dest())
    ap.add_argument("--docker", default="docker")
    ap.add_argument("--image", default="")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET_S)
    ap.add_argument("--skip", default=DEFAULT_SKIP)
    ap.add_argument("--windows-paths", action="store_true", help="pass the backup folder to docker.exe as a Windows path")
    ap.add_argument("--dry-run", action="store_true", help="list what would be copied; copy nothing")
    try:
        a = ap.parse_args(argv)
        re.compile(a.skip)
    except (SystemExit, re.error) as e:
        print("docker_volume_backup: bad arguments (%s)" % (e if isinstance(e, re.error) else "see usage"), file=out)
        return 2

    names, err = list_volumes(a.docker, runner)
    if err:
        print("docker_volume_backup: skipped, %s; nothing to read, nothing changed" % err, file=out)
        return 0
    keep, skipped = wanted_volumes(names, a.skip)
    if not keep:
        print("docker_volume_backup: nothing to back up (%d volumes, %d anonymous or cache, all skipped)" % (len(names), len(skipped)), file=out)
        return 0
    if a.dry_run:
        for n in keep:
            print("would copy " + n, file=out)
        print("docker_volume_backup: dry run, %d to copy, %d skipped" % (len(keep), len(skipped)), file=out)
        return 0

    image = pick_image(a.docker, a.image, runner)
    if not image:
        print("docker_volume_backup: FAILED, no local alpine or busybox image to run tar in (it never pulls); %d volume(s) not copied" % len(keep), file=out)
        return 1
    os.makedirs(a.dest, exist_ok=True)
    free = shutil.disk_usage(a.dest).free
    if free < MIN_FREE_BYTES:
        print("docker_volume_backup: FAILED, only %d MB free under %s; %d volume(s) not copied" % (free // 1048576, a.dest, len(keep)), file=out)
        return 1
    stamp = datetime.datetime.fromtimestamp(now()).strftime("%Y%m%d-%H%M%S")
    set_dir = os.path.join(a.dest, stamp)
    os.makedirs(set_dir, exist_ok=True)

    started, done, failed = now(), [], []
    for n in keep:
        left = a.budget - (now() - started)
        if left < 30:
            failed.append((n, "time budget used up"))
            continue
        ok, detail = backup_one(a.docker, image, n, set_dir, a.windows_paths, runner, timeout=int(min(PER_VOLUME_S, left)))
        (done if ok else failed).append((n, detail))
        print("%s %s: %s" % ("copied" if ok else "FAILED", n, detail), file=out)

    gone = prune_old_sets(a.dest, a.keep) if not failed else []
    if failed:
        print("docker_volume_backup: FAILED, %d copied, %d not copied (%s) in %s" % (len(done), len(failed), ", ".join(n for n, _ in failed), set_dir), file=out)
        return 1
    print("docker_volume_backup: %d volume(s) copied to %s, %d skipped%s" % (
        len(done), set_dir, len(skipped), (", removed %d older set(s)" % len(gone)) if gone else ""), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
