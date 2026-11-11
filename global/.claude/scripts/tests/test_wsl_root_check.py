"""Tests for wsl_root_check.py (issue 368). Every outside program is a fake; the gateway shell snippet runs for real against a temp folder."""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import wsl_root_check as rc  # noqa: E402
import wsl_bridge_watchdog as wd  # noqa: E402

TASKLIST_RUNNING = "Docker Desktop.exe   1234 Console   1   300,000 K"


class Fake:
    """Answers run() by matching the start of argv; records every call."""

    def __init__(self, answers=None):
        self.answers = answers or []
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        for prefix, result in self.answers:
            if argv[: len(prefix)] == prefix:
                return result(argv) if callable(result) else result
        return 0, ""

    def ran(self, *prefix):
        return any(c[: len(prefix)] == list(prefix) for c in self.calls)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rootcheck-")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.tmp]))
        self.state = os.path.join(self.tmp, "state")
        self.binfmt = os.path.join(self.tmp, "binfmt")
        os.makedirs(self.state)
        os.makedirs(self.binfmt)
        self.clock = [1_800_000_000.0]

    def entry_writer(self):
        def write(path, entry):
            self.written = (path, entry)
            name = entry.split(":")[1]
            open(os.path.join(self.binfmt, name), "w").write("enabled\n")
        return write

    def ctx(self, fake=None, **kw):
        return rc.Ctx(self.state, binfmt=self.binfmt, run=fake or Fake(), write_register=kw.pop("write_register", self.entry_writer()),
                      now=lambda: self.clock[0], proc=self.tmp, **kw)


class InteropTests(Base):
    def test_missing_wslinterop_is_registered_and_reported(self):
        open(os.path.join(self.binfmt, "register"), "w").close()
        changes, problems = rc.check_interop(self.ctx())
        self.assertEqual(problems, [])
        self.assertEqual(len(changes), 1)
        self.assertIn("WSLInterop was missing", changes[0])
        self.assertEqual(self.written[1], rc.WSL_INTEROP_ENTRY)
        self.assertTrue(os.path.exists(os.path.join(self.binfmt, "WSLInterop")))

    def test_present_wslinterop_changes_nothing(self):
        open(os.path.join(self.binfmt, "register"), "w").close()
        open(os.path.join(self.binfmt, "WSLInterop"), "w").close()
        self.written = None
        self.assertEqual(rc.check_interop(self.ctx()), ([], []))
        self.assertIsNone(self.written)

    def test_unmounted_binfmt_is_mounted_first(self):
        def mount(argv):
            open(os.path.join(self.binfmt, "register"), "w").close()
            return 0, ""
        fake = Fake([(["mount"], mount)])
        changes, problems = rc.check_interop(self.ctx(fake))
        self.assertEqual(problems, [])
        self.assertEqual(changes[0], "binfmt_misc mounted")
        self.assertIn("registered it", changes[1])

    def test_failed_mount_is_a_problem_and_registers_nothing(self):
        fake = Fake([(["mount"], (32, "denied"))])
        changes, problems = rc.check_interop(self.ctx(fake))
        self.assertEqual(changes, [])
        self.assertIn("mounting it failed", problems[0])

    def test_a_refused_register_write_is_a_problem(self):
        open(os.path.join(self.binfmt, "register"), "w").close()
        def boom(path, entry):
            raise PermissionError(1, "Operation not permitted")
        changes, problems = rc.check_interop(self.ctx(write_register=boom))
        self.assertEqual(changes, [])
        self.assertIn("registering it failed", problems[0])

    def test_a_write_that_creates_nothing_is_not_reported_as_fixed(self):
        open(os.path.join(self.binfmt, "register"), "w").close()
        changes, problems = rc.check_interop(self.ctx(write_register=lambda p, e: None))
        self.assertEqual(changes, [])
        self.assertIn("did not create it", problems[0])

    def test_broken_interop_skips_the_checks_that_need_windows(self):
        fake = Fake([(["mount"], (32, "denied"))])
        ctx = self.ctx(fake)
        with mock.patch.object(rc, "check_sysctl", return_value=([], [])):
            changes, problems = rc.run_all(ctx)
        self.assertTrue(any("gateway check skipped" in p for p in problems))
        self.assertFalse(fake.ran(ctx.wsl))
        self.assertFalse(fake.ran(ctx.schtasks))


