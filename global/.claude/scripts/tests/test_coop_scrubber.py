import json
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import coop_scrubber as cs  # noqa: E402
import coop_fixtures as fx  # noqa: E402


def patch_env(case):
    """Every environment change a test makes is undone when it ends."""
    patcher = mock.patch.dict(os.environ)
    patcher.start()
    case.addCleanup(patcher.stop)


class CoopFolder(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        patch_env(self)
        os.environ["HOME"] = self.home

    def test_match_covers_everything_under_a_folder(self):
        pats = ["~/repos/Acme-Co*", "/srv/shared/**/ops"]
        self.assertTrue(cs.is_coop_folder(self.home + "/repos/Acme-Co", pats))
        self.assertTrue(cs.is_coop_folder(self.home + "/repos/Acme-Co-stack/src/deep", pats))
        self.assertTrue(cs.is_coop_folder("/srv/shared/a/b/ops/x", pats))

    def test_other_folders_do_not_match(self):
        pats = ["~/repos/Acme-Co*"]
        self.assertFalse(cs.is_coop_folder(self.home + "/repos/Other", pats))
        self.assertFalse(cs.is_coop_folder(self.home + "/repos", pats))
        self.assertFalse(cs.is_coop_folder(self.home + "/repos/Acme", pats))

    def test_no_patterns_or_no_folder_means_off(self):
        self.assertFalse(cs.is_coop_folder(self.home + "/repos/Acme-Co", []))
        self.assertFalse(cs.is_coop_folder("", ["~/repos/*"]))

    def test_symlinked_folder_is_matched_by_its_real_path(self):
        real = os.path.join(self.home, "repos", "Acme-Co-real")
        os.makedirs(real)
        link = os.path.join(self.home, "elsewhere")
        os.symlink(real, link)
        self.assertTrue(cs.is_coop_folder(link, ["~/repos/Acme-Co*"]))


class RosterAndPatterns(unittest.TestCase):
    def scrubber(self, **kw):
        return cs.Scrubber(cs.Roster(fx.ROSTER), use_ner=False, **kw)

    def test_roster_name_email_phone(self):
        text = "%s (%s) rang %s and %s" % (fx.ZELDA, fx.ZELDA_EMAIL, fx.BARNABY_PHONE, fx.ZELDA_PHONE)
        out = self.scrubber().scrub_strings([text])[0]
        self.assertEqual(out, "[PERSON_1] ([EMAIL_1]) rang [PHONE_2] and [PHONE_1]")

    def test_last_first_and_case_variants(self):
        out = self.scrubber().scrub_strings(["Quillfeather, Zelda and BARNABY THISTLEWOOD and zelda quillfeather"])[0]
        self.assertEqual(out, "[PERSON_1] and [PERSON_2] and [PERSON_1]")

    def test_same_person_same_placeholder_in_every_call(self):
        a = self.scrubber().scrub_strings([fx.BARNABY])[0]
        b = self.scrubber().scrub_strings(["note: %s." % fx.BARNABY])[0]
        self.assertEqual(a, "[PERSON_2]")
        self.assertEqual(b, "note: [PERSON_2].")

    def test_unknown_email_and_phone_get_hashed_placeholders(self):
        out = self.scrubber().scrub_strings(["write to stranger@example.invalid or 423-555-0199"])[0]
        self.assertRegex(out, r"^write to \[EMAIL_x[0-9a-f]{4}\] or \[PHONE_x[0-9a-f]{4}\]$")
        again = self.scrubber().scrub_strings(["stranger@example.invalid"])[0]
        self.assertEqual(again, out.split(" ")[2])

    def test_bare_digit_runs_are_not_phones(self):
        text = "build 20261104123456 size 4235559999 port 8080 version 1.2.3.4"
        self.assertEqual(self.scrubber().scrub_strings([text])[0], text)

    def test_roster_phone_found_even_without_a_standard_shape(self):
        out = self.scrubber().scrub_strings(["call 423.555.0142 now, or 4235550142"])[0]
        self.assertEqual(out, "call [PHONE_1] now, or [PHONE_1]")

    def test_allow_list_and_builtin_allow_stay_readable(self):
        s = cs.Scrubber(cs.Roster({"people": [{"names": ["Claude Operator"], "emails": []}]}), allow=["Claude Operator"], use_ner=False)
        self.assertEqual(s.scrub_strings(["Claude Operator wrote"])[0], "Claude Operator wrote")

    def test_name_inside_a_word_is_left_alone(self):
        s = cs.Scrubber(cs.Roster({"people": [{"names": ["Ann Lee"]}]}), use_ner=False)
        self.assertEqual(s.scrub_strings(["Joann Leeds"])[0], "Joann Leeds")

    def test_one_word_roster_names_are_not_matched(self):
        s = cs.Scrubber(cs.Roster({"people": [{"names": ["Friends"]}, {"names": ["Smith,"]}]}), use_ner=False)
        self.assertEqual(s.scrub_strings(["Friends of Smith"])[0], "Friends of Smith")

    def test_longer_name_wins_over_shorter(self):
        s = cs.Scrubber(cs.Roster({"people": [{"names": ["Ann Lee"]}, {"names": ["Ann Lee Smith"]}]}), use_ner=False)
        self.assertEqual(s.scrub_strings(["Ann Lee Smith"])[0], "[PERSON_2]")

    def test_counts(self):
        s = self.scrubber()
        s.scrub_strings([fx.ZELDA + " " + fx.ZELDA + " " + fx.BARNABY_EMAIL])
        self.assertEqual(s.counts, {"PERSON": 2, "EMAIL": 1, "PHONE": 0})

    def test_nested_shape_is_kept(self):
        resp = {"stdout": fx.ZELDA, "stderr": "", "interrupted": False, "isImage": False,
                "file": {"content": ["a", fx.BARNABY_EMAIL], "numLines": 3}}
        out = self.scrubber().scrub(resp)
        self.assertEqual(out, {"stdout": "[PERSON_1]", "stderr": "", "interrupted": False, "isImage": False,
                               "file": {"content": ["a", "[EMAIL_2]"], "numLines": 3}})
        self.assertEqual(resp["stdout"], fx.ZELDA)  # the input is not changed

    def test_plain_string_response(self):
        self.assertEqual(self.scrubber().scrub(fx.ZELDA_EMAIL), "[EMAIL_1]")


class PresidioStandIn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        py, worker = fx.write_fake_presidio(self.tmp)
        patch_env(self)
        os.environ["COOP_SCRUBBER_PRESIDIO_PYTHON"] = py
        os.environ["COOP_SCRUBBER_PRESIDIO_WORKER"] = worker

    def test_presidio_spans_are_used_and_roster_wins_overlap(self):
        s = cs.Scrubber(cs.Roster(fx.ROSTER), use_ner=True)
        out = s.scrub_strings(["Wendell Fogbottom met %s" % fx.ZELDA])[0]
        self.assertRegex(out, r"^\[PERSON_x[0-9a-f]{4}\] met \[PERSON_1\]$")
        self.assertEqual(s.ner_state, "on")

    def test_a_product_phrase_is_not_a_person(self):
        # Issue 239: the card title "Standard Nextcloud images" showed as [PERSON_...] in what a lane saw.
        s = cs.Scrubber(cs.Roster(), use_ner=True)
        out = s.scrub_strings(["Standard Nextcloud images", "Wendell Fogbottom asked about Standard Nextcloud images"])
        self.assertEqual(out[0], "Standard Nextcloud images")
        self.assertRegex(out[1], r"^\[PERSON_x[0-9a-f]{4}\] asked about Standard Nextcloud images$")
        self.assertEqual(s.counts["PERSON"], 1)

    def test_products_setting_adds_words_and_roster_people_are_still_replaced(self):
        s = cs.Scrubber(cs.Roster(fx.ROSTER), use_ner=True)
        self.assertRegex(s.scrub_strings(["Zephyr Lantern"])[0], r"^\[PERSON_x")
        s = cs.Scrubber(cs.Roster(fx.ROSTER), use_ner=True, products=["lantern"])
        self.assertEqual(s.scrub_strings(["Zephyr Lantern"])[0], "Zephyr Lantern")
        # a roster person whose name holds a product word is still replaced: the roster is never second-guessed
        r = cs.Roster({"salt": "s", "people": [{"names": ["Dana Nextcloud"], "emails": [], "phones": []}]})
        self.assertEqual(cs.Scrubber(r, use_ner=True).scrub_strings(["Dana Nextcloud wrote"])[0], "[PERSON_1] wrote")

    def test_all_strings_go_in_one_call(self):
        s = cs.Scrubber(cs.Roster(), use_ner=True)
        out = s.scrub({"a": "Wendell Fogbottom", "b": ["Wendell Pennywhistle", 3]})
        self.assertRegex(out["a"], r"^\[PERSON_x")
        self.assertRegex(out["b"][0], r"^\[PERSON_x")
        self.assertEqual(out["b"][1], 3)

    def test_missing_presidio_still_runs_the_rest(self):
        os.environ["COOP_SCRUBBER_PRESIDIO_PYTHON"] = os.path.join(self.tmp, "nope")
        s = cs.Scrubber(cs.Roster(fx.ROSTER), use_ner=True)
        out = s.scrub_strings([fx.ZELDA + " Wendell Fogbottom"])[0]
        self.assertEqual(out, "[PERSON_1] Wendell Fogbottom")
        self.assertEqual(s.ner_state, "unavailable")

    def test_ner_false_never_calls_presidio(self):
        s = cs.Scrubber(cs.Roster(), use_ner=False)
        self.assertEqual(s.scrub_strings(["Wendell Fogbottom"])[0], "Wendell Fogbottom")
        self.assertEqual(s.ner_state, "off")

    def test_failing_worker_is_treated_as_unavailable(self):
        bad = os.path.join(self.tmp, "bad.py")
        open(bad, "w").write("import sys\nsys.exit(3)\n")
        os.environ["COOP_SCRUBBER_PRESIDIO_WORKER"] = bad
        s = cs.Scrubber(cs.Roster(), use_ner=True)
        s.scrub_strings(["hello"])
        self.assertEqual(s.ner_state, "unavailable")


class ConfigFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patch_env(self)

    def test_missing_config_is_off(self):
        os.environ["COOP_SCRUBBER_CONFIG"] = os.path.join(self.tmp, "none.json")
        self.assertEqual(cs.load_config()["folders"], [])

    def test_unparseable_config_raises(self):
        p = os.path.join(self.tmp, "c.json")
        open(p, "w").write("{not json")
        os.environ["COOP_SCRUBBER_CONFIG"] = p
        with self.assertRaises(ValueError):
            cs.load_config()

    def test_config_values(self):
        p = os.path.join(self.tmp, "c.json")
        fx.write_json(p, {"folders": ["~/x*"], "allow": ["Op"], "ner": False})
        os.environ["COOP_SCRUBBER_CONFIG"] = p
        self.assertEqual(cs.load_config(), {"folders": ["~/x*"], "allow": ["Op"], "ner": False, "products": []})
        fx.write_json(p, {"folders": [], "products": ["Lantern", 7]})
        self.assertEqual(cs.load_config()["products"], ["Lantern"])

    def test_missing_roster_is_empty(self):
        os.environ["COOP_ROSTER_FILE"] = os.path.join(self.tmp, "none.json")
        self.assertTrue(cs.Roster.load().empty())

    def test_status_prints_counts_only(self):
        import io
        import contextlib
        r = os.path.join(self.tmp, "r.json")
        fx.write_json(r, fx.ROSTER)
        os.environ["COOP_ROSTER_FILE"] = r
        os.environ["COOP_SCRUBBER_CONFIG"] = os.path.join(self.tmp, "none.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cs.main(["x", "status"])
        out = buf.getvalue()
        self.assertIn("roster_emails=2", out)
        self.assertNotIn("Zelda", out)


if __name__ == "__main__":
    unittest.main()
