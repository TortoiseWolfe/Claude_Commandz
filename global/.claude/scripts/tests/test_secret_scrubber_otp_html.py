"""Tests for the two-step (TOTP) secret and HTML key rules in scripts/secret_scrubber.py, and the same page run through
hooks/output-scrubber.py. Every value is made up at run time: random, never copied from a file."""
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..")
sys.path.insert(0, SCRIPTS)
import secret_scrubber as ss  # noqa: E402

HOOK = os.path.join(HERE, "..", "..", "hooks", "output-scrubber.py")
B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def b32_secret(n=32):
    """A random base32 secret that always holds at least one digit, the way a real one does almost every time."""
    s = [secrets.choice(B32) for _ in range(n)]
    s[secrets.randbelow(n)] = secrets.choice("234567")
    return "".join(s)


def groups(s, size=4, sep=" "):
    return sep.join(s[i:i + size] for i in range(0, len(s), size))


def scrub(text, tier=2):
    return ss.scrub_text(text, tier, needles=[])


class OtpAuth(unittest.TestCase):
    def test_otpauth_link_masks_the_secret_and_keeps_the_rest(self):
        b = b32_secret()
        out, n = scrub("otpauth://totp/Site:someone?secret=%s&issuer=Site" % b)
        self.assertEqual(n, 1)
        self.assertNotIn(b, out)
        self.assertEqual(out, "otpauth://totp/Site:someone?secret=<redacted:totp-secret>&issuer=Site")

    def test_otpauth_link_inside_an_html_attribute(self):
        b = b32_secret()
        out, n = scrub('<a href="otpauth://totp/Site:u?issuer=Site&amp;secret=%s">scan</a>' % b)
        self.assertEqual(n, 1)
        self.assertNotIn(b, out)

    def test_otpauth_link_in_script_with_escaped_quotes_and_encoded_equals(self):
        b = b32_secret()
        out, n = scrub("text: \\'otpauth://totp/S:u%%3Fsecret%%3D%s%%26issuer%%3DS\\'" % b)
        self.assertEqual(n, 1)
        self.assertNotIn(b, out)

    def test_tier_one_alone_masks_it_too(self):
        b = b32_secret()
        out, n = scrub("otpauth://totp/Site:u?secret=%s" % b, tier=1)
        self.assertEqual(n, 1)
        self.assertNotIn(b, out)


class BareBase32(unittest.TestCase):
    def test_secret_label(self):
        b = b32_secret()
        out, n = scrub("Secret: %s" % b)
        self.assertEqual((out, n), ("Secret: <redacted:totp-secret>", 1))

    def test_key_label_across_markup(self):
        b = b32_secret(16)
        out, n = scrub("<strong>Key:</strong> <code>%s</code>" % b)
        self.assertEqual(n, 1)
        self.assertNotIn(b, out)
        self.assertIn("<strong>Key:</strong>", out)

    def test_label_before_a_literal_backslash_n(self):
        b = b32_secret()
        out, n = scrub("Your secret\\n%s\\nthen scan" % b)
        self.assertEqual(n, 1)
        self.assertNotIn(b, out)

    def test_wordfence_qr_text_input(self):
        b = b32_secret()
        page = '<input id="wfls-qr-code-text" class="wfls-center" type="text" value="%s" onclick="this.select();" size="32" readonly>' % b
        out, n = scrub(page)
        self.assertEqual(n, 1)
        self.assertNotIn(b, out)
        self.assertIn('id="wfls-qr-code-text"', out)
        self.assertIn("readonly", out)


class SpacedGroups(unittest.TestCase):
    def test_four_by_four_base32_under_a_qr_code(self):
        b = b32_secret()
        out, n = scrub("<p>Or enter this code manually: %s</p>" % groups(b))
        self.assertEqual(n, 1)
        self.assertEqual(out, "<p>Or enter this code manually: <redacted:totp-secret></p>")

    def test_non_breaking_space_separators(self):
        b = b32_secret()
        out, n = scrub("<code>%s</code>" % groups(b, sep="&nbsp;"))
        self.assertEqual(n, 1)
        self.assertNotIn(b[:4], out)

    def test_recovery_codes_as_hex_groups_in_a_list(self):
        code = secrets.token_hex(8) + "0"          # always has a digit
        code = code[:16]
        page = '<ul class="wfls-recovery-codes"><li>%s</li></ul>' % groups(code)
        out, n = scrub(page)
        self.assertEqual(n, 1)
        self.assertNotIn(groups(code), out)

    def test_recovery_codes_array_in_script(self):
        a, b = secrets.token_hex(8), secrets.token_hex(8)
        out, n = scrub("recovery: ['%s', '%s'],\n  code: 'x'" % (a, b))
        self.assertEqual(n, 2)
        self.assertNotIn(a, out)
        self.assertNotIn(b, out)
        self.assertIn("recovery: ['<redacted:recovery-code>', '<redacted:recovery-code>']", out)

    def test_script_payload_secret_is_masked_by_its_name(self):
        h = secrets.token_hex(20)
        out, n = scrub("var payload = {\n  secret: '%s',\n  user: 7,\n};" % h)
        self.assertEqual(n, 1)
        self.assertNotIn(h, out)


