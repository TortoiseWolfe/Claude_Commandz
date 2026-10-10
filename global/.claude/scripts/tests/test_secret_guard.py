import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(__file__), "..", "..", "hooks", "secret-guard.py")
# Rule E reads real env files; point it at an empty tree so these tests never touch this machine's.
_KS_TMP = tempfile.TemporaryDirectory()
os.environ["KNOWN_SECRETS_REPOS"] = os.path.join(_KS_TMP.name, "repos")
os.environ["KNOWN_SECRETS_IGNORE"] = os.path.join(_KS_TMP.name, "ignore.txt")
os.environ["KNOWN_SECRETS_CACHE"] = os.path.join(_KS_TMP.name, "cache.json")
os.makedirs(os.environ["KNOWN_SECRETS_REPOS"])
spec = importlib.util.spec_from_file_location("secret_guard", HOOK)
sg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sg)

GHO = "gho_" + "a1B2" * 10
JWT = "eyJhbGciOiJIUzI1" + ".eyJzdWIiOiIxMjM0NTY3" + ".abcdefghijklmnop"


def bash(cmd):
    return sg.evaluate({"tool_name": "Bash", "tool_input": {"command": cmd}})


class Base(unittest.TestCase):
    def blocked(self, cmd, rule=None):
        r = bash(cmd)
        self.assertIsNotNone(r, "should block: " + cmd)
        if rule:
            self.assertEqual(r[0], rule, cmd)
        return r

    def allowed(self, cmd):
        self.assertIsNone(bash(cmd), "should allow: " + cmd)


class CredentialsWord(Base):
    """The bare word is prose or a search pattern, not a path; a credentials FILE still blocks."""
    def test_prose_and_patterns_are_allowed(self):
        self.allowed('grep -n -i "credentials" notes.md')
        self.allowed("python3 - <<'EOF'\nprint('Mail and web content carry credentials too.')\nEOF")
        self.allowed("cat docs/feedback_never_leak_credentials_to_self.md")

    def test_credentials_files_still_block(self):
        self.blocked("cat ~/.aws/" + "credentials")
        self.blocked("cat ./credentials.json")
        self.blocked("head -3 /root/.config/x/credentials.yml")


class RuleA(Base):
    def test_blocks(self):
        for c in [
            "export GREG_PASSWORD=hunter2pass",
            "docker run -e EMAIL_PASSWORD=hunter2pass img",
            "mycli --password hunter2pass",
            "mycli --token=abcdef123456",
            "API_KEY='abcdef123456' ./run.sh",
            "curl -H 'Authorization: token %s' https://api.github.com" % GHO,
            "echo ghp_" + "x" * 30,
            "echo github_pat_" + "A" * 30,
            "echo sk-" + "a" * 24,
            "echo sk_live_" + "a" * 12,
            "echo AKIA" + "A" * 16,
            "echo ASIA" + "B" * 16,
            "echo AIza" + "c" * 35,
            "echo xoxb-1234567890-abc",
            "echo glpat-" + "d" * 22,
            "curl -H 'Authorization: Bearer %s' x" % JWT,
            "echo '-----BEGIN RSA PRIVATE KEY-----'",
            "curl -X POST https://discord.com/api/" + "webhooks/42/" + "Yz9" * 22 + " -d x",
            "export DISCORD_WEBHOOK=https://discord.com/api/" + "webhooks/42/" + "Yz9" * 22,
            "curl -d x https://hooks.slack.com/" + "services/T0AAA/B0BBB/" + "Qx7" * 8,
        ]:
            self.blocked(c, "A")

    def test_allows(self):
        for c in [
            "GREG_PASSWORD=$GREG_PASSWORD make test",
            "GREG_PASSWORD=${GREG_PASSWORD} make test",
            'TOKEN="$(cat tokenfile)" make',
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS=32000",
            "export PASSWORD=short",
            "export PASSWORD=changeme-please",
            "export API_KEY=your-api-key-here",
            "export API_KEY=xxxxxxxx",
            "export API_KEY=<paste-it>",
            "mycli --token-file=/home/u/tokenfile",
            "mycli --token $TOKEN",
            "git commit --author='Jane Doe <j@x.org>' -m fix",
            "export SECRET_PATH=somewhere-else",
            "ls -la",
            "xdg-open https://docs.github.com/en/webhooks/about-webhooks",
            "export DISCORD_WEBHOOK=$DISCORD_WEBHOOK",
            "grep -n WEBHOOK README.md",
        ]:
            self.allowed(c)

    def test_reason_never_echoes_secret(self):
        for c in ["export GREG_PASSWORD=hunter2pass", "echo " + GHO]:
            self.assertNotIn("hunter2pass", self.blocked(c)[1])
            self.assertNotIn(GHO, self.blocked(c)[1])


