import base64
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "otp_code.py")
spec = importlib.util.spec_from_file_location("otp_code", SCRIPT)
otp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(otp)

# RFC 6238 appendix B seed "12345678901234567890" (SHA-1), 6-digit codes are the last six of the vectors.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()
RFC_VECTORS = {59: "287082", 1111111109: "081804", 1111111111: "050471",
               1234567890: "005924", 2000000000: "279037", 20000000000: "353130"}
# Built at runtime so no scanner flags this file.
OTHER = base64.b32encode(b"Zq7" * 7).decode()


class Totp(unittest.TestCase):
    def test_rfc6238_vectors(self):
        for t, want in RFC_VECTORS.items():
            self.assertEqual(otp.totp(RFC_SECRET, now=t), want, t)

    def test_parse_accepts_spaced_lowercase_and_uri(self):
        spaced = " ".join(RFC_SECRET[i:i + 4] for i in range(0, len(RFC_SECRET), 4)).lower()
        self.assertEqual(otp.parse_secret(spaced), RFC_SECRET)
        uri = "otpauth://totp/GoDaddy:me?secret=%s&issuer=GoDaddy" % RFC_SECRET
        self.assertEqual(otp.parse_secret(uri), RFC_SECRET)

    def test_parse_rejects_junk(self):
        for bad in ("", "   ", "not base32 !!", "otpauth://totp/x?issuer=y"):
            with self.assertRaises(ValueError):
                otp.parse_secret(bad)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "otp")
        self.env = dict(os.environ, AFA_OTP_DIR=self.dir)

    def run_cli(self, *args, stdin=""):
        return subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True,
                              text=True, env=self.env)

    def write_secret(self, site, secret, mode):
        os.makedirs(self.dir, exist_ok=True)
        p = os.path.join(self.dir, site)
        with open(p, "w") as fh:
            fh.write(secret + "\n")
        os.chmod(p, mode)
        return p

    def test_code_is_six_digits_and_secret_never_shown(self):
        self.write_secret("godaddy", OTHER, 0o600)
        r = self.run_cli("godaddy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^\d{6}\n$")
        self.assertNotIn(OTHER, r.stdout + r.stderr)

    def test_refuses_loose_mode(self):
        for mode in (0o644, 0o640, 0o660, 0o604):
            self.write_secret("godaddy", OTHER, mode)
            r = self.run_cli("godaddy")
            self.assertNotEqual(r.returncode, 0, oct(mode))
            self.assertEqual(r.stdout, "")
            self.assertNotIn(OTHER, r.stdout + r.stderr)
            self.assertIn("600", r.stderr)

    def test_missing_secret(self):
        r = self.run_cli("godaddy")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")

    def test_bad_site_name_cannot_escape_folder(self):
        r = self.run_cli("../godaddy")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_corrupt_secret_error_does_not_leak_it(self):
        junk = "Nn5!leakme"
        self.write_secret("godaddy", junk, 0o600)
        r = self.run_cli("godaddy")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("leakme", r.stdout + r.stderr)

    def test_setup_writes_600_and_echoes_nothing(self):
        r = self.run_cli("--setup", "godaddy", stdin=OTHER + "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout + r.stderr, "")
        p = os.path.join(self.dir, "godaddy")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        self.assertEqual(self.run_cli("godaddy").returncode, 0)

    def test_setup_overwrite_tightens_old_mode(self):
        self.write_secret("godaddy", "OLD", 0o644)
        self.assertEqual(self.run_cli("--setup", "godaddy", stdin=OTHER).returncode, 0)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, "godaddy")).st_mode), 0o600)

    def test_setup_accepts_otpauth_uri(self):
        uri = "otpauth://totp/GoDaddy:me?secret=%s" % OTHER
        self.assertEqual(self.run_cli("--setup", "godaddy", stdin=uri).returncode, 0)
        with open(os.path.join(self.dir, "godaddy")) as fh:
            self.assertEqual(fh.read().strip(), OTHER.rstrip("="))

    def test_setup_refuses_junk_and_writes_nothing(self):
        r = self.run_cli("--setup", "godaddy", stdin="not a key!!")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "godaddy")))


if __name__ == "__main__":
    unittest.main()
