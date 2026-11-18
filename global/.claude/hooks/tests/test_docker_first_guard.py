"""test_docker_first_guard.py: tests for docker-first-guard.py PreToolUse hook.

Enforces Jonathan's rule: "always Docker first! for all our projects".
Refuses package manager and dev commands on host when not in docker run, docker exec, or docker compose.
Supports exemption list at ~/.config/afa/docker-first-exempt.txt.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "docker-first-guard.py"
RULE_QUOTE = '"always Docker first! for all our projects"'


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="docker-first-guard-"))
        cls.home = cls.tmp / "home"
        cls.afa = cls.home / ".config" / "afa"
        cls.afa.mkdir(parents=True)
        cls.exempt_dir = cls.tmp / "exempt-project"
        cls.exempt_dir.mkdir(parents=True)
        (cls.exempt_dir / "src").mkdir()

        # Exemption file with comments, blank lines, and an exempt path prefix
        (cls.afa / "docker-first-exempt.txt").write_text(
            f"# Exempt projects (person-authorized only)\n\n{cls.exempt_dir}\n"
        )

        cls.non_exempt_dir = cls.tmp / "normal-project"
        cls.non_exempt_dir.mkdir(parents=True)

        cls.no_exempt_home = cls.tmp / "no-exempt-home"
        cls.no_exempt_home.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_hook(self, command, cwd=None, home=None, payload=None, env=None):
        if payload is None:
            payload = {"tool_name": "Bash", "tool_input": {"command": command}}
            if cwd is not None:
                payload["cwd"] = str(cwd)
            else:
                payload["cwd"] = str(self.non_exempt_dir)
            payload = json.dumps(payload)
        full_env = {
            "HOME": str(home or self.home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        if env:
            full_env.update(env)
        r = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, env=full_env)
        return r.returncode, r.stderr

    def refused(self, command, *needles, **kw):
        code, err = self.run_hook(command, **kw)
        self.assertEqual(code, 2, f"allowed, but should be refused: {command}\nstdout/stderr: {err}")
        self.assertTrue(err.startswith("BLOCKED:"), f"Expected BLOCKED prefix, got: {err}")
        self.assertIn(RULE_QUOTE, err)
        self.assertIn("README container recipe", err)
        for needle in needles:
            self.assertIn(needle, err)
        return err

    def allowed(self, command, **kw):
        code, err = self.run_hook(command, **kw)
        self.assertEqual((code, err), (0, ""), f"refused, but should be allowed: {command}")


class HostCommandsRefused(Base):
    def test_host_npm_commands_refused(self):
        for command in (
            "npm ci",
            "npm install",
            "npm install --save-dev typescript",
            "npm run dev",
            "npm run build",
            "npm test",
            "npm start",
            "npm dev",
            "npm build",
            "npm exec vitest",
            "npm dlx create-next-app",
            "npm --prefix frontend install",
            "npm -s ci",
            "npm -w web test",
            "npm i",
            "npm t",
        ):
            with self.subTest(command=command):
                self.refused(command)

    def test_host_other_package_managers_refused(self):
        for command in (
            "pnpm install",
            "pnpm ci",
            "pnpm run build",
            "pnpm test",
            "pnpm dev",
            "pnpm dlx create-react-app",
            "yarn install",
            "yarn test",
            "yarn run dev",
            "yarn build",
            "yarn dlx degit",
            "bun install",
            "bun run dev",
            "bun test",
            "bun dev",
            "bunx vitest",
        ):
            with self.subTest(command=command):
                self.refused(command)

    def test_host_vite_next_expo_refused(self):
        for command in (
            "vite",
            "vite dev",
            "vite build",
            "vite preview",
            "vite --port 3000",
            "./node_modules/.bin/vite",
            "next dev",
            "next build",
            "next start",
            "next",
            "expo start",
            "expo start --clear",
        ):
            with self.subTest(command=command):
                self.refused(command)

    def test_host_npx_refused(self):
        for command in (
            "npx playwright test",
            "npx vitest run",
            "npx vite",
            "npx next dev",
            "npx expo start",
            "npx ci",
            "npx dlx create-app",
        ):
            with self.subTest(command=command):
                self.refused(command)

    def test_host_subshell_and_inline_scripts_refused(self):
        for command in (
            'bash -c "npm ci"',
            "sh -c 'pnpm install'",
            'sh -c "vite dev"',
            'bash -c "expo start"',
            'echo $(npm ci)',
            'VAR=`pnpm test`',
        ):
            with self.subTest(command=command):
                self.refused(command)

    def test_refusal_does_not_echo_raw_command(self):
        secret_cmd = "npm ci --token SECRET_KEY_12345_XYZ"
        err = self.refused(secret_cmd)
        self.assertNotIn("SECRET_KEY_12345_XYZ", err)
        self.assertNotIn("npm ci --token", err)


class DockerCommandsAllowed(Base):
    def test_docker_run_allowed(self):
        for command in (
            'docker run -v $(pwd):/app -w /app node:20 sh -c "npm ci"',
            'docker run --rm -it node:20 npm test',
            'docker run --rm node:20 pnpm install',
            'docker run -p 3000:3000 node:20 vite dev',
            'docker run -p 3000:3000 node:20 next dev',
            'docker run --rm node:20 bun test',
            'sudo docker run --rm node:20 npm ci',
        ):
            with self.subTest(command=command):
                self.allowed(command)

    def test_docker_exec_allowed(self):
        for command in (
            "docker exec -it my-container npm test",
            "docker exec app pnpm install",
            "docker exec web vite build",
            "docker exec web bun run dev",
        ):
            with self.subTest(command=command):
                self.allowed(command)

    def test_docker_compose_allowed(self):
        for command in (
            "docker compose run --rm app npm test",
            "docker compose exec web npm run build",
            "docker compose run --rm frontend pnpm install",
            "docker compose exec app bun test",
            "docker-compose run app npm ci",
            "docker-compose exec web pnpm test",
            "docker compose up -d",
            "docker compose ps",
        ):
            with self.subTest(command=command):
                self.allowed(command)


class EverydayAndReadonlyAllowed(Base):
    def test_everyday_tools_allowed(self):
        for command in (
            "python3 ~/.claude/scripts/session_to_md.py",
            "python3 ~/.claude/scripts/hub_digest.py",
            "git status",
            "git diff",
            "git log -n 5",
            "git branch -a",
            "gh pr view 12",
            "gh pr list",
            "gh repo view",
            "ls -la",
            "cat package.json",
            "pwd",
        ):
            with self.subTest(command=command):
                self.allowed(command)

    def test_informational_versions_allowed(self):
        for command in (
            "npm --version",
            "npm -v",
            "pnpm --version",
            "pnpm -v",
            "yarn --version",
            "yarn -v",
            "bun --version",
            "bun -v",
            "npx --version",
            "vite --version",
            "next --version",
            "expo --version",
            "expo doctor",
        ):
            with self.subTest(command=command):
                self.allowed(command)

    def test_mentions_in_strings_and_grep_allowed(self):
        for command in (
            'echo "npm ci"',
            "echo 'pnpm install'",
            'echo "run: npm test"',
            'grep -rn "npm" .',
            'grep "vite dev" package.json',
            'git commit -m "always Docker first: run npm ci in container"',
            'git log --grep="npm test"',
            'gh pr create --title "feature" --body "ran npm ci in docker container"',
            'python3 -c "print(\'npm install\')"',
        ):
            with self.subTest(command=command):
                self.allowed(command)


class ChainedCommands(Base):
    def test_pipe_and_chains_refuse_when_host_npm_present(self):
        for command in (
            "git status && npm ci",
            "echo hello | npm test",
            "ls -la ; pnpm install",
            "cd /tmp && yarn test",
            "npm ci && git status",
            "docker compose up -d && npm test",
            "git pull || bun install",
        ):
            with self.subTest(command=command):
                self.refused(command)

    def test_chains_with_docker_only_allowed(self):
        for command in (
            "git status && docker compose up -d",
            "git pull && docker run --rm node:20 npm test",
            "echo 'Testing in docker' && docker compose run app npm test",
        ):
            with self.subTest(command=command):
                self.allowed(command)


class Exemptions(Base):
    def test_exempt_path_passes(self):
        self.allowed("npm ci", cwd=self.exempt_dir)
        self.allowed("pnpm install", cwd=self.exempt_dir)
        self.allowed("vite dev", cwd=self.exempt_dir)
        self.allowed("npm test", cwd=self.exempt_dir / "src")

    def test_cd_to_exempt_path_passes(self):
        cmd = f"cd {self.exempt_dir} && npm ci"
        self.allowed(cmd, cwd=self.non_exempt_dir)

    def test_non_exempt_path_refused(self):
        self.refused("npm ci", cwd=self.non_exempt_dir)

    def test_missing_exemption_file_refuses_everything(self):
        self.refused("npm ci", home=self.no_exempt_home, cwd=self.exempt_dir)


class WrappersAndShapesRefused(Base):
    """Ways to run the same host command without it being the first word."""

    def test_wrappers_do_not_hide_a_host_run(self):
        for command in (
            "timeout 10 npm ci",
            "nice -n 5 npm ci",
            "sudo -u deploy pnpm install",
            "sudo -E npm ci",
            "env -u FOO npm ci",
            "env -i npm ci",
            "nohup npm run dev > /dev/null 2>&1 &",
            "VAR=1 npm ci",
            'FOO="a b" npm ci',
            "corepack pnpm install",
            "xargs npm install",
            "xargs -n 1 npm install",
            "find . -name package.json -exec npm ci \\;",
            "/usr/bin/npm ci",
            "npm.cmd ci",
        ):
            with self.subTest(command=command):
                self.refused(command)

    def test_shell_blocks_and_flag_groups_do_not_hide_a_host_run(self):
        for command in (
            "bash -lc 'npm ci'",
            "sh -ec 'pnpm install'",
            "sh -c 'cd web; npm ci'",
            "(cd web && npm ci)",
            "(npm ci)",
            "{ npm ci; }",
            "if true; then npm ci; fi",
            "for d in a b; do npm ci; done",
            "echo hi\nnpm ci",
            "npm ci &",
            "npm ci 2>&1",
            'echo "$(npm ci)"',
            "echo `pnpm test`",
        ):
            with self.subTest(command=command):
                self.refused(command)

    def test_scripts_run_by_name_and_npm_spellings_refused(self):
        for command in (
            "yarn",
            "yarn lint",
            "yarn workspace web test",
            "pnpm lint",
            "pnpm vitest",
            "pnpm --filter web build",
            "pnpm exec vitest",
            "pnpx vitest",
            "bun x vitest",
            "bunx vitest",
            "npx --yes vitest",
            "npm x vitest",
            "npm cit",
            "npm install-test",
            "npm ic",
            "npm run-script build",
            "npm exec -- vitest",
            "node_modules/.bin/vite",
        ):
            with self.subTest(command=command):
                self.refused(command)


class WrappersAndShapesAllowed(Base):
    def test_docker_behind_a_wrapper_or_shell_allowed(self):
        for command in (
            "bash -c 'docker run --rm node:20 npm ci'",
            "bash -lc 'docker compose run --rm app pnpm install'",
            "timeout 600 docker run --rm node:20 npm test",
            "nice -n 5 docker compose run --rm app npm ci",
            "env FOO=1 docker exec web npm test",
            "(cd web && docker compose run --rm app npm ci)",
            "if true; then docker run --rm node:20 npm ci; fi",
            "xargs -n 1 docker run --rm node:20 npm",
        ):
            with self.subTest(command=command):
                self.allowed(command)

    def test_single_quoted_substitution_is_text_not_a_run(self):
        self.allowed("echo '$(npm ci)'")
        self.allowed("grep -n 'npm ci' README.md")
        self.allowed("echo hi # npm ci")

    def test_read_only_package_manager_calls_allowed(self):
        for command in (
            "yarn --version",
            "yarn info react",
            "pnpm list",
            "pnpm --version",
            "pnpm",
            "bun --version",
            "npm ls",
            "npm view react version",
            "npm config get registry",
            "npx --help",
            "git commit -m \"$(cat <<'EOF'\nfix\n\nnpm ci runs in docker\nEOF\n)\"",
        ):
            with self.subTest(command=command):
                self.allowed(command)


class ExemptionFileIsOnlyRead(Base):
    def test_hook_never_writes_or_creates_the_exemption_file(self):
        listed = self.afa / "docker-first-exempt.txt"
        before = (listed.read_text(), listed.stat().st_mtime_ns)
        self.refused("npm ci")
        self.allowed("npm ci", cwd=self.exempt_dir)
        self.assertEqual((listed.read_text(), listed.stat().st_mtime_ns), before)
        # no file: still none afterwards, and no folder was made for it either
        self.refused("npm ci", home=self.no_exempt_home)
        self.assertEqual(list(self.no_exempt_home.iterdir()), [])

    def test_comments_blank_lines_and_a_lookalike_folder(self):
        # the exempt entry is /…/exempt-project; /…/exempt-project-two is a different folder
        other = self.tmp / "exempt-project-two"
        other.mkdir(exist_ok=True)
        self.refused("npm ci", cwd=other)

    def test_unreadable_exemption_file_means_no_exemptions(self):
        bad_home = self.tmp / "bad-home"
        (bad_home / ".config" / "afa").mkdir(parents=True, exist_ok=True)
        (bad_home / ".config" / "afa" / "docker-first-exempt.txt").write_bytes(b"\xff\xfe\x00bad")
        self.refused("npm ci", home=bad_home, cwd=self.exempt_dir)


class MalformedAndOtherToolsAllowed(Base):
    def test_other_tools_allowed(self):
        for tool in ("Read", "Write", "Edit", "MultiEdit", "AskUserQuestion"):
            payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": "npm ci"}})
            self.assertEqual(self.run_hook(None, payload=payload), (0, ""))

    def test_malformed_input_allowed(self):
        for payload in (
            "not json",
            "",
            json.dumps([]),
            json.dumps({"tool_name": "Bash"}),
            json.dumps({"tool_name": "Bash", "tool_input": {}}),
            json.dumps({"tool_name": "Bash", "tool_input": {"command": 12345}}),
            json.dumps({"tool_name": "Bash", "tool_input": "npm ci"}),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.run_hook(None, payload=payload), (0, ""))

    def test_missing_or_odd_cwd_still_checks_the_command(self):
        for cwd in (None, 7, ""):
            payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "npm ci"}, "cwd": cwd})
            code, err = self.run_hook(None, payload=payload)
            self.assertEqual(code, 2, f"cwd={cwd!r}")
            self.assertTrue(err.startswith("BLOCKED:"))

    def test_error_while_checking_fails_open_with_one_line(self):
        # a checker fault must never stop every shell call: load the hook, break the checker, run main()
        script = (
            "import importlib.util, io, json, sys;"
            f"s = importlib.util.spec_from_file_location('g', {str(HOOK)!r});"
            "g = importlib.util.module_from_spec(s); s.loader.exec_module(g);"
            "g.check_command_text = lambda *a: 1/0;"
            "sys.stdin = io.StringIO(json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'npm ci'}}));"
            "sys.exit(g.main())"
        )
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env={"HOME": str(self.home)})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip(), "docker-first-guard: allowed after an error (ZeroDivisionError)")


if __name__ == "__main__":
    unittest.main()
