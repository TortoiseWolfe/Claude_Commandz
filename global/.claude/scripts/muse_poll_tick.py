#!/usr/bin/env python3
"""Safely close one open hub inbox item selected by a literal substring."""

from __future__ import annotations

import datetime
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path


OPEN_PREFIX = b"- [ ] "


def without_line_ending(line: bytes) -> tuple[bytes, bytes]:
    if line.endswith(b"\r\n"):
        return line[:-2], b"\r\n"
    if line.endswith((b"\n", b"\r")):
        return line[:-1], line[-1:]
    return line, b""


def create_backup(path: Path, original: bytes, mode: int) -> Path:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    prefix = f"{path.name}.bak.{stamp}.{os.getpid()}"
    for suffix in range(100):
        name = prefix if suffix == 0 else f"{prefix}.{suffix}"
        backup = path.with_name(name)
        try:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
        return backup
    raise FileExistsError(f"could not create a unique backup for {path}")


def replace_atomically(path: Path, updated: bytes, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tick.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def prune_expired_backups(path: Path) -> None:
    pattern = re.compile(re.escape(f"{path.name}.bak.") + r"\d{8}T\d{6}\.\d{6}Z\.\d+(?:\.\d+)?")
    cutoff = time.time() - 24 * 60 * 60
    try:
        entries = os.scandir(path.parent)
    except OSError as error:
        print(f"warning: could not check old backups: {error}", file=sys.stderr)
        return
    with entries:
        for entry in entries:
            if pattern.fullmatch(entry.name) is None:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                    os.unlink(entry.path)
            except FileNotFoundError:
                pass
            except OSError as error:
                print(f"warning: could not remove expired backup {entry.name}: {error}", file=sys.stderr)


def tick(path: Path, needle: str, note: str) -> int:
    if not needle or "\n" in needle or "\r" in needle:
        print("the match text must be non-empty and fit on one line", file=sys.stderr)
        return 2
    if not note.strip():
        print("the done note must not be empty", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"no open item matches {needle!r}")
        return 1

    needle_bytes = needle.encode("utf-8")
    note_bytes = note.encode("utf-8")
    original = path.read_bytes()
    lines = original.splitlines(keepends=True)
    matches = [index for index, line in enumerate(lines) if line.startswith(OPEN_PREFIX) and needle_bytes in line]
    if len(matches) != 1:
        if matches:
            print(f"more than one open item matches {needle!r}; no changes made:")
            for index in matches:
                print(without_line_ending(lines[index])[0].decode("utf-8", errors="replace"))
        else:
            print(f"no open item matches {needle!r}")
        return 1

    index = matches[0]
    body, ending = without_line_ending(lines[index])
    stamp = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    done = b" | done " + stamp.encode("ascii") + b": " + note_bytes
    lines[index] = b"- [x] " + body[len(OPEN_PREFIX):] + done + ending
    updated = b"".join(lines)

    mode = stat.S_IMODE(path.stat().st_mode)
    backup = create_backup(path, original, mode)
    replace_atomically(path, updated, mode)
    prune_expired_backups(path)
    print(f"ticked 1; backup: {backup.name}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: muse_poll_tick.py <board> <unique substring> <done note>", file=sys.stderr)
        return 2
    return tick(Path(argv[1]), argv[2], argv[3])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