class GatewayStepTests(Base):
    def test_gateway_not_running_is_left_alone(self):
        fake = Fake([(["wsl.exe", "-l"], (0, "Debian\nUbuntu-20.04\n"))])
        self.assertEqual(rc.check_gateway(self.ctx(fake)), ([], []))
        self.assertEqual(len(fake.calls), 1)  # only the list; the gateway is never addressed, so never started

    def test_utf16_distro_list_is_read(self):
        text = "Debian\r\nOpenClawGateway\r\n".encode("utf-16-le")
        self.assertEqual(rc.decode(text).split(), ["Debian", "OpenClawGateway"])

    def test_gateway_changes_and_problems_are_reported_with_a_prefix(self):
        fake = Fake([(["wsl.exe", "-l"], (0, "OpenClawGateway\n")),
                     (["wsl.exe", "-d"], (0, "CHANGED systemd-binfmt.service masked (was enabled)\nPROBLEM could not neutralize binfmt.d/x.conf\n"))])
        changes, problems = rc.check_gateway(self.ctx(fake))
        self.assertEqual(changes, ["gateway: systemd-binfmt.service masked (was enabled)"])
        self.assertEqual(problems, ["gateway: could not neutralize binfmt.d/x.conf"])
        self.assertTrue(fake.ran("wsl.exe", "-d", "OpenClawGateway", "-u", "root"))

    def test_the_mask_is_proven_only_when_the_snippet_says_masked_and_nothing_failed(self):
        for out, rcode, want in (("MASKED\n", 0, True), ("", 0, False), ("MASKED\nPROBLEM x\n", 0, False), ("MASKED\n", 1, False)):
            fake = Fake([(["wsl.exe", "-l"], (0, "OpenClawGateway\n")), (["wsl.exe", "-d"], (rcode, out))])
            ctx = self.ctx(fake)
            rc.check_gateway(ctx)
            self.assertEqual(ctx.mask_proven, want, (out, rcode))

    def test_a_stopped_gateway_proves_nothing(self):
        fake = Fake([(["wsl.exe", "-l"], (0, "Debian\n"))])
        ctx = self.ctx(fake)
        rc.check_gateway(ctx)
        self.assertFalse(ctx.mask_proven)

    def test_a_failed_distro_list_is_a_problem_not_a_gateway_start(self):
        fake = Fake([(["wsl.exe", "-l"], (1, ""))])
        changes, problems = rc.check_gateway(self.ctx(fake))
        self.assertEqual(changes, [])
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("gateway not checked", problems[0])


class GatewayScriptTests(Base):
    """The real shell snippet, against a fake root folder and a fake systemctl."""

    def run_snippet(self, state):
        root = os.path.join(self.tmp, "gw")
        os.makedirs(os.path.join(root, "usr/lib/binfmt.d"), exist_ok=True)
        os.makedirs(os.path.join(root, "etc/binfmt.d"), exist_ok=True)
        open(os.path.join(root, "usr/lib/binfmt.d/python3.12.conf"), "w").write(":python3.12:M::x::/usr/bin/python3.12:\n")
        open(os.path.join(root, "etc/binfmt.d/WSLInterop.conf"), "w").write(rc.WSL_INTEROP_ENTRY + "\n")
        sc = os.path.join(self.tmp, "systemctl")
        log = os.path.join(self.tmp, "sc.log")
        open(sc, "w").write('#!/bin/sh\necho "$@" >> "%s"\n[ "$1" = is-enabled ] && echo "%s"\nexit 0\n' % (log, state))
        os.chmod(sc, os.stat(sc).st_mode | stat.S_IEXEC)
        env = dict(os.environ, ROOT_PREFIX=root, SYSTEMCTL=sc)
        run = lambda: subprocess.run(["sh", "-c", rc.GATEWAY_SCRIPT], env=env, capture_output=True, text=True).stdout
        return root, log, run

    def test_unmasked_unit_is_masked_and_binfmt_files_neutralized(self):
        root, log, run = self.run_snippet("enabled")
        out = run()
        self.assertIn("CHANGED systemd-binfmt.service masked (was enabled)", out)
        self.assertIn("mask systemd-binfmt.service", open(log).read())
        self.assertNotIn("MASKED", out.splitlines())  # this fake keeps saying "enabled", so the mask is not proven
        for n in ("python3.12.conf", "WSLInterop.conf"):
            link = os.path.join(root, "etc/binfmt.d", n)
            self.assertTrue(os.path.islink(link) and os.readlink(link) == "/dev/null", n)
            self.assertIn("CHANGED binfmt.d/%s pointed at /dev/null" % n, out)
        self.assertTrue(os.path.exists(os.path.join(root, "etc/binfmt.d/WSLInterop.conf.disabled")))  # the old file is kept

    def test_second_run_changes_nothing(self):
        root, log, run = self.run_snippet("masked")
        run()
        self.assertEqual(run().strip(), "MASKED")  # the proof line only; no CHANGED or PROBLEM line
        self.assertNotIn("mask", open(log).read())  # masked already: never asked to mask


