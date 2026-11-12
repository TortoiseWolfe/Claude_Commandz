"""email_voice_check.py: the author's surname for the third-person self-reference check is read at run time."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from tmpdirs import fresh_dir

SCRIPT = Path(__file__).resolve().parent.parent / "email_voice_check.py"
spec = importlib.util.spec_from_file_location("email_voice_check", SCRIPT)
evc = importlib.util.module_from_spec(spec)
sys.modules["email_voice_check"] = evc
spec.loader.exec_module(evc)


class SelfNamesTest(unittest.TestCase):
    def cfg(self, body):
        f = Path(fresh_dir(self)) / "voice-check.json"
        f.write_text(body if isinstance(body, str) else json.dumps(body))
        return str(f)

    def test_surnames_come_from_the_private_file(self):
        self.assertEqual(evc.author_surnames(self.cfg({"author_surnames": ["Sample", "Tester"]})), ["Sample", "Tester"])

    def test_missing_bad_or_empty_file_gives_the_placeholder(self):
        self.assertEqual(evc.author_surnames(str(Path(fresh_dir(self)) / "absent.json")), ["Doe"])
        for body in ("not json", {"author_surnames": []}, {"author_surnames": "x"}, {"author_surnames": ["a b", 3]}, ["x"]):
            self.assertEqual(evc.author_surnames(self.cfg(body)), ["Doe"], body)

    def test_bare_first_name_trips_but_another_persons_full_name_does_not(self):
        rx = evc.build_self_names(["Sample"])
        self.assertTrue(rx.search("Jon has the password"))
        self.assertIsNone(rx.search("Jon Smith joined"))

    def test_the_authors_own_full_name_still_trips(self):
        rx = evc.build_self_names(["Sample"])
        self.assertTrue(rx.search("Jonathan Sample has the password"))
        self.assertTrue(rx.search("Jon Wolfe has the password"))

    def test_module_default_is_built_from_the_loader(self):
        self.assertTrue(evc.SELF_NAMES.search("Jon has the password"))


if __name__ == "__main__":
    unittest.main()
