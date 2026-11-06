"""openclaw_gateway.py: the one way every OpenClaw script reaches the gateway. Docker first; the WSL distro only as the fallback.

5 Nov 2026 9:30 PM: on the tower there is no OpenClawGateway distro (OpenClaw runs in Docker), so every hub post died with wsl.exe exit 255
and "stderr=" empty in the log. Nothing here reaches a real gateway: `docker` and `wsl.exe` are fakes on PATH, and state_guard forces the
wsl route for every other test."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import state_guard  # noqa: E402,F401  first: tests always take the wsl route
import openclaw_gateway as gw  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

HERE = Path(__file__).resolve().parent.parent


class Fakes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(fresh_dir(self))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls"
        gw.forget()
        self.addCleanup(gw.forget)

    def fake_docker(self, running=True, exists=True):
        """A docker on PATH: `inspect -f {{.State.Running}} NAME` answers; `exec ...` records its words and prints them."""
        f = self.bin / "docker"
        f.write_text(
            '#!/usr/bin/env bash\necho "docker $*" >> "%s"\n'
            'if [ "$1" = inspect ]; then\n  [ "%d" = 1 ] || exit 1\n  echo %s\n  exit 0\nfi\n'
            'if [ "$1" = exec ]; then shift; echo "EXEC $*"; cat; exit 0; fi\nexit 99\n'
            % (self.calls, 1 if exists else 0, "true" if running else "false"))
        f.chmod(0o755)

    def fake_wsl(self, listing, code=0, utf16=True):
        """A wsl.exe on PATH that answers `-l -q` with listing and fails anything else, so a stray real call shows."""
        (self.tmp / "listing").write_bytes(listing.encode("utf-16-le") if utf16 else listing.encode())
        f = self.bin / "wsl.exe"
        f.write_text('#!/usr/bin/env bash\necho "wsl $*" >> "%s"\n[ "$1 $2" = "-l -q" ] || { echo "EXEC $*"; exit 0; }\ncat "%s"\nexit %d\n'
                     % (self.calls, self.tmp / "listing", code))
        f.chmod(0o755)

    def env(self, route=""):
        return mock.patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + "/usr/bin:/bin", gw.ROUTE_ENV: route})

    def asked(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []


class Route(Fakes):
    def test_a_running_container_means_docker_and_wsl_is_never_asked(self):
        self.fake_docker(running=True)
        self.fake_wsl("Debian\r\nOpenClawGateway\r\n")  # NX-01 keeps its distro as the rollback: asking the container first must not wake it
        with self.env():
            self.assertEqual(gw.route(), "docker")
        self.assertFalse([c for c in self.asked() if c.startswith("wsl")])

    def test_no_container_but_the_distro_is_listed_means_wsl(self):
        self.fake_docker(exists=False)
        self.fake_wsl("Debian\r\nOpenClawGateway\r\n")
        with self.env():
            self.assertEqual(gw.route(), "wsl")

    def test_a_stopped_container_falls_to_the_distro(self):
        self.fake_docker(running=False)
        self.fake_wsl("Debian\nOpenClawGateway\n", utf16=False)  # a plain utf-8 listing is read too
        with self.env():
            self.assertEqual(gw.route(), "wsl")

    def test_neither_means_docker_so_the_log_carries_dockers_own_message(self):
        self.fake_docker(exists=False)
        self.fake_wsl("Debian\r\nUbuntu-24.04\r\ndocker-desktop\r\n")
        with self.env():
            self.assertEqual(gw.route(), "docker")

    def test_nothing_installed_at_all_is_docker_too(self):
        with self.env():
            self.assertEqual(gw.route(), "docker")

    def test_the_answer_is_kept_for_a_while_and_the_env_forces_it(self):
        self.fake_docker(running=True)
        with self.env():
            self.assertEqual(gw.route(), "docker")
            (self.bin / "docker").unlink()
            self.assertEqual(gw.route(), "docker")  # not asked again inside the TTL
        with self.env("wsl"):
            self.assertEqual(gw.route(), "wsl")

    def test_tests_cannot_reach_a_real_gateway(self):
        self.assertEqual(os.environ.get(gw.ROUTE_ENV), "wsl")  # state_guard sets it for every run


class Argv(Fakes):
    def test_docker_runs_in_the_container_in_the_node_home_with_stdin_open(self):
        with self.env("docker"):
            a = gw.argv(["bash", "-c", "echo $HOME", "x"])
        self.assertEqual(a, ["docker", "exec", "-i", "-w", "/home/node", gw.GATEWAY_CONTAINER, "bash", "-c", "echo $HOME", "x"])

    def test_docker_root_and_terminal(self):
        with self.env("docker"):
            self.assertEqual(gw.argv(["id"], user="root")[:7], ["docker", "exec", "-i", "-w", "/home/node", "-u", "root"])
            self.assertEqual(gw.argv(["openclaw", "tui"], tty=True, via_shell=True)[:3], ["docker", "exec", "-it"])

    def test_wsl_is_what_it_was(self):
        with self.env("wsl"):
            self.assertEqual(gw.argv(["bash", "-c", "echo $HOME"]), ["wsl.exe", "-d", "OpenClawGateway", "-u", "openclaw", "-e", "bash", "-c", "echo $HOME"])
            self.assertEqual(gw.argv(["bash", "-s"], via_shell=True), ["wsl.exe", "-d", "OpenClawGateway", "-u", "openclaw", "--", "bash", "-s"])
            self.assertEqual(gw.argv(["id"], user="root")[:5], ["wsl.exe", "-d", "OpenClawGateway", "-u", "root"])

    def test_distro_only_work_never_goes_to_the_container(self):
        with self.env("docker"):
            a = gw.argv(["bash", "-s"], user="root", distro_only=True)
        self.assertEqual(a[:5], ["wsl.exe", "-d", "OpenClawGateway", "-u", "root"])
        self.assertNotIn("docker", a)

    def test_home_workspace_and_name_follow_the_route(self):
        with self.env("docker"):
            self.assertEqual(gw.home(), "/home/node")
            self.assertIsNone(gw.workspace())  # the tower's front desk has no workspace the hub writes
            self.assertIn("container", gw.name())
            self.assertEqual(gw.home(distro_only=True), "/home/openclaw")
        with self.env("wsl"):
            self.assertEqual(gw.home(), "/home/openclaw")
            self.assertEqual(gw.workspace(), "/home/openclaw/.openclaw/workspace")
            self.assertEqual(gw.name(), "the OpenClawGateway distro")


class WhyFailed(unittest.TestCase):
    def test_never_empty_and_reads_wsls_utf16_on_stdout(self):
        R = lambda o, e: type("R", (), {"returncode": 1, "stdout": o, "stderr": e})()
        self.assertEqual(gw.why_failed(R("", "")), "(no output)")
        self.assertEqual(gw.why_failed(None), "no process result")
        self.assertEqual(gw.why_failed(R("out", "err")), "err out")
        self.assertEqual(len(gw.why_failed(R("x" * 900, ""))), 300)
        msg = "There is no distribution with the supplied name.\r\nError code: Wsl/Service/WSL_E_DISTRO_NOT_FOUND"
        self.assertEqual(gw.why_failed(R("\0".join(msg) + "\0", "")), "There is no distribution with the supplied name. Error code: Wsl/Service/WSL_E_DISTRO_NOT_FOUND")


class Cli(Fakes):
    def run_cli(self, route, *args, stdin=""):
        self.fake_docker(running=True)
        self.fake_wsl("OpenClawGateway\r\n")
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + "/usr/bin:/bin", **{gw.ROUTE_ENV: route})
        return subprocess.run([sys.executable, str(HERE / "openclaw_gateway.py")] + list(args), input=stdin, capture_output=True, text=True, env=env, timeout=30)

    def test_exec_runs_the_command_and_passes_stdin_through(self):
        r = self.run_cli("docker", "exec", "--", "bash", "-s", stdin="hello\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("EXEC -i -w /home/node %s bash -s" % gw.GATEWAY_CONTAINER, r.stdout)
        self.assertIn("hello", r.stdout)

    def test_exec_wsl_flag_takes_the_distro_even_when_the_container_runs(self):
        r = self.run_cli("docker", "exec", "--wsl", "-u", "root", "--", "id")
        self.assertIn("EXEC -d OpenClawGateway -u root -e id", r.stdout)

    def test_probes(self):
        self.assertEqual(self.run_cli("docker", "route").stdout.strip(), "docker")
        self.assertEqual(self.run_cli("docker", "home").stdout.strip(), "/home/node")
        r = self.run_cli("docker", "workspace")
        self.assertEqual((r.returncode, r.stdout.strip()), (1, ""))
        r = self.run_cli("wsl", "workspace")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "/home/openclaw/.openclaw/workspace"))

    def test_bad_usage_exits_2(self):
        self.assertEqual(self.run_cli("docker", "exec", "-x", "--", "id").returncode, 2)
        self.assertEqual(self.run_cli("docker", "exec").returncode, 2)
        self.assertEqual(self.run_cli("docker", "nonsense").returncode, 2)


class ShellTwin(Fakes):
    def sh(self, route, script, stdin=""):
        self.fake_docker(running=True)
        self.fake_wsl("OpenClawGateway\r\n")
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + "/usr/bin:/bin", **{gw.ROUTE_ENV: route})
        return subprocess.run(["bash", "-c", '. "%s/openclaw_gateway.sh"; %s' % (HERE, script)], input=stdin, capture_output=True, text=True, env=env, timeout=30)

    def test_oc_gateway_runs_through_the_python_route(self):
        r = self.sh("docker", 'oc_gateway -- bash -c "echo hi"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("EXEC -i -w /home/node %s bash -c echo hi" % gw.GATEWAY_CONTAINER, r.stdout)

    def test_timeout_can_wrap_the_python_call(self):
        r = self.sh("docker", 'timeout 20 python3 "$oc_gateway_py" exec -- id')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(gw.GATEWAY_CONTAINER, r.stdout)

    def test_workspace_and_home(self):
        self.assertEqual(self.sh("docker", "oc_gateway_home").stdout.strip(), "/home/node")
        r = self.sh("docker", "oc_gateway_workspace")
        self.assertEqual((r.returncode, r.stdout.strip()), (1, ""))
        self.assertEqual(self.sh("wsl", "oc_gateway_workspace").stdout.strip(), "/home/openclaw/.openclaw/workspace")


class NoDistroNamedByHand(unittest.TestCase):
    """The point of the helper: no other script names the distro or runs wsl.exe -d for the gateway itself."""

    def test_only_the_helper_names_the_distro_in_code(self):
        offenders = []
        for p in sorted(HERE.glob("*.py")) + sorted(HERE.glob("*.sh")):
            if p.name.startswith("openclaw_gateway"):
                continue
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                code = line.split("#", 1)[0] if p.suffix == ".sh" else line
                if "wsl.exe" in code and "OpenClawGateway" in code and not code.lstrip().startswith(('"""', "'''")) and "\"" in code and "-d" in code:
                    offenders.append("%s:%d" % (p.name, n))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