class RuleB(Base):
    def test_blocks(self):
        for c in [
            "cat ~/.claude.json", "tail -n 5 /home/u/.claude/settings.local.json",
            "jq . ~/.claude/settings.json", "grep PASSWORD .env", "cat .env.production",
            "head -3 ./app/.env.local", "cat ~/.config/gh/hosts.yml", "sed -n 1p auth.json",
            "cat ~/.ssh/id_ed25519", "cat key.pem", "cat AuthKey.p8", "cat ~/.aws/credentials",
            "cat ~/.netrc", "awk 1 .npmrc", "strings ~/.pgpass", "base64 ~/.docker/config.json",
            "cat ~/.config/gcloud/application_default.json", "cat gateways.json",
            "cat mcp-token.txt", "cat secrets/api-key", "diff .env .env.example",
            "echo hi && cat .env", "ls | cat .env", "rg PASSWORD .env",
            "echo \"$(cat .env)\"",
            "python3 -c \"import json;print(json.load(open('/home/u/.claude.json')))\"",
            "node -e \"console.log(require('fs').readFileSync('.env','utf8'))\"",
            "python3 - <<'EOF'\nprint(open('.env').read())\nEOF",
            "bash -c 'cat .env'",
        ]:
            self.blocked(c, "B")

    def test_allows(self):
        for c in [
            "python3 ~/.claude/scripts/redact_view.py ~/.claude.json",
            "grep -c PASSWORD .env", "grep -l PASSWORD .env", "grep -q X .env",
            "grep --count X .env", "grep -rl TOKEN .env.local",
            "cat .env.example", "cat .env.sample", "cat .env.template",
            "stat ~/.ssh/id_ed25519", "cat ~/.ssh/id_ed25519.pub",
            "docker run -v ~/.aws:/root/.aws:ro img", "ls ~/.claude", "ls -la .env",
            "wc -l .env", "file ~/.claude.json", "test -f ~/.claude.json", "du -sh ~/.aws/",
            "chmod 600 .env", "cp .env .env.bak", "mv .env .env.old", "rm .env",
            "git check-ignore .env", "git ls-files .env", "git log --oneline -- .env",
            "find . -name .env", "cat README.md; stat .env", "git commit -m 'cat .env docs'",
            "cat src/process.env.ts", "cat package.json", "grep foo src/app.ts",
            "python3 script.py --env-file .env",
        ]:
            self.allowed(c)


class RuleCD(unittest.TestCase):
    def ev(self, tool, **ti):
        return sg.evaluate({"tool_name": tool, "tool_input": ti})

    def test_read(self):
        self.assertEqual(self.ev("Read", file_path="/repo/.env")[0], "C")
        self.assertEqual(self.ev("Read", file_path="/h/.claude/settings.json")[0], "C")
        self.assertIn("redact_view.py", self.ev("Read", file_path="/h/.claude.json")[1])
        self.assertIsNone(self.ev("Read", file_path="/repo/.env.example"))
        self.assertIsNone(self.ev("Read", file_path="/repo/src/index.ts"))
        self.assertIsNone(self.ev("Read", file_path="/h/.ssh/id_rsa.pub"))

    def test_grep(self):
        self.assertEqual(self.ev("Grep", pattern="x", path="/repo/.env", output_mode="content")[0], "D")
        self.assertEqual(self.ev("Grep", pattern="x", glob="**/.env*", output_mode="content")[0], "D")
        self.assertIsNone(self.ev("Grep", pattern="x", path="/repo/.env"))
        self.assertIsNone(self.ev("Grep", pattern="x", path="/repo/.env", output_mode="files_with_matches"))
        self.assertIsNone(self.ev("Grep", pattern="x", path="/repo/src", output_mode="content"))

    def test_other_tools_allowed(self):
        self.assertIsNone(self.ev("Edit", file_path="/repo/.env"))
        self.assertIsNone(self.ev("Glob", pattern="*.env"))


