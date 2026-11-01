import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import redact_process as rp  # noqa: E402

HELPER = Path(__file__).resolve().parent.parent / "ps_redacted.sh"


class RedactProcess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repos = os.path.join(self.tmp.name, "repos")
        os.makedirs(self.repos)
        self.secrets = os.path.join(self.tmp.name, "secrets")
        os.makedirs(self.secrets)
        self.value = "A1b2C3d4" + "E5f6G7h8" + "J9k0L1m2"
        with open(os.path.join(self.secrets, "stream.key"), "w") as fh:
            fh.write(self.value + "\n")
        self.env = {
            "KNOWN_SECRETS_REPOS": self.repos,
            "KNOWN_SECRETS_DIR": self.secrets,
            "KNOWN_SECRETS_IGNORE": os.path.join(self.tmp.name, "missing-ignore"),
        }

    def test_redact_text_masks_known_keys_stream_urls_and_assignments(self):
        listing = ("ffmpeg rtmp://live.twitch.tv/app/%s key=bareKeyValue --token=tokenValue "
                   "known=%s\n" % (self.value, self.value))
        with mock.patch.dict(os.environ, self.env):
            output = rp.redact_text(listing)
        for value in (self.value, "bareKeyValue", "tokenValue"):
            self.assertNotIn(value, output)
        self.assertIn("rtmp://live.twitch.tv/app/", output)
        self.assertIn("<redacted:stream.key>", output)

    def test_shell_helper_runs_a_listing_and_redacts_stdout_and_stderr(self):
        listing = os.path.join(self.tmp.name, "fake-listing")
        with open(listing, "w") as fh:
            fh.write("#!/bin/sh\nprintf '%s\\n' 'key=bareKeyValue'\nprintf '%s\\n' 'token=tokenValue' >&2\n")
        os.chmod(listing, 0o700)
        with mock.patch.dict(os.environ, self.env):
            result = subprocess.run([str(HELPER), listing], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("bareKeyValue", result.stdout + result.stderr)
        self.assertNotIn("tokenValue", result.stdout + result.stderr)
        self.assertIn("<redacted:", result.stdout)


if __name__ == "__main__":
    unittest.main()
