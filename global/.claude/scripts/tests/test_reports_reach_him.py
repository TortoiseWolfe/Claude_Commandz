"""18 Oct: "it says I'll get reports back and then it just evolves into silence". The rules these tests hold:
  - every lane's report.md reaches his chat without the hub: one post per report version, the lane name as a heading, one item of at
    most 150 words with the full report's path when it was shortened (19 Oct 11:10 PM: "I don't need 5 part reports"), a 12-hour stamp; the first run only records what exists, and the dispatcher's own lanes
    (whose reports its sweep already posts) are left alone;
  - a person the people index gives a role for is masked by that role ("[field partner]"), not as a bare "[person]";
  - the receptionist forwards "next step" questions as its whole reply;
  - a picture is described in words with the Windows path (the web chat draws only inline base64 images, and a post is cut into parts).
The PC and OpenClaw are fakes; the names in the people fixture are invented."""

import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
import openclaw_status_digest as dg  # noqa: E402
from test_openclaw_dispatch import NOW, FakeHost  # noqa: E402
from test_openclaw_status_digest import PEOPLE, SENDERS, Base as DigestBase  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

HOOK = Path(__file__).resolve().parent.parent / "openclaw_ask_hook"


class ReportPosts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(fresh_dir(self))
        self.lanes = self.tmp / "lanes"
        self.ctx = dp.Ctx({}, self.tmp / "dispatch.json", self.lanes)
        self.host = FakeHost()

    def report(self, lane, text, age=300, date="2026-10-18"):
        p = self.lanes / ("%s-%s" % (date, lane)) / "report.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def run_posts(self, **kw):
        return dp.post_new_reports(self.ctx, self.host, time.time(), **kw)

    def seeded(self):
        """A state where every report now on disk counts as already posted: what the install does."""
        self.run_posts(seed=True)
        self.host.chats.clear()


class ReportsAreRecordedAndNeverPosted(ReportPosts):
    """21 Oct 6:45 PM: lane-report copies in his chat were "horrible UX" (43 piled up while the chat was down). The hub reads a report
    when lane_watch.sh wakes it and tells him what he needs in one answer, so post_new_reports records and posts nothing."""

    def test_a_new_report_is_recorded_and_nothing_reaches_the_chat(self):
        self.seeded()
        p = self.report("harmonic-egg-mail", "Done: the renewal card is waiting.")
        self.assertEqual(self.run_posts(), [])
        self.assertEqual(self.host.chats, [])
        data = json.loads(self.ctx.reports_posted_path.read_text())
        self.assertIn(str(p), data["posted"])
        self.assertEqual(data["texts"][str(p)]["last"], "Done: the renewal card is waiting.")

    def test_a_changed_report_is_recorded_again_and_still_not_posted(self):
        self.seeded()
        p = self.report("late-lane", "Done: first version.")
        self.run_posts()
        p.write_text("Done: second version.")
        os.utime(p, (time.time() - 100, time.time() - 100))
        self.assertEqual(self.run_posts(), [])
        data = json.loads(self.ctx.reports_posted_path.read_text())
        self.assertEqual(data["texts"][str(p)]["last"], "Done: second version.")
        self.assertEqual(self.host.chats, [])

    def test_seeding_and_plain_runs_post_nothing_and_record_every_report(self):
        self.report("a", "Done.")
        self.report("b", "Done too.")
        self.assertEqual(self.run_posts(seed=True), [])
        self.assertEqual(self.run_posts(), [])
        self.assertEqual(len(json.loads(self.ctx.reports_posted_path.read_text())["posted"]), 2)
        self.assertEqual(self.host.chats, [])

    def test_the_text_is_cleaned_of_control_characters(self):
        self.seeded()
        p = self.report("tty-lane", "Done:\x1b[31m red\x00 text.")
        self.run_posts()
        last = json.loads(self.ctx.reports_posted_path.read_text())["texts"][str(p)]["last"]
        self.assertNotRegex(last, r"[\x00-\x08\x0b-\x1f\x7f]")

    def test_folders_that_are_not_lanes_are_ignored(self):
        self.seeded()
        (self.lanes / "context").mkdir(parents=True)
        (self.lanes / "context" / "report.md").write_text("not a lane")
        (self.lanes / "queue").mkdir()
        self.run_posts()
        self.assertEqual(json.loads(self.ctx.reports_posted_path.read_text())["posted"], {})