class Process(unittest.TestCase):
    def run_hook(self, stdin, env=None):
        e = dict(os.environ, **(env or {}))
        return subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True, text=True, env=e)

    def test_exit_codes(self):
        ok = self.run_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls ~/.claude"}}))
        self.assertEqual((ok.returncode, ok.stderr), (0, ""))
        bad = self.run_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat ~/.claude.json"}}))
        self.assertEqual(bad.returncode, 2)
        self.assertIn("redact_view.py", bad.stderr)

    def test_malformed_stdin_allows_and_logs(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "sub", "secret-guard.log")
            for stdin in ["{not json", "[1,2]", ""]:
                r = self.run_hook(stdin, {"SECRET_GUARD_LOG": log})
                self.assertEqual((r.returncode, r.stderr), (0, ""))
            with open(log) as fh:
                content = fh.read()
            lines = content.splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(oct(os.stat(log).st_mode & 0o777), "0o600")
            self.assertEqual(oct(os.stat(os.path.dirname(log)).st_mode & 0o777), "0o700")
            self.assertNotIn("not json", content)


class RuleE(unittest.TestCase):
    """A file holding a real env-file secret value is never read to the screen, whatever its name."""
    PW = "Pq7" * 6

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        repos = os.path.join(self.tmp.name, "repos", "app")
        os.makedirs(repos)
        with open(os.path.join(repos, "." + "env"), "w") as fh:
            fh.write("ADMIN_PASSWORD=%s\nPORT=3000\n" % self.PW)
        self.note = os.path.join(self.tmp.name, "note.md")
        with open(self.note, "w") as fh:
            fh.write("- URL: https://example.org\n- Login: jon / %s\n" % self.PW)
        self.clean = os.path.join(self.tmp.name, "clean.md")
        with open(self.clean, "w") as fh:
            fh.write("PORT=3000\n")
        self.saved = (sg.KS_REPOS, sg.KS_CACHE, sg._ks_values)
        sg.KS_REPOS, sg.KS_CACHE, sg._ks_values = (os.path.join(self.tmp.name, "repos"),
                                                   os.path.join(self.tmp.name, "cache.json"), None)

    def tearDown(self):
        sg.KS_REPOS, sg.KS_CACHE, sg._ks_values = self.saved

    def ev(self, tool, cwd=None, **ti):
        return sg.evaluate({"tool_name": tool, "tool_input": ti, "cwd": cwd or self.tmp.name})

    def test_blocks_readers_read_and_grep_content(self):
        for cmd in ["cat %s" % self.note, "sed -n 1,30p %s | grep -v -i password" % self.note,
                    "head note.md", "python3 ~/.claude/scripts/redact_view.py %s" % self.note,
                    "grep -n Login %s" % self.note]:
            r = self.ev("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertEqual(r[0], "E", cmd)
            self.assertNotIn(self.PW, r[1])
            self.assertIn("app/." + "env ADMIN_PASSWORD", r[1])
        self.assertEqual(self.ev("Read", file_path=self.note)[0], "E")
        self.assertEqual(self.ev("Grep", pattern="Login", path=self.note, output_mode="content")[0], "E")

    def test_allows_clean_files_counts_and_non_readers(self):
        for cmd in ["cat %s" % self.clean, "grep -c Login %s" % self.note, "wc -l %s" % self.note,
                    "stat %s" % self.note, "ls %s" % self.tmp.name]:
            self.assertIsNone(self.ev("Bash", command=cmd), cmd)
        self.assertIsNone(self.ev("Read", file_path=self.clean))
        self.assertIsNone(self.ev("Grep", pattern="Login", path=self.note, output_mode="count"))

    def test_env_list_is_cached_and_values_read_fresh(self):
        self.ev("Read", file_path=self.note)
        self.assertTrue(os.path.exists(sg.KS_CACHE))
        self.assertEqual(oct(os.stat(sg.KS_CACHE).st_mode & 0o777), "0o600")
        self.assertNotIn(self.PW, open(sg.KS_CACHE).read())


if __name__ == "__main__":
    unittest.main()
