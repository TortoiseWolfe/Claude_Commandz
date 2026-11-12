"""Tests for scripts/shell_scrub_prefix.sh and scripts/shell_scrub_wrapper.py, run the way Claude Code runs them:
prefix.sh '<eval '...' < /dev/null && pwd -P >| cwdfile>', one argument. Every secret here is made up.
All state paths (off switch, heartbeat, wrapper, known-values cache) are redirected into a temp folder through env overrides."""
import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.realpath(os.path.join(HERE, ".."))
PREFIX = os.path.join(SCRIPTS, "shell_scrub_prefix.sh")
WRAPPER = os.path.join(SCRIPTS, "shell_scrub_wrapper.py")
FAKE = "FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe"
MASKED = "jwt_secret=<redacted:field jwt_secret>"
# Put together at run time from pieces, so a leak scanner reading this public file finds no key block in it.
PEM_WORDS = "PRIVATE" + " " + "KEY"
FAKE_PEM = ("-----BEGIN " + PEM_WORDS + "-----\nZmFrZWtleWJvZHlsaW5lMQ==\nZmFrZWtleWJvZHlsaW5lMg==\n-----END "
            + PEM_WORDS + "-----")
TIMINGS = {}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="shellscrub-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pids = []
        self.addCleanup(self.kill_pids)
        self.off = os.path.join(self.tmp, "off")
        self.heart = os.path.join(self.tmp, "heartbeat")
        self.cwdfile = os.path.join(self.tmp, "cwdfile")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("SHELL_SCRUB", "SECRET_SCRUBBER", "KNOWN_"))}
        self.env.update(SHELL_SCRUB_OFF_FILE=self.off, SHELL_SCRUB_HEARTBEAT=self.heart,
                        KNOWN_SECRETS_REPOS=os.path.join(self.tmp, "repos"), KNOWN_SECRETS_DIR=os.path.join(self.tmp, "sec"),
                        KNOWN_SECRETS_IGNORE=os.path.join(self.tmp, "ignore"), KNOWN_SECRETS_CACHE=os.path.join(self.tmp, "c.json"))
        os.makedirs(os.path.join(self.tmp, "repos"))

    def kill_pids(self):
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def arg(self, cmd):
        return "eval %s < /dev/null && pwd -P >| %s" % (shlex.quote(cmd), shlex.quote(self.cwdfile))

    def popen(self, cmd, raw=False, **env):
        e = dict(self.env, **env)
        return subprocess.Popen([PREFIX, cmd if raw else self.arg(cmd)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, env=e, cwd=self.tmp)

    def run_cmd(self, cmd, raw=False, **env):
        t = time.time()
        p = self.popen(cmd, raw, **env)
        out, err = p.communicate(timeout=60)
        return p.returncode, out.decode(), err.decode(), time.time() - t

    def wait_file(self, path, secs=5):
        end = time.time() + secs
        while time.time() < end:
            if os.path.exists(path) and open(path).read().strip():
                return open(path).read().strip()
            time.sleep(0.02)
        self.fail("file never appeared: " + path)

    def read_until_eof(self, fh, secs):
        """Read a pipe until EOF or the deadline; returns bytes."""
        got, end = b"", time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([fh], [], [], 0.1)
            if r:
                chunk = os.read(fh.fileno(), 65536)
                if not chunk:
                    break
                got += chunk
        return got


class Filtering(Base):
    def test_masked_at_exit_zero(self):
        rc, out, err, _ = self.run_cmd("echo 'hello'; echo '%s'" % (MASKED.replace("<redacted:field jwt_secret>", FAKE)))
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, "hello\n" + MASKED + "\n")
        self.assertNotIn(FAKE, out + err)

    def test_masked_at_exit_three_and_code_kept(self):
        rc, out, err, _ = self.run_cmd("echo 'jwt_secret=%s'; exit 3" % FAKE)
        self.assertEqual(rc, 3)
        self.assertEqual(out, MASKED + "\n")
        self.assertNotIn(FAKE, out + err)

    def test_stderr_masked(self):
        rc, out, err, _ = self.run_cmd("echo 'jwt_secret=%s' >&2; echo plain" % FAKE)
        self.assertEqual((rc, out, err), (0, "plain\n", MASKED + "\n"))

    def test_signal_death_code(self):
        rc, _, _, _ = self.run_cmd("kill -9 $$")
        self.assertEqual(rc, 137)

    def test_cwd_file_written_and_cd_carries(self):
        os.makedirs(os.path.join(self.tmp, "sub"))
        rc, out, err, _ = self.run_cmd("cd sub && pwd")
        self.assertEqual((rc, out.strip()), (0, os.path.join(self.tmp, "sub")), err)
        self.assertEqual(open(self.cwdfile).read().strip(), os.path.join(self.tmp, "sub"))

    def test_heredoc_pipe_and_substitution(self):
        cmd = ("cat <<'EOF'\nline one jwt_secret=%s \"quoted\" $HOME\nEOF\n"
               "echo 'A' | tr A B && echo \"n=$(echo abc | wc -c)\"\n" % FAKE)
        rc, out, err, _ = self.run_cmd(cmd)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, 'line one jwt_secret=<redacted:field jwt_secret> "quoted" $HOME\nB\nn=4\n')

    def test_multiline_private_key_masked_whole(self):
        rc, out, err, _ = self.run_cmd("printf 'before\\n%s\\nafter\\n'" % FAKE_PEM.replace("\n", "\\n"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, "before\n<redacted:private-key>\nafter\n")
        self.assertNotIn("ZmFrZWtleWJvZHk", out)

    def test_heartbeat_touched(self):
        self.assertFalse(os.path.exists(self.heart))
        self.run_cmd("true")
        self.assertTrue(os.path.exists(self.heart))
        self.assertLess(time.time() - os.path.getmtime(self.heart), 10)

    def test_stdin_is_inherited(self):
        p = subprocess.Popen([PREFIX, "cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=self.env)
        out, _ = p.communicate(b"x\n", timeout=20)
        self.assertEqual(out, b"x\n")     # a non-Bash-shaped command: passes through untouched

    def test_partial_output_arrives_before_the_command_ends(self):
        p = self.popen("echo first; sleep 2; echo second")
        t = time.time()
        r, _, _ = select.select([p.stdout], [], [], 1.0)
        self.assertTrue(r, "nothing within 1 s")
        self.assertEqual(os.read(p.stdout.fileno(), 100), b"first\n")
        self.assertLess(time.time() - t, 1.0)
        self.assertIsNone(p.poll())
        out, _ = p.communicate(timeout=20)
        self.assertEqual(out, b"second\n")

    def test_partial_line_without_newline_is_held_then_released(self):
        rc, out, err, _ = self.run_cmd("printf 'jwt_secret=%s'" % FAKE)
        self.assertEqual(out, "jwt_secret=<redacted:field jwt_secret>")

    def test_fail_closed_on_filter_error(self):
        rc, out, err, _ = self.run_cmd("echo 'jwt_secret=%s'; echo two; exit 5" % FAKE, SECRET_SCRUBBER_FAIL="1")
        self.assertEqual(rc, 5)
        self.assertEqual(out, "[withheld: the secret scrubber failed on this text]\n" * 2)
        self.assertNotIn(FAKE, out + err)

    def test_import_failure_withholds_and_keeps_exit_code(self):
        empty = os.path.join(self.tmp, "emptylib")
        os.makedirs(empty)
        rc, out, err, _ = self.run_cmd("echo 'jwt_secret=%s'; echo two >&2; exit 4" % FAKE, SHELL_SCRUB_LIB_DIR=empty)
        self.assertEqual(rc, 4)
        self.assertNotIn(FAKE, out + err)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertEqual(len(err.splitlines()), 1)
        for text in (out, err):
            self.assertIn("withheld", text)
            self.assertIn(self.off, text)

    def test_five_megabytes(self):
        n = 5000000
        t = time.time()
        rc, out, err, _ = self.run_cmd("yes 'INFO request served path=/api/items status=200 took=12ms' | head -c %d" % n)
        TIMINGS["5MB"] = time.time() - t
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(out), n)
        self.assertLess(TIMINGS["5MB"], 60)
        print("\n5 MB through the wrapper: %.2f s" % TIMINGS["5MB"])


class Background(Base):
    def test_background_sleep_returns_fast(self):
        pidfile = os.path.join(self.tmp, "pid")
        t = time.time()
        rc, out, err, _ = self.run_cmd("sleep 30 >/dev/null 2>&1 &\necho $! > %s" % shlex.quote(pidfile))
        elapsed = time.time() - t
        self.pids.append(int(open(pidfile).read()))
        self.assertEqual(rc, 0, err)
        self.assertLess(elapsed, 1.0)
        os.kill(self.pids[-1], 0)     # still alive

    def test_detached_child_holding_stdout_returns_fast_and_later_line_is_masked(self):
        pidfile = os.path.join(self.tmp, "pid")
        cmd = "( sleep 1; echo 'jwt_secret=%s' ) &\necho $! > %s\necho now" % (FAKE, shlex.quote(pidfile))
        t = time.time()
        p = self.popen(cmd)
        p.wait(timeout=10)
        elapsed = time.time() - t
        self.pids.append(int(open(pidfile).read()))
        self.assertLess(elapsed, 1.0, "the call waited for the background child")
        self.assertEqual(p.returncode, 0)
        os.kill(self.pids[-1], 0)     # not killed by the wrapper leaving
        got = self.read_until_eof(p.stdout, 6).decode()
        self.assertEqual(got, "now\n" + MASKED + "\n")
        self.assertNotIn(FAKE, got)

    def test_background_child_survives_a_closed_pipe(self):
        flag = os.path.join(self.tmp, "alive")
        cmd = "( sleep 1; echo x; echo y >&2; echo alive > %s ) &\necho started" % shlex.quote(flag)
        p = self.popen(cmd)
        p.wait(timeout=10)
        self.assertEqual(p.stdout.read(8), b"started\n")
        p.stdout.close()
        p.stderr.close()      # the reader is gone: the background child must not die from SIGPIPE
        self.wait_file(flag, 6)

    def test_sigterm_to_the_wrapper_ends_the_child(self):
        pidfile = os.path.join(self.tmp, "pid")
        p = self.popen("echo $$ > %s; exec sleep 30" % shlex.quote(pidfile))
        child = int(self.wait_file(pidfile))
        self.pids.append(child)
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=5)
        self.assertEqual(p.returncode, 143)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)


