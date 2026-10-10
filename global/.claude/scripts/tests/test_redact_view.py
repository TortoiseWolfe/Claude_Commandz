import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "redact_view.py")
spec = importlib.util.spec_from_file_location("redact_view", SCRIPT)
rv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rv)

GHO = "gho_" + "a1B2" * 10  # 44 chars
PW = "hunter2pass"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text):
        p = os.path.join(self.tmp.name, name)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)


class Json(Fixture):
    def doc(self):
        return {
            "apiKey": "supersecretvalue",
            "max_output_tokens": 32000,
            "enabled": True,
            "name": "demo",
            "auth": {"user": "bob", "note": "plainword"},
            "authCount": 3,
            "gh": "Bearer " + GHO,
            "mcpServers": {"m": {"command": "docker",
                                 "args": ["run", "-e", "EMAIL_PASSWORD=" + PW, "--password", "pw2literal", "img"]}},
            "projects": {"/home/x/repo.v2": {"mcpServers": {"k": {"token": "abcdef"}}, "n": 1}},
        }

    def test_masks_and_keeps(self):
        p = self.write("c.json", json.dumps(self.doc()))
        r = self.run_cli(p)
        self.assertEqual(r.returncode, 0)
        out = r.stdout
        for leak in ["supersecretvalue", PW, "pw2literal", GHO, "abcdef\""]:
            self.assertNotIn(leak, out)
        self.assertIn("<redacted:16>", out)
        self.assertIn("32000", out)
        self.assertIn('"enabled": true', out)
        self.assertIn('"name": "demo"', out)
        self.assertIn("gho_…[44]", out)
        self.assertIn("EMAIL_PASSWORD=<redacted:%d>" % len(PW), out)
        self.assertNotIn("bob", out)  # children of a secret-named key are masked
        self.assertIn('"authCount": 3', out)

    def test_path_dotted_and_double_colon(self):
        p = self.write("c.json", json.dumps(self.doc()))
        a = self.run_cli(p, "--path", "projects./home/x/repo.v2.mcpServers")
        b = self.run_cli(p, "--path", "projects::/home/x/repo.v2::mcpServers")
        self.assertEqual(a.returncode, 0)
        self.assertEqual(a.stdout, b.stdout)
        self.assertIn('"k"', a.stdout)
        self.assertNotIn("abcdef\"", a.stdout)
        self.assertEqual(self.run_cli(p, "--path", "nope.x").returncode, 1)

    def test_keys_only(self):
        p = self.write("c.json", json.dumps(self.doc()))
        out = self.run_cli(p, "--keys-only").stdout
        self.assertIn("apiKey: string(len=16)", out)
        self.assertIn("enabled: boolean", out)
        self.assertIn("max_output_tokens: number", out)
        for leak in ["supersecretvalue", PW, "demo", "docker"]:
            self.assertNotIn(leak, out)

    def test_pem_and_jwt_token_shapes(self):
        jwt = "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY3.abcdefghijklmnop"
        pem = "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADAN\n-----END PRIVATE KEY-----"
        p = self.write("c.json", json.dumps({"a": jwt, "b": "x " + pem, "c": "AKIA" + "A" * 16}))
        out = self.run_cli(p).stdout
        self.assertNotIn("MIIBVQ", out)
        self.assertNotIn("abcdefghijklmnop", out)
        self.assertIn("eyJh…[%d]" % len(jwt), out)
        self.assertIn("AKIA…[20]", out)


