import contextlib
import io
import json
import os
import stat
import sys
import shutil
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import coop_roster_build as rb  # noqa: E402
import coop_fixtures as fx  # noqa: E402

CSV = ("First Name,Last Name,Email,Mobile Phone,Company\n"
       "Zelda,Quillfeather,zelda.q@example.invalid,(423) 555-0142,Acme\n"
       "Barnaby,Thistlewood,barnaby@example.invalid,+1 423 555 0177,\n")
JSON = json.dumps({"members": [{"display_name": "Cordelia Wexcombe", "email": "cordelia@example.invalid"},
                               {"name": "Ignatius Bramblefoot", "phone": "423-555-0155"}]})
VCF = ("BEGIN:VCARD\nVERSION:3.0\nN:Ottoline;Perpetua;;;\nFN:Perpetua Ottoline\nEMAIL;TYPE=work:perp@example.invalid\n"
       "TEL;TYPE=cell:423 555 0166\nEND:VCARD\n")
SQL = ("CREATE TABLE `contact` (\n  `id` int(10) NOT NULL,\n  `first_name` varchar(64),\n  `last_name` varchar(64),\n"
       "  `email_note` varchar(9),\n  PRIMARY KEY (`id`)\n);\n"
       "INSERT INTO `contact` VALUES (1,'Hortensia','Fiddlewick','n'),(2,'Lucian','O\\'Marrow',NULL);\n"
       "CREATE TABLE `email` (\n  `id` int,\n  `email` varchar(99)\n);\n"
       "INSERT INTO email (id, email) VALUES (1,'hortensia@example.invalid');\n")
