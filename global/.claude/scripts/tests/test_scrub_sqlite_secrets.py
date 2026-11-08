"""scrub_sqlite_secrets.py: secret values inside a SQLite database are replaced with SQL, the database stays sound, and it never
changes a database another process holds open.

Every value here is made up at run time; the database is a small fixture in a throwaway folder."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scrub_sqlite_secrets.py")
PW = "Pq7" * 6
QUOTED = 'Ab1"' + "Cd2" * 4          # a value JSON has to escape
TOK = "Yz9" * 22
ENV = "." + "env"


def mkdb(path, extra=None):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT)")
    con.execute("CREATE TABLE event (id INTEGER PRIMARY KEY, data TEXT, n INTEGER)")
    rows = [
        ("m1", "s1", json.dumps({"out": "Login: jon / %s" % PW})),
        ("m2", "s1", json.dumps({"out": "smtp %s" % QUOTED})),
        ("m3", "s2", json.dumps({"out": "build finished, 12 tests passed"})),
        ("m4", "s2", "post https://example.invalid/hooks/%s done" % TOK),
    ]
    con.executemany("INSERT INTO message VALUES (?,?,?)", rows)
    con.execute("INSERT INTO event (data, n) VALUES (?, 7)", ("token %s" % TOK,))
    if extra:
        extra(con)
    con.commit()
    con.close()


class ScrubSqlite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repos = os.path.join(self.tmp.name, "repos")
        os.makedirs(os.path.join(self.repos, "app"))
        with open(os.path.join(self.repos, "app", ENV), "w") as fh:
            fh.write("ADMIN_PASSWORD=%s\nOPS_TOKEN=%s\nSMTP_PASSWORD='%s'\nPORT=3000\n" % (PW, TOK, QUOTED))
        self.secrets = os.path.join(self.tmp.name, "secrets")
        os.makedirs(self.secrets)
        self.db = os.path.join(self.tmp.name, "opencode.db")

    def run_cli(self, *extra):
        return subprocess.run([sys.executable, SCRIPT, "--repos", self.repos, "--secrets-dir", self.secrets, "--ignore", "/nonexistent",
                               *extra, self.db], capture_output=True, text=True)

    def files_bytes(self):
        out = b""
        for suffix in ("", "-wal"):
            p = self.db + suffix
            if os.path.exists(p):
                out += open(p, "rb").read()
        return out

    def rows(self, sql):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def test_replaces_every_form_with_sql_and_leaves_no_trace_in_the_file(self):
        mkdb(self.db)
        self.assertIn(PW.encode(), self.files_bytes())
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        raw = self.files_bytes()
        for leak in (PW, TOK, QUOTED, json.dumps(QUOTED)[1:-1]):
            self.assertNotIn(leak.encode(), raw, "a value is still in the database file or its log")
            self.assertNotIn(leak, r.stdout + r.stderr)
        got = dict(self.rows("SELECT id, data FROM message"))
        self.assertEqual(json.loads(got["m1"])["out"], "Login: jon / <redacted:ADMIN_PASSWORD>")   # still valid JSON
        self.assertEqual(json.loads(got["m2"])["out"], "smtp <redacted:SMTP_PASSWORD>")
        self.assertEqual(got["m3"], json.dumps({"out": "build finished, 12 tests passed"}))        # untouched row, byte for byte
        self.assertIn("<redacted:OPS_TOKEN>", got["m4"])
        self.assertEqual(self.rows("SELECT data, n FROM event"), [("token <redacted:OPS_TOKEN>", 7)])  # other columns kept
        self.assertEqual(self.rows("PRAGMA integrity_check"), [("ok",)])
        self.assertIn("rescan finds 0", r.stdout)
        self.assertEqual([f for f in os.listdir(self.tmp.name) if ".pre-scrub-" in f], [], "the backup holds old values and is removed after a clean rescan")

    def test_a_long_value_in_overflow_pages_leaves_no_old_copy_in_free_pages(self):
        # A row of ~60 KB spills into overflow pages; after an UPDATE the old pages go to the free list with the old text still in them.
        # Only the rebuild (VACUUM) clears them. Real OpenCode rows (whole tool outputs) are like this.
        big = ("line of output " * 2000) + " password was " + PW + " " + ("more output " * 2000)
        mkdb(self.db, lambda c: c.execute("INSERT INTO message VALUES ('big', 's3', ?)", (big,)))
        self.assertIn(PW.encode(), self.files_bytes())
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(PW.encode(), self.files_bytes(), "an old copy of a value is still in a free page")
        self.assertEqual(self.rows("PRAGMA integrity_check"), [("ok",)])

    def test_a_symlinked_database_is_refused_and_the_target_is_untouched(self):
        real = os.path.join(self.tmp.name, "elsewhere.db")
        mkdb(real)
        before = open(real, "rb").read()
        os.symlink(real, self.db)
        r = self.run_cli()
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertEqual(open(real, "rb").read(), before)
        self.assertEqual([f for f in os.listdir(self.tmp.name) if ".pre-scrub-" in f], [])

    def test_a_symlinked_log_file_is_refused(self):
        mkdb(self.db)
        target = os.path.join(self.tmp.name, "other-wal")
        open(target, "wb").close()
        if os.path.exists(self.db + "-wal"):
            os.unlink(self.db + "-wal")
        os.symlink(target, self.db + "-wal")
        r = self.run_cli()
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)

    def test_a_clean_run_removes_backups_left_by_earlier_runs_unless_asked_to_keep(self):
        mkdb(self.db)
        stale = self.db + ".pre-scrub-20000101-000000"
        with open(stale, "wb") as fh:
            fh.write(PW.encode())
        r = self.run_cli("--keep-backup")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(stale), "--keep-backup must leave earlier backups alone")
        mkdb(os.path.join(self.tmp.name, "other.db"))   # a second database in the same folder is not ours to touch
        open(os.path.join(self.tmp.name, "other.db.pre-scrub-1"), "wb").close()
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO message VALUES ('m9', 's9', ?)", ("again %s" % PW,))
        con.commit()
        con.close()
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(stale), "a clean database no longer needs old backups")
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "other.db.pre-scrub-1")))

    def test_a_database_with_no_secret_is_left_untouched(self):
        mkdb(self.db, lambda c: (c.execute("DELETE FROM message WHERE id IN ('m1','m2','m4')"), c.execute("DELETE FROM event")))
        con = sqlite3.connect(self.db)
        con.execute("VACUUM")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
        before = open(self.db, "rb").read()
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("holds no secret values", r.stdout)
        self.assertEqual(open(self.db, "rb").read(), before)

    def test_refuses_while_another_process_holds_the_database_and_does_not_touch_it(self):
        mkdb(self.db)
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import sqlite3,sys\nc=sqlite3.connect(%r)\nc.execute('select count(*) from message').fetchall()\n"
                                   "print('ready',flush=True)\nsys.stdin.read()" % self.db],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "ready")
            before = self.files_bytes()
            r = self.run_cli()
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertIn(str(holder.pid), r.stderr)
            self.assertIsNone(holder.poll(), "the holder must not be ended")
            self.assertEqual(self.files_bytes(), before)
            self.assertEqual([f for f in os.listdir(self.tmp.name) if ".pre-scrub-" in f], [])
        finally:
            holder.stdin.close()
            holder.wait(timeout=10)
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)   # once the holder has ended it goes through
        self.assertNotIn(PW.encode(), self.files_bytes())

    def test_keep_backup_keeps_a_copy_that_restores_the_original(self):
        mkdb(self.db)
        original = dict(self.rows("SELECT id, data FROM message"))
        r = self.run_cli("--keep-backup")
        self.assertEqual(r.returncode, 0, r.stderr)
        backups = [f for f in os.listdir(self.tmp.name) if ".pre-scrub-" in f]
        self.assertEqual(len(backups), 1)
        path = os.path.join(self.tmp.name, backups[0])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        shutil.copy(path, self.db)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(self.db + suffix):
                os.unlink(self.db + suffix)
        self.assertEqual(dict(self.rows("SELECT id, data FROM message")), original)

    def test_dry_run_counts_and_changes_nothing(self):
        mkdb(self.db)
        before = self.files_bytes()
        r = self.run_cli("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would scrub", r.stdout)
        self.assertIn("ADMIN_PASSWORD: 1", r.stdout)
        self.assertEqual(self.files_bytes(), before)
        self.assertEqual([f for f in os.listdir(self.tmp.name) if ".pre-scrub-" in f], [])

    def test_a_value_it_cannot_reach_fails_closed_and_keeps_the_backup(self):
        # A table without a rowid and a blob column are not rewritten; the rescan must still catch the value and say so.
        def extra(c):
            c.execute("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID")
            c.execute("INSERT INTO kv VALUES ('a', ?)", ("x %s y" % PW,))
        mkdb(self.db, extra)
        r = self.run_cli()
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("ADMIN_PASSWORD", r.stderr)
        self.assertNotIn(PW, r.stdout + r.stderr)
        self.assertEqual(len([f for f in os.listdir(self.tmp.name) if ".pre-scrub-" in f]), 1, "the backup is kept when the rescan fails")

    def test_a_blob_holding_a_value_fails_closed(self):
        mkdb(self.db, lambda c: (c.execute("CREATE TABLE b (id INTEGER PRIMARY KEY, x BLOB)"),
                                 c.execute("INSERT INTO b (x) VALUES (?)", (("blob %s" % PW).encode(),))))
        r = self.run_cli()
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)

    def test_no_values_to_look_for_is_exit_2(self):
        mkdb(self.db)
        os.unlink(os.path.join(self.repos, "app", ENV))
        r = self.run_cli()
        self.assertEqual(r.returncode, 2)
        self.assertIn(PW.encode(), self.files_bytes())

    def test_a_missing_database_is_not_an_error(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0)

    def test_a_dry_run_never_blocks_a_writer(self):
        # 8 Nov incident: a dry run took the write lock for as long as it read the whole database, and a running lane's
        # OpenCode got "Failed to execute statement". The dry run is held mid-read here, and a second connection must still be able to write.
        import importlib.util
        import threading
        spec = importlib.util.spec_from_file_location("scrub_sqlite_secrets_under_test", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(os.path.abspath(SCRIPT)))
        spec.loader.exec_module(mod)
        mkdb(self.db)
        reading, release = threading.Event(), threading.Event()
        real_rewrite = mod.rewrite

        def slow_rewrite(con, nds, dry):
            reading.set()                    # the dry run is now inside its transaction
            release.wait(30)
            return real_rewrite(con, nds, dry)

        mod.rewrite = slow_rewrite
        result = {}
        t = threading.Thread(target=lambda: result.update(rc=mod.main(
            ["--repos", self.repos, "--secrets-dir", self.secrets, "--ignore", "/nonexistent", "--dry-run", self.db])))
        t.start()
        try:
            self.assertTrue(reading.wait(30), "the dry run never reached its read")
            writer = sqlite3.connect(self.db, timeout=1)
            try:
                writer.execute("INSERT INTO message VALUES ('w1', 's9', 'written while the dry run reads')")
                writer.commit()
            except sqlite3.OperationalError as e:
                self.fail("the dry run blocked a writer: %s" % e)
            finally:
                writer.close()
        finally:
            release.set()
            t.join(60)
        self.assertEqual(result.get("rc"), 0)
        self.assertEqual(self.rows("SELECT data FROM message WHERE id='w1'"), [("written while the dry run reads",)])

    def stand_in_opencode(self):
        """A process called opencode that holds no file: a symlink to sleep, started with that name."""
        # The name is made up and handed to the script, so a real OpenCode lane running on the host cannot change the result.
        self.prog = "ocstub%d" % os.getpid()
        link = os.path.join(self.tmp.name, "bin", self.prog)
        os.makedirs(os.path.dirname(link))
        os.symlink(shutil.which("bash"), link)   # not sleep: some sleep programs (uutils) refuse to run under another name
        proc = subprocess.Popen([link, "-c", "while :; do sleep 1; done"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc

    def test_a_real_run_waits_while_any_opencode_process_is_alive_even_if_it_holds_no_file(self):
        mkdb(self.db)
        self.stand_in_opencode()
        before = self.files_bytes()
        r = self.run_cli("--opencode-dir", self.tmp.name, "--opencode-program", self.prog)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("OpenCode process is alive", r.stderr)
        self.assertEqual(self.files_bytes(), before)
        self.assertEqual([f for f in os.listdir(self.tmp.name) if ".pre-scrub-" in f], [])
        # A database outside OpenCode's data folder (every other test) is not held back by a lane that is running elsewhere.
        r = self.run_cli("--opencode-dir", os.path.join(self.tmp.name, "bin"), "--opencode-program", self.prog)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(PW.encode(), self.files_bytes())

    def test_a_dry_run_goes_ahead_while_an_opencode_process_is_alive(self):
        mkdb(self.db)
        self.stand_in_opencode()
        r = self.run_cli("--opencode-dir", self.tmp.name, "--opencode-program", self.prog, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would scrub", r.stdout)

    def test_a_test_pointed_at_the_live_database_is_refused(self):
        home = os.path.join(self.tmp.name, "home")
        live = os.path.join(home, ".local", "share", "opencode")
        os.makedirs(live)
        self.db = os.path.join(live, "opencode.db")
        mkdb(self.db)
        before = self.files_bytes()
        env = dict(os.environ, HOME=home, PYTEST_CURRENT_TEST="tests/x.py::t (call)")
        r = subprocess.run([sys.executable, SCRIPT, "--repos", self.repos, "--secrets-dir", self.secrets, "--ignore", "/nonexistent", self.db],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertEqual(self.files_bytes(), before)
        env.pop("PYTEST_CURRENT_TEST")
        r = subprocess.run([sys.executable, SCRIPT, "--repos", self.repos, "--secrets-dir", self.secrets, "--ignore", "/nonexistent",
                            "--opencode-dir", self.tmp.name, "--opencode-program", "ocstub-none", self.db], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)   # outside a test, the same path is scrubbed as usual

    def test_no_test_file_names_the_live_opencode_folder(self):
        needle = "." + "local/share/" + "opencode"
        here = os.path.dirname(os.path.abspath(__file__))
        offenders = []
        for name in sorted(os.listdir(here)):
            if name.startswith("test_") and name.endswith(".py"):   # the shell tests build that path under a fake HOME of their own
                with open(os.path.join(here, name), encoding="utf-8", errors="replace") as fh:
                    for n, line in enumerate(fh, 1):
                        if needle in line and not line.lstrip().startswith("#"):
                            offenders.append("%s:%d" % (name, n))
        self.assertEqual(offenders, [], "a test names the live OpenCode folder; use a temp folder")


if __name__ == "__main__":
    unittest.main()