class SysctlTests(Base):
    def test_drift_is_rewritten_and_reported(self):
        for key in wd.DESIRED:
            p = self.tmp + wd.sysctl_path(key)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").write("0\n")
        changes, problems = rc.check_sysctl(self.ctx())
        self.assertEqual(problems, [])
        self.assertEqual(len(changes), len(wd.DESIRED))
        for key, want in wd.DESIRED.items():
            self.assertEqual(open(self.tmp + wd.sysctl_path(key)).read(), str(want))
        self.assertEqual(rc.check_sysctl(self.ctx()), ([], []))  # second pass: nothing to do


class TaskTests(Base):
    def test_disabled_task_is_enabled(self):
        def query(argv):
            return (0, "Scheduled Task State:   Disabled\n") if argv[3] == "\\AFA\\OpenClawStatus" else (1, "ERROR: not found")
        fake = Fake([(["schtasks.exe", "/query"], query)])
        ctx = self.ctx(fake)
        ctx.mask_proven = True
        changes, problems = rc.check_tasks(ctx)
        self.assertEqual(problems, [])
        self.assertEqual(changes, ["task \\AFA\\OpenClawStatus was Disabled: enabled it"])
        self.assertTrue(fake.ran("schtasks.exe", "/change", "/tn", "\\AFA\\OpenClawStatus", "/enable"))

    def disabled_status_only(self, container_out=(0, ""), extra=()):
        def query(argv):
            return (0, "Scheduled Task State:   Disabled\n") if argv[3] == "\\AFA\\OpenClawStatus" else (1, "ERROR: not found")
        return Fake([(["schtasks.exe", "/query"], query), (["docker", "ps"], container_out)] + list(extra))

    def test_openclawstatus_stays_disabled_with_no_container_and_no_proven_mask(self):
        fake = self.disabled_status_only()
        self.assertEqual(rc.check_tasks(self.ctx(fake)), ([], []))
        self.assertFalse(fake.ran("schtasks.exe", "/change"))

    def test_openclawstatus_is_enabled_when_the_gateway_container_runs(self):
        fake = self.disabled_status_only((0, "openclaw-openclaw-gateway-1\n"))
        changes, problems = rc.check_tasks(self.ctx(fake))
        self.assertEqual(changes, ["task \\AFA\\OpenClawStatus was Disabled: enabled it"])
        self.assertTrue(fake.ran("schtasks.exe", "/change", "/tn", "\\AFA\\OpenClawStatus", "/enable"))

    def test_another_container_or_a_failed_docker_does_not_count(self):
        for out in ((0, "openclaw-stage-openclaw-gateway-1\nsomething\n"), (1, "Cannot connect")):
            fake = self.disabled_status_only(out)
            self.assertEqual(rc.check_tasks(self.ctx(fake)), ([], []))
            self.assertFalse(fake.ran("schtasks.exe", "/change"))

    def test_openclawstatus_is_enabled_when_the_mask_was_proven_this_run(self):
        fake = self.disabled_status_only()
        ctx = self.ctx(fake)
        ctx.mask_proven = True
        changes, _ = rc.check_tasks(ctx)
        self.assertEqual(changes, ["task \\AFA\\OpenClawStatus was Disabled: enabled it"])
        self.assertFalse(fake.ran("docker", "ps"))  # the mask proof is enough; docker is not asked

    def test_towerkeeper_is_never_guarded(self):
        def query(argv):
            return (0, "Scheduled Task State:   Disabled\n") if argv[3] == "\\AFA\\TowerKeeper" else (1, "ERROR: not found")
        fake = Fake([(["schtasks.exe", "/query"], query)])
        changes, _ = rc.check_tasks(self.ctx(fake))
        self.assertEqual(changes, ["task \\AFA\\TowerKeeper was Disabled: enabled it"])
        self.assertFalse(fake.ran("docker", "ps"))

    def test_enabled_or_absent_tasks_are_not_touched(self):
        fake = Fake([(["schtasks.exe", "/query"], (0, "Scheduled Task State:   Enabled\n"))])
        self.assertEqual(rc.check_tasks(self.ctx(fake)), ([], []))
        self.assertFalse(fake.ran("schtasks.exe", "/change"))

    def test_refused_enable_names_the_exact_command(self):
        fake = Fake([(["schtasks.exe", "/query"], (0, "Scheduled Task State:   Disabled\n")),
                     (["schtasks.exe", "/change"], (1, "ERROR: Access is denied.\n"))])
        ctx = self.ctx(fake)
        ctx.mask_proven = True
        changes, problems = rc.check_tasks(ctx)
        self.assertEqual(changes, [])
        self.assertIn("schtasks.exe /change /tn '\\AFA\\OpenClawStatus' /enable", problems[0])


