"""Tests for docker_volume_backup.py. Docker is a fake runner: nothing here touches a real engine, volume, restart or prune."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import docker_volume_backup as dvb  # noqa: E402

ANON = "a" * 64


class FakeDocker:
    """Answers the few docker calls the script makes. A `docker run` writes the .part file the container's tar would have written."""

    def __init__(self, volumes, engine_rc=0, images=("alpine",), fail=()):
        self.volumes, self.engine_rc, self.images, self.fail = volumes, engine_rc, images, set(fail)
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        a = argv[2:] if argv[0] == "timeout" else argv
        if a[1:3] == ["volume", "ls"]:
            return (self.engine_rc, "\n".join(self.volumes) + "\n") if self.engine_rc == 0 else (self.engine_rc, "")
        if a[1:3] == ["image", "inspect"]:
            return (0, "") if a[3] in self.images else (1, "no such image")
        if a[1] == "run":
            vol = [x for x in a if x.endswith(":/v:ro")][0].split(":")[0]
            if vol in self.fail:
                return 1, "tar: boom"
            bind = [x for x in a if x.endswith(":/b")][0][:-3]
            if bind.startswith("WIN:"):
                bind = bind[4:]
            with open(os.path.join(bind, vol + ".tgz.part"), "wb") as f:
                f.write(b"x" * 10)
            return 0, "ok\n"
        return 0, ""

    def verbs(self):
        return [(c[2:] if c[0] == "timeout" else c)[1:3] for c in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        self.dest = tempfile.mkdtemp(prefix="dvb-")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.dest]))
        self.clock = [1_800_000_000.0]

    def go(self, fake, *args):
        out = io.StringIO()
        rc = dvb.main(["--dest", self.dest, *args], runner=fake, now=lambda: self.clock[0], out=out)
        return rc, out.getvalue()

    def sets(self):
        return sorted(d for d in os.listdir(self.dest))


class SelectionTests(Base):
    def test_anonymous_and_cache_volumes_are_skipped(self):
        names = [ANON, "rd12_node_modules", "rd12_pnpm", "cd-runner_dind-data", "hf_cache", "ollama-data", "supabase_db_app", "wp-data"]
        keep, skipped = dvb.wanted_volumes(names, dvb.DEFAULT_SKIP)
        self.assertEqual(keep, ["supabase_db_app", "wp-data"])
        self.assertEqual(len(skipped), 6)

    def test_custom_skip_pattern(self):
        keep, _ = dvb.wanted_volumes(["a", "b"], "^a$")
        self.assertEqual(keep, ["b"])