class PipedFields(unittest.TestCase):
    """A form dumped as one 'id|type|value' string per field."""

    def test_the_two_step_field_value_is_masked_and_the_name_kept(self):
        s = b32_secret()
        out, n = scrub("fields = ['wfls-qr-code-text|text|%s', 'wfls-activate-field|text|']" % s)
        self.assertEqual(n, 1)
        self.assertNotIn(s, out)
        self.assertIn("wfls-qr-code-text|text|", out)
        self.assertIn("'wfls-activate-field|text|'", out)

    def test_a_licence_field(self):
        lic = secrets.token_hex(64)
        out, n = scrub("rows: license-key|password|%s" % lic)
        self.assertEqual(n, 1)
        self.assertNotIn(lic, out)

    def test_other_fields_and_pipes_are_left_alone(self):
        for text in ("'wfls-enable|checkbox|false'", "'wfls-qr-code-text|text|'", "ls | grep key | sort",
                     "'search-box|text|Find a key'", "'page-title|text|%s'" % secrets.token_hex(16)):
            self.assertEqual(scrub(text, 1), (text, 0), text)

    def test_a_pass_that_already_masked_is_not_masked_twice(self):
        once, _n = scrub("'wfls-qr-code-text|text|%s'" % b32_secret())
        self.assertEqual(scrub(once, 1), (once, 0))


class HtmlKeys(unittest.TestCase):
    def test_licence_input_by_name(self):
        lic = secrets.token_hex(64)
        out, n = scrub('<input type="text" name="licenseKey" id="x" value="%s" class="regular-text">' % lic)
        self.assertEqual(n, 1)
        self.assertNotIn(lic, out)
        self.assertIn('name="licenseKey"', out)
        self.assertIn('class="regular-text"', out)

    def test_wordfence_option_name_apikey(self):
        lic = secrets.token_hex(64)
        out, n = scrub("<input name='apiKey' value='%s'>" % lic)
        self.assertEqual(n, 1)
        self.assertNotIn(lic, out)

    def test_by_id_with_words(self):
        lic = secrets.token_hex(32)
        out, n = scrub('<input id="wf-license-key" type="text" value="%s">' % lic)
        self.assertEqual(n, 1)
        self.assertNotIn(lic, out)

    def test_password_type_input(self):
        pw = "Zq" + secrets.token_hex(6)
        out, n = scrub('<input type="password" name="pwd1" value="%s">' % pw)
        self.assertEqual(n, 1)
        self.assertNotIn(pw, out)

    def test_data_attribute_whose_own_name_says_key(self):
        lic = secrets.token_hex(32)
        out, n = scrub('<div class="wrap" data-api-key="%s" data-state="on">' % lic)
        self.assertEqual(n, 1)
        self.assertNotIn(lic, out)
        self.assertIn('data-state="on"', out)

    def test_meta_csrf_token_content(self):
        tok = secrets.token_hex(16)
        out, n = scrub('<meta name="csrf-token" content="%s">' % tok)
        self.assertEqual(n, 1)
        self.assertNotIn(tok, out)

    def test_tag_with_escaped_quotes_inside_json(self):
        lic = secrets.token_hex(32)
        out, n = scrub('{"html":"<input name=\\"license\\" value=\\"%s\\">"}' % lic)
        self.assertEqual(n, 1)
        self.assertNotIn(lic, out)

    def test_structured_reply_html_string(self):
        lic = secrets.token_hex(32)
        obj, n = ss.scrub_obj({"body": '<input name="license" value="%s">' % lic}, 2, [])
        self.assertEqual(n, 1)
        self.assertNotIn(lic, json.dumps(obj))

    def test_a_pass_that_already_masked_is_not_masked_twice(self):
        once, n1 = scrub('<input name="license" value="%s">' % secrets.token_hex(32))
        twice, n2 = scrub(once)
        self.assertEqual((once, n2), (twice, 0))
        self.assertEqual(n1, 1)