class DockerTests(Base):
    def docker_fake(self, containers="", version_rc=1, running=TASKLIST_RUNNING, restart_rc=0):
        return Fake([(["timeout"], (version_rc, "")), (["tasklist.exe"], (0, running)),
                     (["docker.exe", "ps"], (0, containers)), (["docker.exe", "desktop"], (restart_rc, ""))])

    def test_first_failure_only_waits(self):
        fake = self.docker_fake()
        self.assertEqual(rc.check_docker(self.ctx(fake)), ([], []))
        self.assertFalse(fake.ran("docker.exe", "desktop", "restart"))

    def test_second_failure_with_no_containers_restarts_once(self):
        fake = self.docker_fake()
        rc.check_docker(self.ctx(fake))
        changes, problems = rc.check_docker(self.ctx(fake))
        self.assertEqual(problems, [])
        self.assertIn("restarted Docker Desktop", changes[0])
        self.assertTrue(fake.ran("docker.exe", "desktop", "restart"))

    def test_volumes_are_backed_up_before_the_restart(self):
        fake = self.docker_fake()
        rc.check_docker(self.ctx(fake))
        changes, problems = rc.check_docker(self.ctx(fake))
        self.assertEqual(problems, [])
        idx_backup = [i for i, c in enumerate(fake.calls) if any(x.endswith("docker_volume_backup.py") for x in c)]
        idx_restart = [i for i, c in enumerate(fake.calls) if c[:3] == ["docker.exe", "desktop", "restart"]]
        self.assertEqual(len(idx_backup), 1)
        self.assertEqual(len(idx_restart), 1)
        self.assertLess(idx_backup[0], idx_restart[0])
        call = fake.calls[idx_backup[0]]
        self.assertIn("--windows-paths", call)
        self.assertEqual(call[call.index("--docker") + 1], "docker.exe")
        self.assertTrue(call[call.index("--dest") + 1].endswith("/.cache/docker-volume-backups"))  # the folder two levels above the state folder = the home
        self.assertIn("before it:", changes[0])

    def test_a_failed_volume_backup_blocks_the_restart(self):
        fake = Fake([(["timeout"], (1, "")), (["tasklist.exe"], (0, TASKLIST_RUNNING)), (["docker.exe", "ps"], (0, "")),
                     ([sys.executable], (1, "copied wp-data\ndocker_volume_backup: FAILED, 1 copied, 1 not copied (db-data) in /x\n")),
                     (["docker.exe", "desktop"], (0, ""))])
        rc.check_docker(self.ctx(fake))
        changes, problems = rc.check_docker(self.ctx(fake))
        self.assertEqual(changes, [])
        self.assertFalse(fake.ran("docker.exe", "desktop", "restart"))
        self.assertIn("volume backup failed", problems[0])
        self.assertIn("not copied (db-data)", problems[0])
        self.assertFalse(os.path.exists(os.path.join(self.state, "wsl-root-check.docker-restart")))  # tried again next run

    def test_a_missing_backup_script_blocks_the_restart(self):
        fake = Fake([(["timeout"], (1, "")), (["tasklist.exe"], (0, TASKLIST_RUNNING)), (["docker.exe", "ps"], (0, "")),
                     ([sys.executable], (127, ""))])
        rc.check_docker(self.ctx(fake))
        _, problems = rc.check_docker(self.ctx(fake))
        self.assertIn("exit 127", problems[0])
        self.assertFalse(fake.ran("docker.exe", "desktop", "restart"))

    def test_a_running_container_blocks_the_restart(self):
        fake = self.docker_fake(containers="abc123\ndef456\n")
        rc.check_docker(self.ctx(fake))
        changes, problems = rc.check_docker(self.ctx(fake))
        self.assertEqual(changes, [])
        self.assertIn("2 container(s) run", problems[0])
        self.assertFalse(fake.ran("docker.exe", "desktop", "restart"))

    def test_uncountable_containers_block_the_restart(self):
        fake = Fake([(["timeout"], (1, "")), (["tasklist.exe"], (0, TASKLIST_RUNNING)), (["docker.exe", "ps"], (1, "error"))])
        rc.check_docker(self.ctx(fake))
        changes, problems = rc.check_docker(self.ctx(fake))
        self.assertEqual(changes, [])
        self.assertFalse(fake.ran("docker.exe", "desktop", "restart"))
        self.assertIn("could not be counted", problems[0])

    def test_at_most_one_restart_an_hour(self):
        fake = self.docker_fake()
        for _ in range(2):
            rc.check_docker(self.ctx(fake))
        self.clock[0] += 600
        rc.check_docker(self.ctx(fake))
        changes, problems = rc.check_docker(self.ctx(fake))
        self.assertEqual(changes, [])
        self.assertIn("within the hour", problems[0])
        self.assertEqual(sum(c[:3] == ["docker.exe", "desktop", "restart"] for c in fake.calls), 1)
        self.clock[0] += 4000
        changes, _ = rc.check_docker(self.ctx(fake))  # the count is still at two, so the first look after the hour restarts
        self.assertEqual(len(changes), 1)

    def test_docker_answering_or_docker_desktop_not_running_changes_nothing(self):
        for fake in (self.docker_fake(version_rc=0), self.docker_fake(running="INFO: No tasks are running")):
            for _ in range(3):
                self.assertEqual(rc.check_docker(self.ctx(fake)), ([], []))
            self.assertFalse(fake.ran("docker.exe", "desktop", "restart"))

    def test_a_good_answer_resets_the_failure_count(self):
        bad = self.docker_fake()
        rc.check_docker(self.ctx(bad))
        rc.check_docker(self.ctx(self.docker_fake(version_rc=0)))
        self.assertEqual(rc.check_docker(self.ctx(bad)), ([], []))  # counted from one again, so still waiting