class BackupTests(Base):
    def test_copies_each_named_volume_read_only(self):
        fake = FakeDocker([ANON, "supabase_db_app", "wp-data"])
        rc, out = self.go(fake)
        self.assertEqual(rc, 0)
        (stamp,) = self.sets()
        self.assertEqual(sorted(os.listdir(os.path.join(self.dest, stamp))), ["supabase_db_app.tgz", "wp-data.tgz"])
        runs = [c for c in fake.calls if "run" in c]
        self.assertEqual(len(runs), 2)
        for c in runs:
            self.assertTrue(any(x.endswith(":/v:ro") for x in c), "the volume must be mounted read-only")
            self.assertIn("--rm", c)
            self.assertIn("none", c)  # --network none
            self.assertEqual(c[0], "timeout")
        self.assertIn("2 volume(s) copied", out)

    def test_never_removes_prunes_stops_or_restarts_anything(self):
        fake = FakeDocker(["wp-data", "db-data"], fail=["db-data"])
        self.go(fake)
        for verbs in fake.verbs():
            self.assertNotIn(verbs[0], ("rm", "stop", "kill", "restart", "desktop", "system", "prune", "compose"))
            self.assertNotEqual(verbs, ["volume", "rm"])
            self.assertNotEqual(verbs, ["volume", "prune"])
        self.assertTrue(all(v in (["volume", "ls"], ["image", "inspect"], ["run", "--rm"]) for v in fake.verbs()))

    def test_engine_that_does_not_answer_is_a_clean_skip(self):
        fake = FakeDocker(["wp-data"], engine_rc=124)
        rc, out = self.go(fake)
        self.assertEqual(rc, 0)
        self.assertIn("skipped, engine did not answer", out)
        self.assertEqual(self.sets(), [])
        self.assertFalse(any("run" in c for c in fake.calls))

    def test_nothing_to_back_up(self):
        rc, out = self.go(FakeDocker([ANON, "rd1_node_modules"]))
        self.assertEqual(rc, 0)
        self.assertIn("nothing to back up", out)
        self.assertEqual(self.sets(), [])

    def test_one_failed_copy_returns_1_and_leaves_no_partial_file(self):
        rc, out = self.go(FakeDocker(["wp-data", "db-data"], fail=["db-data"]))
        self.assertEqual(rc, 1)
        (stamp,) = self.sets()
        self.assertEqual(os.listdir(os.path.join(self.dest, stamp)), ["wp-data.tgz"])
        self.assertIn("FAILED, 1 copied, 1 not copied (db-data)", out)

    def test_no_local_image_is_a_failure_and_never_pulls(self):
        fake = FakeDocker(["wp-data"], images=())
        rc, out = self.go(fake)
        self.assertEqual(rc, 1)
        self.assertIn("never pulls", out)
        self.assertNotIn(["pull"], [c[1:2] for c in fake.calls])

    def test_busybox_is_used_when_alpine_is_absent(self):
        fake = FakeDocker(["wp-data"], images=("busybox",))
        self.assertEqual(self.go(fake)[0], 0)
        self.assertTrue(any("busybox" in c for c in fake.calls if "run" in c))

    def test_too_little_free_space_is_a_failure(self):
        usage = mock.Mock(free=1024 ** 3)
        with mock.patch.object(dvb.shutil, "disk_usage", return_value=usage):
            rc, out = self.go(FakeDocker(["wp-data"]))
        self.assertEqual(rc, 1)
        self.assertIn("MB free", out)

    def test_budget_used_up_fails_the_rest(self):
        fake = FakeDocker(["a-data", "b-data"])
        real = fake.__call__

        def slow(argv, timeout):
            self.clock[0] += 1000  # the first copy eats the budget
            return real(argv, timeout)

        out = io.StringIO()
        rc = dvb.main(["--dest", self.dest, "--budget", "1000"], runner=slow, now=lambda: self.clock[0], out=out)
        self.assertEqual(rc, 1)
        self.assertIn("1 copied, 1 not copied (b-data)", out.getvalue())

    def test_dry_run_copies_nothing(self):
        fake = FakeDocker(["wp-data"])
        rc, out = self.go(fake, "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("would copy wp-data", out)
        self.assertEqual(self.sets(), [])
        self.assertFalse(any("run" in c for c in fake.calls))

    def test_bad_skip_pattern_is_exit_2(self):
        self.assertEqual(self.go(FakeDocker([]), "--skip", "(")[0], 2)

    def test_windows_paths_hand_docker_a_windows_folder(self):
        fake = FakeDocker(["wp-data"])
        with mock.patch.object(dvb, "run", side_effect=lambda argv, t: (0, "WIN:" + argv[-1] + "\n")):
            rc, _ = self.go(fake, "--windows-paths", "--docker", "docker.exe")
        self.assertEqual(rc, 0)
        run_call = [c for c in fake.calls if "run" in c][0]
        self.assertTrue(any(x.startswith("WIN:") and x.endswith(":/b") for x in run_call))
        self.assertIn("docker.exe", run_call)


class PruneTests(Base):
    def test_only_the_newest_sets_stay_and_only_its_own_folders_go(self):
        for s in ("20260101-000001", "20260102-000001", "20260103-000001", "20260104-000001"):
            os.makedirs(os.path.join(self.dest, s))
        os.makedirs(os.path.join(self.dest, "keep-me"))
        open(os.path.join(self.dest, "notes.txt"), "w").close()
        gone = dvb.prune_old_sets(self.dest, 3)
        self.assertEqual(gone, ["20260101-000001"])
        self.assertEqual(sorted(os.listdir(self.dest)), ["20260102-000001", "20260103-000001", "20260104-000001", "keep-me", "notes.txt"])

    def test_failed_run_prunes_nothing(self):
        for s in ("20260101-000001", "20260102-000001", "20260103-000001"):
            os.makedirs(os.path.join(self.dest, s))
        rc, _ = self.go(FakeDocker(["db-data"], fail=["db-data"]), "--keep", "1")
        self.assertEqual(rc, 1)
        self.assertEqual(len([d for d in self.sets() if d.startswith("2026010")]), 3)


if __name__ == "__main__":
    unittest.main()