class TextFormats(Fixture):
    def test_dotenv(self):
        p = self.write(".env", "export GREG_PASSWORD=%s\nPORT=3000\nCLAUDE_CODE_MAX_OUTPUT_TOKENS=32000\n"
                       "DB_URL=\"postgres://x\"\nGH=%s\nAUTH_ENABLED=true\n# note\n" % (PW, GHO))
        out = self.run_cli(p).stdout
        self.assertNotIn(PW, out)
        self.assertNotIn(GHO, out)
        self.assertIn("export GREG_PASSWORD=<redacted:%d>" % len(PW), out)
        self.assertIn("PORT=3000", out)
        self.assertIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS=32000", out)
        self.assertIn("AUTH_ENABLED=true", out)
        self.assertIn("GH=gho_…[44]", out)
        self.assertIn("# note", out)

    def test_ini_toml(self):
        p = self.write("c.toml", "[core]\ntoken = \"abc123def\"\nname = \"x\"\nsecret_key=\"s3cr3t\"\n")
        out = self.run_cli(p).stdout
        self.assertNotIn("abc123def", out)
        self.assertNotIn("s3cr3t", out)
        self.assertIn("[core]", out)
        self.assertIn('name = "x"', out)

    def test_yaml(self):
        p = self.write("h.yml", "github.com:\n  oauth_token: %s\n  user: bob\n  password: %s\n"
                       "cmd:\n  - -e EMAIL_PASSWORD=%s\n" % (GHO, PW, PW))
        out = self.run_cli(p).stdout
        self.assertNotIn(PW, out)
        self.assertNotIn(GHO, out)
        self.assertIn("user: bob", out)
        self.assertIn("github.com:", out)

    def test_plain_text_and_inline_fragments(self):
        p = self.write("n.txt", "run: mysql --password=%s -h db\nthen use -p %s now\nAuthorization: Bearer abcdef\n"
                       "hello world\n" % (PW, PW))
        out = self.run_cli(p).stdout
        self.assertNotIn(PW, out)
        self.assertNotIn("abcdef", out)
        self.assertIn("hello world", out)

    def test_text_keys_only(self):
        p = self.write(".env", "A_TOKEN=%s\nB=1\n" % PW)
        out = self.run_cli(p, "--keys-only").stdout
        self.assertNotIn(PW, out)
        self.assertIn("A_TOKEN=<value len %d>" % len(PW), out)

    def test_credentials_inside_urls(self):
        # A webhook's token is a path segment, so neither the key name nor a token shape gives it
        # away. Fixtures are built at runtime so
        # gitleaks doesn't flag this file.
        tok = "Yz9" * 22
        hook = "https://discord.com/api/" + "webhooks/1555929930645110804/" + tok
        slack = "https://hooks.slack.com/services/" + "T0AAA/B0BBB/" + "Qx7" * 8
        p = self.write(".env", "DISCORD_ANNOUNCE_WEBHOOK=%s\nNOTIFY=%s\nSLACK=%s\n"
                       "DATABASE_URL=postgresql://app:%s@db.example.com:5432/x\n"
                       "SITE=https://example.com/page\n" % (hook, hook, slack, PW))
        out = self.run_cli(p).stdout
        for leak in [tok, "Qx7Qx7", PW]:
            self.assertNotIn(leak, out)
        self.assertIn("DISCORD_ANNOUNCE_WEBHOOK=<redacted:%d>" % len(hook), out)
        self.assertIn("NOTIFY=https://discord.com/api/webhooks/<redacted:", out)
        self.assertIn("SLACK=https://hooks.slack.com/services/<redacted:", out)
        self.assertIn("DATABASE_URL=postgresql://app:<redacted:%d>@db.example.com:5432/x" % len(PW), out)
        self.assertIn("SITE=https://example.com/page", out)

    def test_multiline_private_key(self):
        p = self.write("k.txt", "key:\n-----BEGIN RSA PRIVATE KEY-----\nAAAABBBB\n-----END RSA PRIVATE KEY-----\nok\n")
        out = self.run_cli(p).stdout
        self.assertNotIn("AAAABBBB", out)
        self.assertIn("ok", out)


class Misc(Fixture):
    def test_unreadable(self):
        r = self.run_cli(os.path.join(self.tmp.name, "missing.json"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("redact_view", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_path_on_non_json_errors(self):
        p = self.write("a.env", "A=1\n")
        self.assertEqual(self.run_cli(p, "--path", "x").returncode, 1)

    def test_units(self):
        self.assertEqual(rv.scrub("a -p 8080 b"), "a -p 8080 b")
        self.assertEqual(rv.scrub("X_TOKEN=$X_TOKEN"), "X_TOKEN=$X_TOKEN")
        self.assertEqual(rv.scrub("PASSWORD=" + PW), "PASSWORD=<redacted:%d>" % len(PW))
        self.assertEqual(rv.walk({"max_output_tokens": "32000", "k": "v"}), {"max_output_tokens": "32000", "k": "v"})
        tok = "Yz9" * 22
        self.assertEqual(rv.scrub("curl -X POST https://discordapp.com/api/" + "webhooks/42/" + tok + " -d x"),
                         "curl -X POST https://discordapp.com/api/webhooks/<redacted:%d> -d x" % len("42/" + tok))
        self.assertEqual(rv.walk({"url": "https://outlook.office.com/" + "webhook/" + tok}),
                         {"url": "https://outlook.office.com/webhook/<redacted:%d>" % len(tok)})
        self.assertEqual(rv.scrub("ssh://git@github.com:22/x.git"), "ssh://git@github.com:22/x.git")
        self.assertEqual(rv.scrub("see https://example.com/docs/webhooks/"), "see https://example.com/docs/webhooks/")


if __name__ == "__main__":
    unittest.main()