class RecordTests(Base):
    def test_a_quiet_run_writes_only_the_last_stamp(self):
        self.assertIsNone(rc.record(self.ctx(), [], []))
        self.assertEqual(sorted(os.listdir(self.state)), ["wsl-root-check.last"])

    def test_changes_go_to_the_log_and_a_new_recovery_record(self):
        target = rc.record(self.ctx(), ["WSLInterop was missing from binfmt_misc: registered it"], [])
        self.assertTrue(os.path.dirname(target).endswith("recovery"))
        text = open(target).read()
        self.assertIn("changed: WSLInterop was missing", text)
        self.assertIn("changed: WSLInterop was missing", open(os.path.join(self.state, "wsl-root-check.log")).read())

    def test_a_recent_recovery_record_is_appended_to_not_duplicated(self):
        rec = os.path.join(self.state, "recovery")
        os.makedirs(rec)
        old = os.path.join(rec, "2027-01-15-0800.md")
        open(old, "w").write("# Recovery\n")
        os.utime(old, (self.clock[0] - 600, self.clock[0] - 600))
        target = rc.record(self.ctx(), ["x"], ["y"])
        self.assertEqual(target, old)
        text = open(old).read()
        self.assertTrue(text.startswith("# Recovery\n"))
        self.assertIn("PROBLEM: y", text)
        self.assertEqual(len(os.listdir(rec)), 1)