NAMES = ["Zelda Quillfeather", "Barnaby Thistlewood", "Cordelia Wexcombe", "Ignatius Bramblefoot", "Perpetua Ottoline",
         "Hortensia Fiddlewick", "Lucian O'Marrow"]


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        self.out = os.path.join(self.tmp, "cfg", "roster.json")
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ["COOP_ROSTER_FILE"] = self.out

    def put(self, name, text):
        with open(os.path.join(self.src, name), "w") as fh:
            fh.write(text)

    def build(self, *extra):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = rb.main(["--source", self.src + "/**", "--out", self.out, *extra])
        return rc, buf.getvalue(), err.getvalue()

    def roster(self):
        return json.load(open(self.out))

    def all_names(self):
        return {n for p in self.roster()["people"] for n in p["names"]}

    def test_csv(self):
        self.put("m.csv", CSV)
        rc, out, _ = self.build()
        self.assertEqual(rc, 0)
        r = self.roster()
        self.assertEqual(len(r["people"]), 2)
        z = [p for p in r["people"] if fx.ZELDA in p["names"]][0]
        self.assertEqual(z["emails"], [fx.ZELDA_EMAIL])
        self.assertEqual(z["phones"], ["4235550142"])

    def test_json_vcf_and_sql(self):
        self.put("m.json", JSON)
        self.put("c.vcf", VCF)
        self.put("d.sql", SQL)
        rc, out, _ = self.build()
        self.assertEqual(rc, 0)
        names = self.all_names()
        for n in NAMES[2:]:
            self.assertIn(n, names)
        # a second table's email is not joined to the contact (no foreign keys followed), but it is still in the roster
        emails = {e for p in self.roster()["people"] for e in p["emails"]}
        self.assertIn("hortensia@example.invalid", emails)

    def test_email_in_plain_text_is_found_without_a_name(self):
        self.put("notes.txt", "reach us at helpdesk@example.invalid or 423 555 0188")
        rc, _, _ = self.build()
        self.assertEqual(rc, 0)
        self.assertEqual(sum(len(p["emails"]) for p in self.roster()["people"]), 1)

    def test_output_is_counts_only(self):
        self.put("m.csv", CSV)
        self.put("m.json", JSON)
        self.put("c.vcf", VCF)
        self.put("d.sql", SQL)
        _, out, err = self.build()
        for text in (out, err):
            for token in NAMES + ["example.invalid", "555", self.src]:
                self.assertNotIn(token, text)
        self.assertRegex(out, r"^coop roster: \d+ people, \d+ names, \d+ emails, \d+ phones; files read by kind: ")

    def test_roster_file_is_private(self):
        self.put("m.csv", CSV)
        self.build()
        self.assertEqual(stat.S_IMODE(os.stat(self.out).st_mode), 0o600)

    def test_salt_survives_a_rebuild(self):
        self.put("m.csv", CSV)
        self.build()
        first = self.roster()["salt"]
        self.put("m.csv", CSV + "Eulalia,Dunmore,eulalia@example.invalid,\n")
        self.build()
        self.assertEqual(self.roster()["salt"], first)
        self.assertEqual(len(self.roster()["people"]), 3)

    def test_nothing_found_leaves_the_old_roster_alone(self):
        self.put("m.csv", CSV)
        self.build()
        before = open(self.out).read()
        os.remove(os.path.join(self.src, "m.csv"))
        rc, _, err = self.build()
        self.assertEqual(rc, 3)
        self.assertEqual(open(self.out).read(), before)

    def test_unreadable_source_list_writes_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = rb.main(["--paths-file", os.path.join(self.tmp, "missing.txt"), "--out", self.out])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(self.out))

    def test_paths_file_with_comments_and_home(self):
        self.put("m.csv", CSV)
        pf = os.path.join(self.tmp, "paths.txt")
        open(pf, "w").write("# note\n\n%s\n" % self.src)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = rb.main(["--paths-file", pf, "--out", self.out])
        self.assertEqual(rc, 0)

    def test_built_roster_drives_the_scrubber(self):
        import coop_scrubber as cs
        self.put("m.csv", CSV)
        self.build()
        s = cs.Scrubber(cs.Roster(self.roster()), use_ner=False)
        out = s.scrub_strings(["%s wrote from %s" % (fx.ZELDA, fx.ZELDA_EMAIL)])[0]
        self.assertRegex(out, r"^\[PERSON_\d+\] wrote from \[EMAIL_\d+\]$")

    def test_sql_values_with_quotes_commas_and_nulls(self):
        c = rb.Collector()
        rb.read_sql("INSERT INTO t (first_name, last_name, email) VALUES ('Mo,ra','Vale','mora@example.invalid'),(NULL,'X',NULL);", c)
        names = {n for p in c.people for n in p["names"]}
        self.assertEqual(names, {"Mo,ra Vale"})

    def test_a_row_with_only_a_name_column_is_not_a_person(self):
        c = rb.Collector()
        rb.read_sql("CREATE TABLE `state` (\n `id` int,\n `name` varchar(9),\n `abbreviation` varchar(3)\n);\n"
                    "INSERT INTO `state` VALUES (1,'Tennessee Valley','TV'),(2,'Quillfeather Region','QR');\n"
                    "CREATE TABLE `contact` (\n `id` int,\n `display_name` varchar(9),\n `first_name` varchar(9),\n `last_name` varchar(9)\n);\n"
                    "INSERT INTO `contact` VALUES (1,'Dr. Zelda Quillfeather','Zelda','Quillfeather'),(2,'Acme Cooperative','','');\n", c)
        names = {n for p in c.people for n in p["names"]}
        self.assertEqual(names, {"Dr. Zelda Quillfeather", "Zelda Quillfeather"})

    def test_json_object_with_only_a_name_is_not_a_person_but_csv_is_lenient(self):
        c = rb.Collector()
        rb.walk_json({"tags": [{"name": "Gardening Club"}], "member": {"name": "Cordelia Wexcombe", "email": "c@example.invalid"}}, c)
        self.assertEqual({n for p in c.people for n in p["names"]}, {"Cordelia Wexcombe"})
        c2 = rb.Collector()
        rb.read_csv("Name\nCordelia Wexcombe\n", c2, ",")
        self.assertEqual({n for p in c2.people for n in p["names"]}, {"Cordelia Wexcombe"})

    def test_output_has_a_breakdown_by_source_and_no_names(self):
        self.put("d.sql", SQL)
        _, out, _ = self.build()
        self.assertIn("records by source", out)
        self.assertIn("sql:contact", out)
        self.assertRegex(out, r"name shapes: 2-word \d+")

    def test_classify(self):
        self.assertEqual(rb.classify("First Name"), "first")
        self.assertEqual(rb.classify("last_name"), "last")
        self.assertEqual(rb.classify("Display Name"), "full")
        self.assertEqual(rb.classify("E-mail"), "email")
        self.assertEqual(rb.classify("mobile_phone"), "phone")
        for k in ("username", "company_name", "email_type", "phone_type", "filename", "id"):
            self.assertIsNone(rb.classify(k), k)


if __name__ == "__main__":
    unittest.main()