class TheCommandLine(unittest.TestCase):
    def test_post_reports_is_a_verb_of_the_dispatcher(self):
        src = (Path(dp.__file__)).read_text()
        self.assertIn('argv[0] == "post-reports"', src)

    def test_the_status_task_runs_it(self):
        sh = (Path(dp.__file__).parent / "openclaw_status_refresh.sh").read_text()
        self.assertIn("post-reports", sh)


ROLE_PEOPLE = PEOPLE + """<details>
<summary>Percival Dunmore is Jonathan's field partner on the porch job.</summary>
```
GitHub    unconfirmed. dunmore-by-design is a candidate only.
Email     percy.dunmore@example.test
Role      Porch partner.
Mask      field partner
```
</details>
<details>
<summary>Ludmilla Carrow carries a bad mask line.</summary>
```
Mask      boss@example.test 555
```
</details>
"""


class RolesNotPerson(DigestBase):
    def setUp(self):
        super().setUp()
        (self.d / "people.md").write_text(ROLE_PEOPLE)
        self.names = dg.load_names([self.d / "people.md", self.d / "senders.tsv"])
        self.flt = dg.Filter(self.names)

    def test_a_person_with_a_mask_line_is_masked_by_that_role(self):
        self.assertEqual(self.flt.clean("porch waits on Percival tape"), "porch waits on [field partner] tape")
        self.assertEqual(self.flt.clean("Dunmore's tape"), "[field partner] tape")
        self.assertEqual(self.flt.clean("mail from percy"), "mail from [field partner]")

    def test_everyone_else_is_still_a_bare_person(self):
        self.assertEqual(self.flt.clean("Bartholomew Fitch keeps the books"), "[person] [person] keeps the books")

    def test_a_mask_line_that_is_not_plain_words_is_ignored(self):
        self.assertEqual(self.flt.clean("Ludmilla called"), "[person] called")

    def test_the_name_never_reaches_the_digest(self):
        roadmap = ("## Roadmap\n\n<details>\n<summary><b>Arc 1: Porch</b> (1 open, 0 done)</summary>\n\n"
                   "- [ ] **Porch measuring waits on Percival tape.** detail\n</details>\n")
        out = self.digest(roadmap_body=roadmap, flt=self.flt)
        self.assertIn("[field partner]", out)
        self.assertNotRegex(out, r"(?i)percival|dunmore")

    def test_load_names_exposes_the_roles(self):
        names = dg.load_names([self.d / "people.md"])
        self.assertEqual(names.roles.get("percival"), "field partner")
        self.assertNotIn("ludmilla", names.roles)


class TheReceptionistForwardsNextStepQuestions(unittest.TestCase):
    def test_a_next_step_question_is_forwarded_as_the_whole_reply(self):
        text = (HOOK / "agents_live.md").read_text()
        line = next((l for l in text.splitlines() if "next step" in l.lower() and "what's left" in l.lower()), "")
        self.assertTrue(line, "agents_live.md has no next-step rule")
        self.assertIn("whole reply", line)
        self.assertRegex(line, r"(?i)no (?:answer|snapshot)")


class PicturesInPosts(unittest.TestCase):
    def test_a_picture_line_has_a_plain_description_and_the_windows_path(self):
        line = dp.image_line("/home/TurtleWolfe/repos/x/reports/porch.png", "the porch rail, left side", distro="Debian")
        self.assertIn("the porch rail, left side", line)
        self.assertIn("\\\\wsl.localhost\\Debian\\home\\TurtleWolfe\\repos\\x\\reports\\porch.png", line)
        self.assertNotIn("![", line)

    def test_the_doc_says_why_there_is_no_embedding(self):
        self.assertIn("base64", dp.post_line.__doc__)
        self.assertIn("image_line", dp.post_line.__doc__)


if __name__ == "__main__":
    unittest.main()