class WatchdogRootRouteTests(unittest.TestCase):
    def test_run_root_runs_the_script_directly_when_already_root(self):
        ok = mock.Mock(returncode=0)
        with mock.patch.object(wd.os, "geteuid", return_value=0), mock.patch.object(wd.subprocess, "run", return_value=ok) as run:
            self.assertEqual(wd.run_root("echo 1 > /proc/x"), (True, "root"))
        self.assertEqual(run.call_args[0][0], ["sh", "-c", "echo 1 > /proc/x"])  # no docker, no wsl.exe


class PrivilegeTests(Base):
    """Root must not follow links a person planted in the state folder, and must not run code from a folder that person can write."""

    def test_state_files_are_written_with_the_owners_identity_then_root_again(self):
        calls = []
        with mock.patch.object(rc, "_OWNER", (1000, 1001)), mock.patch.object(rc.os, "geteuid", return_value=0), \
                mock.patch.object(rc.os, "seteuid", side_effect=lambda u: calls.append(("u", u))), \
                mock.patch.object(rc.os, "setegid", side_effect=lambda g: calls.append(("g", g))):
            rc._write(os.path.join(self.state, "x"), "1")
        self.assertEqual(calls, [("g", 1001), ("u", 1000), ("u", 0), ("g", 0)])  # group first, back to root last

    def test_root_is_restored_even_when_the_write_fails(self):
        calls = []
        with mock.patch.object(rc, "_OWNER", (1000, 1001)), mock.patch.object(rc.os, "geteuid", return_value=0), \
                mock.patch.object(rc.os, "seteuid", side_effect=lambda u: calls.append(("u", u))), \
                mock.patch.object(rc.os, "setegid", side_effect=lambda g: calls.append(("g", g))):
            with self.assertRaises(OSError):
                rc._write(os.path.join(self.state, "no-such-folder", "x"), "1")
        self.assertEqual(calls[-2:], [("u", 0), ("g", 0)])

    def test_nothing_is_switched_when_not_root(self):
        with mock.patch.object(rc, "_OWNER", (1000, 1001)), mock.patch.object(rc.os, "seteuid") as se:
            rc._write(os.path.join(self.state, "x"), "1")
        se.assert_not_called()

    def test_the_script_never_chowns_and_imports_nothing_from_the_scripts_folder_but_wsl_reserve(self):
        src = open(os.path.join(os.path.dirname(HERE), "wsl_root_check.py")).read()
        self.assertNotIn("chown", src.replace("chown-ed", ""))
        self.assertNotIn("wsl_bridge_watchdog", src.replace("wsl_bridge_watchdog.py keeps", ""))

    def test_find_state_takes_only_a_real_folder_owned_by_a_person(self):
        def fake_lstat(d):
            kind = {"/home/a/.claude/state": (stat.S_IFDIR, 0), "/home/b/.claude/state": (stat.S_IFLNK, 1000),
                    "/home/c/.claude/state": (stat.S_IFDIR, 1000)}[d]
            return mock.Mock(st_mode=kind[0] | 0o755, st_uid=kind[1])
        with mock.patch("glob.glob", return_value=["/home/a/.claude/state", "/home/b/.claude/state", "/home/c/.claude/state"]), \
                mock.patch.object(rc.os, "lstat", side_effect=fake_lstat):
            self.assertEqual(rc.find_state(), "/home/c/.claude/state")  # root-owned and symlinked ones are skipped

    def test_find_state_is_none_when_nothing_qualifies(self):
        with mock.patch("glob.glob", return_value=[]):
            self.assertIsNone(rc.find_state())

    def test_the_task_runs_the_root_owned_copy_not_a_home_folder_script(self):
        xml = open(os.path.join(os.path.dirname(HERE), "windows", "AFA-WslRootCheck.xml")).read()
        self.assertIn("/usr/local/lib/afa-root-check/wsl_root_check.py", xml)
        self.assertNotIn("/home/", xml)


class MainTests(Base):
    def test_main_returns_one_with_an_unrepairable_problem_and_zero_when_clean(self):
        env = {"ROOTCHECK_STATE": self.state, "ROOTCHECK_BINFMT": self.binfmt}
        with mock.patch.dict(os.environ, env), mock.patch.object(rc, "run_all", return_value=([], ["boom"])):
            self.assertEqual(rc.main([]), 1)
        with mock.patch.dict(os.environ, env), mock.patch.object(rc, "run_all", return_value=(["fixed"], [])):
            self.assertEqual(rc.main([]), 0)


if __name__ == "__main__":
    unittest.main()