class Prefix(Base):
    def test_off_switch_gives_raw_pass_through(self):
        open(self.off, "w").close()
        rc, out, err, _ = self.run_cmd("echo 'jwt_secret=%s'; exit 2" % FAKE)
        self.assertEqual((rc, out), (2, "jwt_secret=%s\n" % FAKE))
        self.assertFalse(os.path.exists(self.heart), "the wrapper must not start when the switch is off")
        # The model reading this call is told, on the call itself, that nothing was masked.
        self.assertEqual(err.count("shell-scrub: the filter is switched off"), 1, err)
        self.assertIn("NOT being masked", err)

    def test_off_switch_says_nothing_on_a_command_that_is_not_a_bash_call(self):
        # Hooks, the status line and connector starts also come through the prefix; a line on their stderr could break them.
        open(self.off, "w").close()
        rc, out, err, _ = self.run_cmd("echo hello; exit 4", raw=True)
        self.assertEqual((rc, out, err), (4, "hello\n", ""))
        self.assertFalse(os.path.exists(self.heart))

    def test_non_bash_command_passes_through_without_python(self):
        bad = os.path.join(self.tmp, "would_fail.py")
        with open(bad, "w") as fh:
            fh.write("import sys\nopen(%r, 'w').write('started')\nsys.exit(99)\n" % os.path.join(self.tmp, "started"))
        rc, out, err, _ = self.run_cmd("echo hello", raw=True, SHELL_SCRUB_WRAPPER=bad)
        self.assertEqual((rc, out, err), (0, "hello\n", ""))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "started")))
        self.assertFalse(os.path.exists(self.heart))

    def test_non_bash_command_keeps_exit_code_and_quoting(self):
        rc, out, _, _ = self.run_cmd("bash ~/nothing-here 2>/dev/null; echo \"a  b\" 'c'; exit 7", raw=True)
        self.assertEqual((rc, out), (7, "a  b c\n"))

    def test_bash_shaped_command_does_start_the_wrapper(self):
        self.run_cmd("true")
        self.assertTrue(os.path.exists(self.heart))

    def test_missing_wrapper_runs_unfiltered_with_a_warning(self):
        rc, out, err, _ = self.run_cmd("echo 'jwt_secret=%s'" % FAKE, SHELL_SCRUB_WRAPPER=os.path.join(self.tmp, "nope.py"))
        self.assertEqual(rc, 0)
        self.assertEqual(out, "jwt_secret=%s\n" % FAKE)
        self.assertEqual(err.count("NOT being masked"), 1)
        self.assertEqual(len(err.splitlines()), 1)

    def test_files_are_executable_and_have_headers(self):
        for path in (PREFIX, WRAPPER):
            self.assertTrue(os.access(path, os.X_OK), path)
            lines = open(path).read().splitlines()
            self.assertTrue(lines[0].startswith("#!"))
            self.assertTrue(any(l.startswith("# Use when:") or l.startswith("Use when:") for l in lines[:6]), path)


if __name__ == "__main__":
    unittest.main()
