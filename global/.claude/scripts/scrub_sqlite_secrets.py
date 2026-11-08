#!/usr/bin/env python3
"""Replace every real env-file secret value inside a SQLite database with <redacted:KEY>, using SQL, never a byte rewrite.

usage: scrub_sqlite_secrets.py [--repos DIR] [--secrets-dir DIR] [--ignore FILE] [--dry-run] [--no-vacuum] [--keep-backup] DATABASE

OpenCode keeps every session (messages, tool calls, events) in ~/.local/share/opencode/opencode.db. A password a command printed stays readable
there for the next lane, and scrub_known_secrets.py cannot touch it: a byte rewrite would corrupt a database. This script does what a database
allows. In order:
  1. Looks for the secret values (the same ones scrub_known_secrets.py uses) in the database file and its write-ahead log with grep -F. None found:
     it stops, and the database is not opened for writing.
  2. Refuses (exit 3) while any other process holds the database, its -wal or its -shm file open. It never ends a process.
  3. Copies the database with SQLite's own backup call (a copy that is consistent even with the log active) next to it, mode 0600.
  4. In one transaction, rewrites every text value that holds a secret (UPDATE ... SET column = <value with the secret replaced> WHERE rowid = ...).
     Raw, JSON-escaped, URL-encoded and base64 forms are all replaced, so JSON columns stay valid JSON. Checks again that nothing else opened the
     database, then commits. A failure rolls back and leaves the file as it was.
  5. PRAGMA integrity_check must say ok; VACUUM then rebuilds the file so the old pages that held a secret are gone, and the log is truncated.
  6. Scans the database and its log again. Anything still found is an error (exit 4) and the backup is kept. When the rescan finds nothing, the
     backup, which holds the old values, is deleted (--keep-backup keeps it). A deleted file's disk blocks are not wiped by this script.
Prints key names and counts only, never a value. Exit codes: 0 clean or scrubbed, 2 no values to look for, 3 database in use, 4 rescan found a
value, 5 not enough disk space, 6 integrity check or update failed (after a failed update nothing changed and no backup is left; after a failed check the backup is kept, copy it back over the database to restore), 7 a path is a symlink or not a regular file.
"""
import argparse
import glob
import os
import re
import shutil
import stat
import sqlite3
import sys
import tempfile
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import known_secrets_scan as ks  # noqa: E402
from scrub_known_secrets import needles, shortlist  # noqa: E402


def holders(paths):
    """PIDs other than this one that have any of the files open (a file descriptor or a memory map), read from /proc."""
    want = {os.path.realpath(p) for p in paths}
    me = os.getpid()
    found = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            for fd in os.listdir("/proc/%s/fd" % pid):
                try:
                    if os.path.realpath(os.readlink("/proc/%s/fd/%s" % (pid, fd))) in want:
                        found.add(int(pid))
                except OSError:
                    pass
            with open("/proc/%s/maps" % pid) as fh:
                for line in fh:
                    parts = line.split(None, 5)
                    if len(parts) == 6 and parts[5].strip() in want:
                        found.add(int(pid))
        except OSError:
            continue   # the process ended, or belongs to someone else
    return sorted(found)


def q(name):
    return '"%s"' % name.replace('"', '""')


def unsafe_paths(paths):
    """Return {path: (device, inode)} for each path that exists, or the first path that is a symlink or not a regular file (lstat, no following)."""
    ids = {}
    for p in paths:
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(st.st_mode):
            return p
        ids[p] = (st.st_dev, st.st_ino)
    return ids


def db_files(db):
    return [p for p in (db, db + "-wal") if os.path.exists(p)]


def matches_in_files(db, nds):
    """The key names still present in the database file or its log (byte scan, reading in chunks)."""
    pairs = [(n, key) for n, _, key in nds]
    out = set()
    for p in db_files(db):
        if os.path.getsize(p):
            out.update(ks.contains(p, pairs))
    return sorted(out)


