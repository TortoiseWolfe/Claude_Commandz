"""email_voice_check.py: the author's names for the third-person self-reference check are read at run time."""
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

    def test_names_come_from_the_private_file(self):
        f = self.cfg({"author_surnames": ["Author", "Tester"], "author_first_names": ["Sample"]})
        self.assertEqual(evc.author_surnames(f), ["Author", "Tester"])
        self.assertEqual(evc.author_first_names(f), ["Sample"])

    def test_missing_bad_or_empty_file_gives_the_placeholders(self):
        self.assertEqual(evc.author_surnames(str(Path(fresh_dir(self)) / "absent.json")), ["Doe"])
        self.assertEqual(evc.author_first_names(str(Path(fresh_dir(self)) / "absent.json")), [])
        for body in ("not json", {"author_surnames": []}, {"author_surnames": "x"}, {"author_surnames": ["a b", 3]}, ["x"]):
            f = self.cfg(body)
            self.assertEqual(evc.author_surnames(f), ["Doe"], body)
            self.assertEqual(evc.author_first_names(f), [], body)

    def test_bare_first_name_trips_but_another_persons_full_name_does_not(self):
        rx = evc.build_self_names(["Author"], ["Sample"])
        self.assertTrue(rx.search("Sample has the password"))
        self.assertIsNone(rx.search("Sample Other joined"))

    def test_the_authors_own_full_name_still_trips(self):
        rx = evc.build_self_names(["Author"], ["Sample"])
        self.assertTrue(rx.search("Sample Author has the password"))
        self.assertTrue(rx.search("Sample Wolfe has the password"))

    def test_the_public_handles_always_count_and_no_first_names_are_built_in(self):
        rx = evc.build_self_names(["Author"], [])
        self.assertTrue(rx.search("TortoiseWolfe has the password"))
        self.assertTrue(rx.search("Turtle Wolfe has the password"))
        self.assertIsNone(rx.search("Sample has the password"))

    def test_module_default_is_built_from_the_loader(self):
        self.assertEqual(evc.SELF_NAMES.pattern, evc.build_self_names(evc.author_surnames(), evc.author_first_names()).pattern)


if __name__ == "__main__":
    unittest.main()