class Negatives(unittest.TestCase):
    def assertUntouched(self, text, tier=2):
        out, n = scrub(text, tier)
        self.assertEqual((out, n), (text, 0))

    def test_css_classes_and_ids(self):
        self.assertUntouched('<div class="key-icon license-badge token-wrap secret-sauce" id="main-key-panel">')

    def test_git_shas_and_hashes(self):
        self.assertUntouched("commit " + secrets.token_hex(20) + " merged; parent " + secrets.token_hex(20))
        self.assertUntouched("sha256: " + secrets.token_hex(32))
        self.assertUntouched("<a href='/commit/%s'>%s</a>" % (secrets.token_hex(20), secrets.token_hex(4)))

    def test_ordinary_capital_words_in_fours(self):
        self.assertUntouched("THIS WILL FAIL NOW AND STOP HERE")
        self.assertUntouched("NOTE: DONE PASS FAIL SKIP ERROR")

    def test_text_after_a_mask_token_is_not_read_as_a_secret(self):
        # "<redacted:known-secret>" ends in the word "secret" and a ">": the label rule must not treat what follows as a value
        run = "A" * 120 + "Kx9fakeknownvaluefortestsonly77Q" + "B" * 120
        out, n = ss.scrub_text(run, 1, needles=[("Kx9fakeknownvaluefortestsonly77Q", "known-secret")])
        self.assertEqual(n, 1)
        self.assertTrue(out.endswith("B" * 120))

    def test_a_long_capital_word_after_key(self):
        self.assertUntouched("key: UNAUTHORIZEDACCESSDENIED")
        # tier 2's own field rule (older than the 2-step rules) masks a long value after "Secret:"; the new rule must not
        self.assertUntouched("Secret: PROFESSIONALDEVELOPMENT", tier=1)

    def test_hex_dump_lines(self):
        self.assertUntouched("00000010: 7f45 4c46 0201 0100 0000 0000 0000 0000")
        self.assertUntouched("0000a0: 1234 5678 9abc def0 1234 5678 9abc def0")

    def test_hex_words_with_nothing_about_secrets_near(self):
        self.assertUntouched("colors a1b2 c3d4 e5f6 0718 are used here")

    def test_buttons_labels_and_placeholders(self):
        self.assertUntouched('<input type="submit" name="save_key" value="Save Key">')
        self.assertUntouched('<input name="license" placeholder="Enter your license key" value="">')
        self.assertUntouched('<input name="apiKey" value="">')
        self.assertUntouched('<input name="token" value="default">')

    def test_names_that_only_contain_the_letters(self):
        self.assertUntouched('<input name="keyword" value="%s">' % ("find" + secrets.token_hex(4)))
        self.assertUntouched('<input name="monkey" value="%s">' % secrets.token_hex(8))
        self.assertUntouched('<input name="token_type" value="bearer-%s">' % secrets.token_hex(4))
        self.assertUntouched('<input name="page" value="%s">' % secrets.token_hex(8))

    def test_ordinary_html_and_code(self):
        self.assertUntouched('<a href="/x?id=%s" class="btn">go</a>' % secrets.token_hex(8))
        self.assertUntouched("if (a < b && c > d) { key = getKey(); }")
        self.assertUntouched("Use secrets.token_hex(20) to make one.")

    def test_otpauth_word_alone(self):
        self.assertUntouched("the otpauth scheme carries a secret parameter")


class ThroughTheHook(unittest.TestCase):
    """The same page, sent to the real PostToolUse hook as a Bash and a Read result."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, "repos", "x"))
        secrets_dir = os.path.join(self.tmp, "secrets")
        os.makedirs(secrets_dir)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("COOP_", "SECRET_SCRUB", "KNOWN_", "SHELL_SCRUB", "CLAUDE_CODE_SHELL_PREFIX"))}
        self.env.update(HOME=self.home, KNOWN_SECRETS_REPOS=os.path.join(self.home, "repos"), KNOWN_SECRETS_DIR=secrets_dir,
                        KNOWN_SECRETS_IGNORE=os.path.join(self.tmp, "none"), KNOWN_SECRETS_CACHE=os.path.join(self.tmp, "c.json"),
                        SECRET_SCRUB_ALERTS=os.path.join(self.tmp, "alerts.log"),
                        SHELL_SCRUB_HEARTBEAT=os.path.join(self.tmp, "hb"), SHELL_SCRUB_OFF_FILE=os.path.join(self.tmp, "off"),
                        OUTPUT_SCRUBBER_LOG=os.path.join(self.tmp, "out.log"), COOP_SCRUBBER_LOG=os.path.join(self.tmp, "coop.log"))

    def run_hook(self, tool, text):
        stdin = json.dumps({"tool_name": tool, "cwd": os.path.join(self.home, "repos", "x"), "tool_response": {"stdout": text}})
        return subprocess.run([sys.executable, HOOK], input=stdin, env=self.env, capture_output=True, text=True, timeout=60)

    def test_settings_page_is_masked_for_bash_and_for_read(self):
        b, lic = b32_secret(), secrets.token_hex(64)
        page = ("<input id=\"wfls-qr-code-text\" type=\"text\" value=\"%s\" readonly>\n"
                "<p>Manual code: %s</p>\n<input name=\"apiKey\" value=\"%s\">\n"
                "text: 'otpauth://totp/S:u?secret=%s&issuer=S'" % (b, groups(b), lic, b))
        for tool in ("Bash", "Read"):
            r = self.run_hook(tool, page)
            self.assertEqual(r.returncode, 0)
            for leaked in (b, groups(b), lic):
                self.assertNotIn(leaked, r.stdout)
            out = json.loads(r.stdout)["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
            self.assertEqual(out.count("<redacted:"), 4)

    def test_ordinary_page_prints_nothing(self):
        r = self.run_hook("Bash", '<div class="key-icon"><a href="/x">%s</a></div>' % secrets.token_hex(20))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))


if __name__ == "__main__":
    unittest.main()