def rewrite(con, nds, dry):
    """Returns (Counter of key -> values replaced, rows changed, tables skipped)."""
    text_forms = []
    for n, rep, key in nds:
        try:
            text_forms.append((n.decode("utf-8"), rep.decode(), key))
        except UnicodeDecodeError:
            continue   # a form that is not valid text cannot be inside a text column
    text_forms.sort(key=lambda t: -len(t[0]))
    by_form = {f: (rep, key) for f, rep, key in text_forms}
    rx = re.compile("|".join(re.escape(f) for f, _, _ in text_forms))
    counts, rows, skipped = Counter(), 0, []

    def sub(m):
        rep, key = by_form[m.group(0)]
        counts[key] += 1
        return rep

    tables = con.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    for table, sql in tables:
        sql_u = (sql or "").upper()
        if sql_u.startswith("CREATE VIRTUAL") or "WITHOUT ROWID" in sql_u:
            skipped.append(table)
            continue
        cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % q(table))]
        for col in cols:
            changes = []
            for rowid, val in con.execute("SELECT rowid, %s FROM %s WHERE typeof(%s)='text'" % (q(col), q(table), q(col))):
                new = rx.sub(sub, val)
                if new != val:
                    changes.append((new, rowid))
            if changes and not dry:
                con.executemany("UPDATE %s SET %s=? WHERE rowid=?" % (q(table), q(col)), changes)
            rows += len(changes)
    return counts, rows, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description="Redact collected secret values inside a SQLite database.")
    ap.add_argument("database")
    ap.add_argument("--repos", default=os.path.expanduser("~/repos"))
    ap.add_argument("--secrets-dir", default=os.environ.get("KNOWN_SECRETS_DIR") or os.path.expanduser("~/.config/afa/secrets"))
    ap.add_argument("--ignore", default=os.path.expanduser("~/.config/known-secrets/ignore.txt"))
    ap.add_argument("--dry-run", action="store_true", help="count what would change; write nothing, make no backup")
    ap.add_argument("--no-vacuum", action="store_true", help="skip VACUUM (the rescan then fails if old pages still hold a value)")
    ap.add_argument("--keep-backup", action="store_true")
    a = ap.parse_args(argv)
    db = os.path.abspath(a.database)
    if not os.path.lexists(db):
        return 0
    pinned = unsafe_paths([db, db + "-wal", db + "-shm"])
    if isinstance(pinned, str):
        print("sqlite scrub: %s is a symlink or not a regular file; refusing, nothing changed" % pinned, file=sys.stderr)
        return 7
    db_id = pinned[db]
    secrets = ks.collect(a.repos, ks.load_ignore(a.ignore), secrets_dir=a.secrets_dir)
    if not secrets:
        print("sqlite scrub: no secret values to look for", file=sys.stderr)
        return 2
    nds = needles(secrets)
    if not shortlist(db_files(db), sorted({n for n, _, _ in nds})):
        print("sqlite scrub: %s holds no secret values" % os.path.basename(db))
        return 0
    files = [db, db + "-wal", db + "-shm"]
    busy = holders(files)
    if busy and not a.dry_run:
        print("sqlite scrub: %d other process(es) hold %s open (pids %s); nothing changed, try again when they have ended"
              % (len(busy), os.path.basename(db), ", ".join(map(str, busy))), file=sys.stderr)
        return 3
    size = sum(os.path.getsize(p) for p in db_files(db))
    if not a.dry_run and shutil.disk_usage(os.path.dirname(db)).free < 3 * size:
        print("sqlite scrub: less than 3x the database size is free (%d bytes needed); nothing changed" % (3 * size), file=sys.stderr)
        return 5

    backup = None
    if not a.dry_run:
        backup = "%s.pre-scrub-%s-%d" % (db, time.strftime("%Y%m%d-%H%M%S"), os.getpid())
        os.close(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        src = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        dst = sqlite3.connect(backup)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

    con = sqlite3.connect(db, isolation_level=None, timeout=10)
    try:
        con.execute("PRAGMA secure_delete=ON")   # freed pages are zeroed whatever the build default is; VACUUM below is the second layer
        con.execute("BEGIN IMMEDIATE")
        counts, rows, skipped = rewrite(con, nds, a.dry_run)
        if a.dry_run:
            con.execute("ROLLBACK")
        else:
            moved = unsafe_paths([db])
            if isinstance(moved, str) or moved.get(db) != db_id:
                con.execute("ROLLBACK")
                print("sqlite scrub: %s was replaced during the rewrite; rolled back, nothing changed" % os.path.basename(db), file=sys.stderr)
                os.unlink(backup)
                return 7
            late = holders(files)
            if late:
                con.execute("ROLLBACK")
                print("sqlite scrub: a process (pid %s) opened %s during the rewrite; rolled back, nothing changed" % (late[0], os.path.basename(db)), file=sys.stderr)
                os.unlink(backup)
                return 3
            con.execute("COMMIT")
    except Exception as e:   # before COMMIT, the database is unchanged after a rollback, so the backup (which holds the old values) is not needed
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        if backup and os.path.exists(backup):
            os.unlink(backup)
        print("sqlite scrub: the rewrite failed and was rolled back (%s); nothing changed" % type(e).__name__, file=sys.stderr)
        return 6
    finally:
        con.close()

    verb = "would scrub" if a.dry_run else "scrubbed"
    print("sqlite scrub: %s %d value(s) in %d row(s) of %s" % (verb, sum(counts.values()), rows, os.path.basename(db)))
    for key, n in counts.most_common():
        print("  %s: %d" % (key, n))
    if skipped:
        print("  skipped tables (virtual or without rowid): %s" % ", ".join(skipped), file=sys.stderr)
    if a.dry_run:
        return 0

    con = sqlite3.connect(db, isolation_level=None, timeout=10)
    try:
        check = con.execute("PRAGMA integrity_check").fetchall()
        if check != [("ok",)]:
            print("sqlite scrub: integrity_check failed after the rewrite; backup kept at %s" % backup, file=sys.stderr)
            return 6
        if not a.no_vacuum:
            con.execute("VACUUM")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            print("sqlite scrub: integrity_check failed after VACUUM; backup kept at %s" % backup, file=sys.stderr)
            return 6
    finally:
        con.close()

    left = matches_in_files(db, nds)
    if left:
        print("sqlite scrub: the rescan still finds %d key(s): %s; backup kept at %s" % (len(left), ", ".join(k for k in left), backup), file=sys.stderr)
        return 4
    print("sqlite scrub: integrity_check ok; rescan finds 0 values")
    if a.keep_backup:
        print("sqlite scrub: backup kept at %s (it still holds the old values)" % backup)
    else:
        os.unlink(backup)
        # The live database is clean, so backups left by earlier failed runs (they hold old values) are no longer needed.
        # --keep-backup is the opt-in to keep them.
        old = [f for f in glob.glob(glob.escape(db) + ".pre-scrub-*") if os.path.isfile(f) and not os.path.islink(f)]
        for f in old:
            os.unlink(f)
        if old:
            print("sqlite scrub: removed %d older backup(s) from earlier runs" % len(old))
    return 0


if __name__ == "__main__":
    sys.exit(main())
