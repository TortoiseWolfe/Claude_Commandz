"""Tests for scripts/secret_scrubber.py: the library and the --filter mode. Every secret here is made up."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..")
sys.path.insert(0, SCRIPTS)
import secret_scrubber as ss  # noqa: E402

LIB = os.path.join(SCRIPTS, "secret_scrubber.py")
# Every made-up value below is put together at run time from pieces, so a leak scanner reading this public file
# (gitleaks in CI) finds nothing shaped like a real one, while the scrubber under test still sees the whole shape.
FAKE_JWT = ".".join(["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "eyJmYWtlIjoidGVzdC1vbmx5Iiwicm9sZSI6Im5vYm9keSJ9",
                     "ZmFrZXNpZ25hdHVyZS1mb3ItdGVzdGluZy1vbmx5"])
FAKE_SECRET = "Zq7fakeJWTsigningValue0123456789abcdefQwErTy"
PEM_WORDS = "PRIVATE" + " " + "KEY"
FAKE_PEM = ("-----BEGIN " + PEM_WORDS + "-----\nZmFrZWtleWJvZHlsaW5lMQ==\nZmFrZWtleWJvZHlsaW5lMg==\n-----END "
            + PEM_WORDS + "-----")
FAKE_KNOWN = "Kx9!fake-known-value-for-tests-only-77Q"
FAKE_AKIA = "AKIA" + "ABCDEFGHIJKLMNOP"
PLAIN_A = "hunter2" + "hunter2"
PLAIN_B = "ZZfakeValue" + "99887766"
PLAIN_C = "abc123" + "def456ghi"
PLAIN_D = "Ab12Cd34Ef56" + "Gh78Ij90Kl"
PLAIN_E = "FAKEq7Zp2LmX9vTr" + "4KdW8sNb3HcY6uJe"


def scrub(text, tier=2):
    return ss.scrub_text(text, tier, needles=[])


class Library(unittest.TestCase):
    def test_postgrest_style_reply(self):
        reply = json.dumps({"db_schemas": "public", "jwt_secret": FAKE_SECRET, "max_rows": 1000, "version": "12.2"})
        out, n = scrub(reply)
        self.assertNotIn(FAKE_SECRET, out)
        self.assertEqual(n, 1)
        got = json.loads(out)
        self.assertEqual(got["jwt_secret"], "<redacted:field jwt_secret>")
        self.assertEqual((got["db_schemas"], got["max_rows"], got["version"]), ("public", 1000, "12.2"))

    def test_bare_jwt_in_plain_text(self):
        out, n = scrub("the reply said %s and then stopped" % FAKE_JWT, 1)
        self.assertEqual(out, "the reply said <redacted:jwt> and then stopped")
        self.assertEqual(n, 1)

    def test_supabase_and_other_shapes(self):
        text = "a sbp_%s b sb_secret_%s c ghp_%s" % ("a1" * 15, "Zz9-" * 4, "x" * 24)
        out, n = scrub(text, 1)
        self.assertEqual(n, 3)
        self.assertIn("<redacted:supabase-key>", out)
        self.assertNotIn("sbp_a1", out)

    def test_private_key_block_in_one_string(self):
        out, n = scrub("before\n%s\nafter" % FAKE_PEM, 1)
        self.assertEqual(out, "before\n<redacted:private-key>\nafter")
        self.assertEqual(n, 1)

    def test_url_password_only(self):
        out, _ = scrub("postgres://app:s3cretPassw0rd@db.example.test:5432/app", 1)
        self.assertEqual(out, "postgres://app:<redacted:url-password>@db.example.test:5432/app")

    def test_url_password_variable_left(self):
        text = "postgres://app:${DB_PASS}@db/app"
        self.assertEqual(scrub(text, 1), (text, 0))

    def test_auth_and_api_key_headers(self):
        out, n = scrub("Authorization: Bearer abcdEFGH12345678\nx-api-key: ZZtopfake9876543210", 1)
        self.assertEqual(out, "Authorization: Bearer <redacted:auth-header>\nx-api-key: <redacted:auth-header>")
        self.assertEqual(n, 2)

    def test_header_with_variable_left(self):
        text = 'curl -H "Authorization: Bearer $TOKEN" https://x.test'
        self.assertEqual(scrub(text, 1), (text, 0))

    def test_base64_run_skips_shapes(self):
        blob = "A" * 60 + FAKE_AKIA + "B" * 150     # a pure base64 run holding a chance AKIA match
        self.assertEqual(scrub("img " + blob, 1), ("img " + blob, 0))
        self.assertEqual(scrub("key " + FAKE_AKIA, 1)[1], 1)

    def test_known_value_masked_in_base64_run(self):
        # known values still apply inside a long run
        run = "A" * 120 + FAKE_KNOWN.replace("!", "").replace("-", "") + "B" * 120
        out, n = ss.scrub_text(run, 1, needles=[(FAKE_KNOWN.replace("!", "").replace("-", ""), "known-secret")])
        self.assertEqual(n, 1)

    # ---- tier 2
    def test_fields_in_three_notations(self):
        text = 'DB_PASSWORD=%s\napi_key: %s\n{"private_key": "%s"}' % (PLAIN_A, PLAIN_B, PLAIN_C)
        out, n = scrub(text)
        self.assertEqual(n, 3)
        self.assertIn("DB_PASSWORD=<redacted:field DB_PASSWORD>", out)
        self.assertIn("api_key: <redacted:field api_key>", out)
        self.assertIn('"private_key": "<redacted:field private_key>"', out)

    def test_camel_case_and_compound_names(self):
        out, n = scrub('{"serviceRoleKey":"fakeValueABC123xyz","jwtSecret":"anotherFake998877"}')
        self.assertEqual(n, 2)
        self.assertNotIn("fakeValueABC123xyz", out)

    def test_escaped_json_inside_a_string(self):
        out, n = scrub('body: {\\"client_secret\\": \\"fakeValue12345678\\"}')
        self.assertEqual(n, 1)
        self.assertNotIn("fakeValue12345678", out)

    def test_name_skip_words(self):
        for line in ('"next_page_token": "CAESBggBEAEYAg9876"', "token_count = 12345", '"token_type": "Bearerish"',
                     '"secret_name": "prod-db-secret-x"', "password_path: /run/secrets/dbpassword",
                     '"expires_in_token": "3600s"', "max_tokens: 4096", "token_id=abc123def"):
            self.assertEqual(scrub(line), (line, 0), line)

    def test_value_skip_rules(self):
        for line in ("password = 12345678", "secret: true", 'token: null', 'password: ""', "password=$DB_PASS",
                     "password=${DB_PASS}", "password = os.environ.get", 'password = os.environ["X"]',
                     "token = get_token()", '"token": "access_token"', "password: <redacted:url-password>",
                     "password: ********", "token == other_value", "secret: required"):
            self.assertEqual(scrub(line), (line, 0), line)

    def test_bare_key_needs_random_value(self):
        self.assertEqual(scrub("key: value_here")[1], 0)
        self.assertEqual(scrub('"key": "sort"')[1], 0)
        out, n = scrub('"key": "%s"' % PLAIN_D)
        self.assertEqual(n, 1)

    def test_tier1_leaves_fields_alone(self):
        text = 'password = "hunter2hunter2"'
        self.assertEqual(scrub(text, 1), (text, 0))

    def test_plain_text_unchanged(self):
        log = "\n".join("%07x fix: handle the token refresh path (#%d)" % (i * 977, i) for i in range(50))
        self.assertEqual(scrub(log), (log, 0))

    def test_walk_keeps_shape_and_catches_structured_fields(self):
        obj = {"stdout": "x " + FAKE_JWT, "n": 3, "ok": True, "list": ["a", {"jwt_secret": FAKE_SECRET}], "none": None}
        new, n = ss.scrub_obj(obj, 2, needles=[])
        self.assertEqual(n, 2)
        self.assertEqual(new["stdout"], "x <redacted:jwt>")
        self.assertEqual((new["n"], new["ok"], new["none"]), (3, True, None))
        self.assertEqual(new["list"][1], {"jwt_secret": "<redacted:field jwt_secret>"})

    def test_walk_tier1_does_not_mask_fields(self):
        obj = {"file": {"content": 'password = "hunter2hunter2"'}}
        self.assertEqual(ss.scrub_obj(obj, 1, needles=[]), (obj, 0))

    def test_performance_on_200kb(self):
        text = ("INFO 2026-11-11 request served path=/api/items status=200 took=12ms user=anon\n" * 2500)[:200000]
        t = time.time()
        self.assertEqual(scrub(text)[1], 0)
        self.assertLess(time.time() - t, 3)


class Hardening(unittest.TestCase):
    """Step 2 additions: label rule, name/value pairs, bare values to end of line, LineFilter."""

    def setUp(self):       # no known-values lookup (it would write the live cache file)
        self._saved = ss._needles
        ss._needles = []
        self.addCleanup(setattr, ss, "_needles", self._saved)

    # ---- 1. label rule
    def test_value_with_secret_word_but_not_identifier_is_masked(self):
        for line, name in (('{"jwt_secret":"super-fake-jwt-token-with-at-least-32-characters-long"}', "jwt_secret"),
                           ('"password": "mytokenpass1"', "password")):
            out, n = scrub(line)
            self.assertEqual(n, 1, line)
            self.assertIn("<redacted:field %s>" % name, out)
            self.assertNotIn("mytokenpass1", out)
            self.assertNotIn("super-fake", out)

    def test_identifier_like_labels_stay(self):
        for line in ('"token": "access_token"', "password = os.environ.get", '"secret": "SUPABASE_JWT_SECRET"',
                     'api_key: "MY_API_KEY_VAR"'):
            self.assertEqual(scrub(line), (line, 0), line)

    def test_long_identifier_is_not_a_label(self):
        v = "a_token_" + "x" * 40     # identifier characters but over 40 characters
        self.assertEqual(scrub('"password": "%s"' % v)[1], 1)

    # ---- 2. name and value in separate keys
    def test_name_value_pair_in_text_both_orders(self):
        text = ('[{"name":"JWT_SECRET","value":"FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe"},{"name":"REGION","value":"us-east-1"},'
                '{"value":"FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe","key":"DB_PASSWORD"}]')
        out, n = scrub(text)
        self.assertEqual(n, 2)
        self.assertNotIn("FAKEq7Zp", out)
        self.assertIn('{"name":"JWT_SECRET","value":"<redacted:field JWT_SECRET>"}', out)
        self.assertIn('{"name":"REGION","value":"us-east-1"}', out)
        self.assertIn('{"value":"<redacted:field DB_PASSWORD>","key":"DB_PASSWORD"}', out)

    def test_name_value_pair_pretty_printed_and_escaped(self):
        out, n = scrub('{\n  "name": "API_KEY",\n  "value": "FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe"\n}')
        self.assertEqual(n, 1)
        self.assertNotIn("FAKEq7Zp", out)
        out, n = scrub('body: {\\"name\\":\\"CLIENT_SECRET\\",\\"value\\":\\"FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe\\"}')
        self.assertNotIn("FAKEq7Zp", out)

    def test_name_value_pair_in_dict(self):
        obj = {"env": [{"name": "JWT_SECRET", "value": "FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe"},
                       {"key": "REGION", "value": "us-east-1"}, {"value": "FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe", "key": "DB_PASSWORD"}]}
        new, n = ss.scrub_obj(obj, 2, needles=[])
        self.assertEqual(n, 2)
        self.assertEqual(new["env"][0], {"name": "JWT_SECRET", "value": "<redacted:field JWT_SECRET>"})
        self.assertEqual(new["env"][1], {"key": "REGION", "value": "us-east-1"})
        self.assertEqual(new["env"][2]["value"], "<redacted:field DB_PASSWORD>")
        self.assertEqual(ss.scrub_obj({"name": "JWT_SECRET", "value": "FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe"}, 1, needles=[])[1], 0)

    # ---- 3. bare values
    def test_bare_value_at_line_start_runs_to_end_of_line(self):
        for line, name in (("DB_PASSWORD=FAKEpw;with&odd|chars(here)", "DB_PASSWORD"),
                           ("password: FAKE pass phrase with spaces", "password"),
                           ("  export DB_PASSWORD=FAKEpw;with&odd|chars(here)", "DB_PASSWORD"),
                           ("declare -x API_SECRET=FAKEpw;tail&more", "API_SECRET"),
                           ("set password=FAKEpw tail words", "password"),
                           ("- token: FAKEpw;tail&more", "token")):
            out, n = scrub(line)
            self.assertEqual(n, 1, line)
            self.assertTrue(out.endswith("<redacted:field %s>" % name), out)
            self.assertNotIn("FAKE", out)

    def test_bare_value_elsewhere_runs_to_whitespace(self):
        out, n = scrub("run with DB_PASSWORD=FAKEpw;with&odd|chars(here) now")
        self.assertEqual(out, "run with DB_PASSWORD=<redacted:field DB_PASSWORD> now")
        out, _ = scrub("call(x, password='FAKEpw;x') and token=abc;def&ghi ok")
        self.assertNotIn("abc;def", out)

    def test_bare_code_calls_and_trailing_punctuation_are_left_alone(self):
        for line in ("token = get_token()", 'password = os.environ["X"]', "x = 1; token = fetch(a, b)",
                     '  "password": null,', '"secret": true}', "token: 12345,", "password = getpass.getpass()"):
            self.assertEqual(scrub(line), (line, 0), line)

    # ---- 4. stream class
    def test_linefilter_holds_partial_line_until_newline(self):
        lf = ss.LineFilter()
        self.assertEqual(lf.feed(b"jwt_secret=FAKEq7Zp2L"), b"")
        self.assertEqual(lf.feed(b"mX9vTr\nnext "), b"jwt_secret=<redacted:field jwt_secret>\n")
        self.assertEqual(lf.finish(), b"next ")

    def test_linefilter_private_key_block_across_feeds(self):
        lf = ss.LineFilter()
        out = lf.feed(b"a\n-----BEGIN PRIVATE KEY-----\nZmFrZQ==\n") + lf.feed(b"ZmFrZTI=\n-----END PRIVATE KEY-----\nb\n")
        self.assertEqual(out, b"a\n<redacted:private-key>\nb\n")

    def test_linefilter_long_line_is_complete(self):
        lf = ss.LineFilter()
        lf.MAX_LINE = 1000
        out = lf.feed(b"x" * 2500)
        self.assertEqual(len(out), 2000)
        self.assertEqual(lf.finish(), b"x" * 500)

    def test_linefilter_error_returns_only_notices(self):
        lf = ss.LineFilter()
        old = os.environ.get("SECRET_SCRUBBER_FAIL")
        os.environ["SECRET_SCRUBBER_FAIL"] = "1"
        try:
            out = lf.feed(("first %s\nsecond\npart" % FAKE_JWT).encode())
            self.assertEqual(out, (ss.WITHHELD + "\n").encode() * 2)
            self.assertEqual(lf.finish(), (ss.WITHHELD + "\n").encode())
            self.assertEqual(lf.feed(b"more\n"), (ss.WITHHELD + "\n").encode())
        finally:
            if old is None:
                del os.environ["SECRET_SCRUBBER_FAIL"]
            else:
                os.environ["SECRET_SCRUBBER_FAIL"] = old

    def test_linefilter_flush_partial_scrubs(self):
        lf = ss.LineFilter()
        lf.feed(b"got %s ok" % FAKE_JWT.encode())
        self.assertEqual(lf.flush_partial(), b"got <redacted:jwt> ok")
        self.assertEqual(lf.finish(), b"")

    def test_filter_cli_handles_the_adversarial_cases(self):
        text = ('{"jwt_secret":"super-fake-jwt-token-with-at-least-32-characters-long"}\n'
                "DB_PASSWORD=FAKEpw;with&odd|chars(here)\npassword: FAKE pass phrase with spaces\ntoken = get_token()\n")
        r = run_filter(text, env={"KNOWN_SECRETS_REPOS": tempfile.gettempdir() + "/no-such-dir"})
        self.assertEqual(r.stdout.splitlines(), ['{"jwt_secret":"<redacted:field jwt_secret>"}',
                                                 "DB_PASSWORD=<redacted:field DB_PASSWORD>",
                                                 "password: <redacted:field password>", "token = get_token()"])


class CodeAndFlags(unittest.TestCase):
    """Ordinary source code passes through unchanged; command-line flags with a secret name are masked."""

    def keep(self, line, tiers=(1, 2)):
        for t in tiers:
            self.assertEqual(scrub(line, t)[0], line, "tier %d changed: %s" % (t, line))

    def test_flag_values_are_masked(self):
        self.assertEqual(scrub("mysql -u root --password=FAKEq7Zp2LmX9vTr4KdW8s -h db")[0],
                         "mysql -u root --password=<redacted:field password> -h db")
        self.assertEqual(scrub("wp user update 3 --user_pass=FAKEq7Zp2LmX")[0],
                         "wp user update 3 --user_pass=<redacted:field user_pass>")
        self.assertEqual(scrub('run "--token=FAKEq7Zp2LmX9vTr4"')[0], 'run "--token=<redacted:field token>"')

    def test_flag_help_text_is_kept(self):
        self.keep("  --token TOKEN   the bearer token to use")

    def test_header_rules_skip_code(self):
        self.keep('    authorization = request.headers.get("Authorization")')
        self.keep("  headers: { apikey: supabaseAnonKey, Authorization: `Bearer ${token}` },")
        self.keep("  authorization: req.headers.authorization,")
        self.keep("  apiKey: import.meta.env.VITE_API_KEY,")

    def test_header_rules_still_mask_credentials(self):
        self.assertEqual(scrub('curl -H "apikey: %s" https://x.example.com' % PLAIN_E, 1)[0],
                         'curl -H "apikey: <redacted:auth-header>" https://x.example.com')
        self.assertEqual(scrub("  x-api-key: FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe", 1)[0], "  x-api-key: <redacted:auth-header>")
        self.assertEqual(scrub("Authorization: Bearer FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe", 1)[0],
                         "Authorization: Bearer <redacted:auth-header>")
        self.assertEqual(scrub("Authorization: Basic ZmFrZXVzZXJmYWtlcGFzcw", 1)[0], "Authorization: Basic <redacted:auth-header>")

    def test_code_keyword_values_are_kept(self):
        self.keep("const token = await getToken();", (2,))
        self.keep("  token = await getToken();", (2,))
        self.keep("  token = new TokenSource()", (2,))

    def test_type_annotations_are_kept(self):
        self.keep("    secret: str | None = None", (2,))
        self.keep('    api_key: str = ""', (2,))
        self.keep("    token: Optional[str]", (2,))
        self.keep("  token: string;", (2,))

    def test_annotated_literal_masks_only_the_literal(self):
        self.assertEqual(scrub('    api_key: str = "FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe"')[0],
                         '    api_key: str = "<redacted:field api_key>"')

    def test_plain_colon_values_are_still_masked(self):
        self.assertEqual(scrub("password: letmeinFAKE")[0], "password: <redacted:field password>")
        self.assertEqual(scrub("password: FAKE pass phrase with spaces")[0], "password: <redacted:field password>")

    def test_operator_expression_is_kept(self):
        self.keep("    token = cfg.token or env_token", (2,))
        self.keep("    token = tok or env_token", (2,))

    def test_prose_after_a_secret_name_is_masked(self):
        self.assertEqual(scrub("Token: expired, please log in")[0], "Token: <redacted:field Token>")

    def test_one_plain_word_after_a_secret_name_mid_line_is_prose(self):
        # Changed with the word rule (W1): mid-line, a bare one-word value of letters only is a word, not a value.
        # The same shape as print("secret_scan: gitleaks did not run"), which must pass unchanged.
        self.assertEqual(scrub("- [ ] rotate the token: ask Hatch"), ("- [ ] rotate the token: ask Hatch", 0))

    def test_fixture_lines_still_masked(self):
        self.assertEqual(scrub("PGPASSWORD=FAKEhunter2 psql -h db")[0], "PGPASSWORD=<redacted:field PGPASSWORD>")
        self.assertEqual(scrub("password = FAKEhunter2")[0], "password = <redacted:field password>")
        self.assertEqual(scrub('jwt_secret = "your-super-secret-jwt-token"')[0], 'jwt_secret = "<redacted:field jwt_secret>"')


class WordValues(unittest.TestCase):
    """A value that is one plain word (a type name, a variable) is code; a real value is still masked."""

    def keep(self, line):
        self.assertEqual(scrub(line, 2)[0], line, "changed: " + line)

    def test_code_with_word_values_is_kept(self):
        self.keep("async initializeKeys(password: string): Promise<DerivedKeyPair> {")
        self.keep("    privateKey: CryptoKey;")
        self.keep("    privateKey: CryptoKey,")
        self.keep("  const table = tx.table<{ userId: string; privateKey: JsonWebKey }>(")
        self.keep("  sharedSecret: Uint8Array,")
        self.keep("  messaging_private_keys: 'userId',")
        self.keep("  colorToken: 'primary',")
        self.keep('    print("secret_scan: gitleaks did not run: %s" % e, file=sys.stderr)')
        self.keep("def scrub(text, secrets=()):")
        self.keep("                            optional=optional, secrets=None)")
        self.keep('            params = json.dumps({"sessionKey": key, "message": part, "label": "hub"}, ensure_ascii=True)')

    def test_jsx_expressions_and_optional_chains_are_kept(self):
        self.keep("            onToken={onToken}")
        self.keep("  <PasswordStrengthIndicator password={password} />")
        self.keep("                colorToken={s.colorToken}")
        self.keep("  const token = data?.session?.access_token;")
        self.keep("const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;")

    def test_real_values_are_still_masked(self):
        for line, want in [
            ("password: letmeinFAKE", "password: <redacted:field password>"),
            ("password: FAKE pass phrase with spaces", "password: <redacted:field password>"),
            ("password: hunter", "password: <redacted:field password>"),
            ("DB_PASSWORD=hunter", "DB_PASSWORD=<redacted:field DB_PASSWORD>"),
            ("password = hunter", "password = <redacted:field password>"),
            ("PGPASSWORD=hunter psql -h db", "PGPASSWORD=<redacted:field PGPASSWORD>"),
            ("mysql -u root --password=hunter -h db", "mysql -u root --password=<redacted:field password> -h db"),
            ('"password": "hunter"', '"password": "<redacted:field password>"'),
            ('"password": "mytokenpass1"', '"password": "<redacted:field password>"'),
            ('{"jwt_secret":"FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe"}', '{"jwt_secret":"<redacted:field jwt_secret>"}'),
            ('"client_secret": "FAKEabcdefghijklmnopqrstuvwxyzabcd"', '"client_secret": "<redacted:field client_secret>"'),
            ('api_key: str = "FAKEq7Zp2LmX9vTr4KdW8sNb3HcY6uJe"', 'api_key: str = "<redacted:field api_key>"'),
            ("token: FAKEq7Zp2LmX9vTr4KdW8s;", "token: <redacted:field token>"),
        ]:
            self.assertEqual(scrub(line, 2)[0], want, line)

    def test_a_word_in_a_secret_named_quoted_field_is_judged_by_the_name(self):
        self.assertEqual(scrub("  colorToken: 'primary',", 2)[0], "  colorToken: 'primary',")
        self.assertEqual(scrub('x = {"password": "hunter"}', 2)[0], 'x = {"password": "<redacted:field password>"}')

    def test_value_without_three_letters_or_digits_is_not_a_value(self):
        self.assertEqual(ss.literal_secret("()"), False)
        self.assertEqual(ss.literal_secret("{}"), False)
        self.assertEqual(ss.literal_secret("[]"), False)
        self.assertEqual(ss.literal_secret('""'), False)
        self.assertEqual(ss.literal_secret("!@#$%^&*"), True)    # symbols alone can still be a password


class KnownValues(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sec = os.path.join(self.tmp, "secrets")
        os.makedirs(self.sec)
        with open(os.path.join(self.sec, "fake_api_token.txt"), "w") as fh:
            fh.write(FAKE_KNOWN + "\n")
        os.makedirs(os.path.join(self.tmp, "repos"))
        self.env = dict(os.environ, KNOWN_SECRETS_REPOS=os.path.join(self.tmp, "repos"), KNOWN_SECRETS_DIR=self.sec,
                        KNOWN_SECRETS_IGNORE=os.path.join(self.tmp, "none.txt"),
                        KNOWN_SECRETS_CACHE=os.path.join(self.tmp, "cache.json"))

    def run_py(self, code):
        r = subprocess.run([sys.executable, "-c", code], env=self.env, capture_output=True, text=True, timeout=60,
                           cwd=SCRIPTS)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_every_form_is_masked(self):
        code = (
            "import sys, json; sys.path.insert(0, '.')\n"
            "import secret_scrubber as ss, known_secrets_scan as ks\n"
            "v = %r\n"
            "forms = sorted(f.decode() for f in ks.forms(v))\n"
            "bad = []\n"
            "for f in forms:\n"
            "    out, n = ss.scrub_text('before ' + f + ' after', 1)\n"
            "    if f in out or n < 1: bad.append(len(f))\n"
            "print(json.dumps({'forms': len(forms), 'bad': bad}))\n" % FAKE_KNOWN)
        got = json.loads(self.run_py(code))
        self.assertGreaterEqual(got["forms"], 5)     # raw, json, quote, quote_plus, base64 middles
        self.assertEqual(got["bad"], [])

    def test_cache_holds_paths_only(self):
        self.run_py("import sys; sys.path.insert(0, '.'); import secret_scrubber as ss; ss.known_needles()")
        raw = open(self.env["KNOWN_SECRETS_CACHE"]).read()
        self.assertNotIn(FAKE_KNOWN, raw)
        self.assertEqual(sorted(json.loads(raw)), ["files", "generated", "root"])


def run_filter(text, *args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    e.setdefault("KNOWN_SECRETS_REPOS", tempfile.gettempdir() + "/no-such-repos-dir")
    return subprocess.run([sys.executable, LIB, "--filter", *args], input=text, capture_output=True, text=True,
                          env=e, timeout=60)


class Filter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = {"KNOWN_SECRETS_DIR": os.path.join(self.tmp, "s"), "KNOWN_SECRETS_REPOS": self.tmp,
                    "KNOWN_SECRETS_CACHE": os.path.join(self.tmp, "c.json"),
                    "KNOWN_SECRETS_IGNORE": os.path.join(self.tmp, "i")}

    def test_multiline_private_key_masked_whole(self):
        text = "line one\nstart %s tail\nlast\n" % FAKE_PEM
        r = run_filter(text, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ZmFrZWtleWJvZHk", r.stdout)
        self.assertNotIn("BEGIN", r.stdout)
        self.assertEqual(r.stdout, "line one\nstart <redacted:private-key>\n tail\nlast\n")

    def test_text_after_the_end_line_is_kept(self):
        r = run_filter("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY----- trailing\nnext\n", env=self.env)
        self.assertEqual(r.stdout, "<redacted:private-key>\n trailing\nnext\n")

    def test_lines_arrive_one_at_a_time(self):
        p = subprocess.Popen([sys.executable, LIB, "--filter"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, env=dict(os.environ, **self.env))
        try:
            p.stdin.write("first line\n")
            p.stdin.flush()
            self.assertEqual(p.stdout.readline(), "first line\n")      # stdin is still open
            p.stdin.write("jwt_secret=%s\n" % FAKE_SECRET)
            p.stdin.flush()
            self.assertEqual(p.stdout.readline(), "jwt_secret=<redacted:field jwt_secret>\n")
        finally:
            p.stdin.close()
            p.wait(timeout=10)

    def test_tier_flag(self):
        line = 'password = "hunter2hunter2"\n'
        self.assertEqual(run_filter(line, "--tier", "1", env=self.env).stdout, line)
        self.assertEqual(run_filter(line, "--tier", "2", env=self.env).stdout, 'password = "<redacted:field password>"\n')
        self.assertEqual(run_filter(line, env=self.env).stdout, 'password = "<redacted:field password>"\n')
        self.assertEqual(run_filter(line, "--tier", "3", env=self.env).returncode, 2)

    def test_crash_withholds_rest_of_stream(self):
        env = dict(self.env, SECRET_SCRUBBER_FAIL_AFTER="1")
        r = run_filter("ok line\n%s\nsecond %s\n" % (FAKE_JWT, FAKE_JWT), env=env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.splitlines(), ["ok line", ss.WITHHELD, ss.WITHHELD])
        self.assertNotIn("eyJ", r.stdout)

    def test_crash_on_first_line(self):
        r = run_filter("%s\n" % FAKE_JWT, env=dict(self.env, SECRET_SCRUBBER_FAIL="1"))
        self.assertEqual(r.stdout, ss.WITHHELD + "\n")


if __name__ == "__main__":
    unittest.main()
