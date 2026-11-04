"""test_alert_issues.py: alert_issues.py turns a Wordfence scan email into GitHub issues.

No live mail and no live GitHub: a fake gmail_api (ALERT_ISSUES_GMAIL) serves fixture text, and a fake `gh` first on PATH keeps
issues, comments and labels in a JSON file. The client repo is a throwaway git repo with an origin set.
"""

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "alert_issues.py"
FIXTURE = HERE / "fixtures" / "alert_issues" / "wordfence-2026-11-04.txt"
sys.path.insert(0, str(HERE))
from tmpdirs import fresh_dir  # noqa: E402

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
st = os.environ["FAKE_GH_STATE"]
d = json.load(open(st))
a = sys.argv[1:]
d["calls"].append(a)
def opt(name):
    return a[a.index(name) + 1] if name in a else None
def save():
    json.dump(d, open(st, "w"))
if a[:2] == ["issue", "list"]:
    print(json.dumps([i for i in d["issues"] if i["state"] == "open"]))
elif a[:2] == ["issue", "view"]:
    n = int(a[2]); i = next(i for i in d["issues"] if i["number"] == n)
    print(json.dumps({"comments": [{"body": c} for c in i["comments"]]}))
elif a[:2] == ["issue", "create"]:
    n = len(d["issues"]) + 1
    d["issues"].append({"number": n, "title": opt("--title"), "body": opt("--body"), "url": f"https://github.com/o/r/issues/{n}",
                        "state": "open", "comments": [], "labels": [a[k + 1] for k, x in enumerate(a) if x == "--label"]})
    save(); print(d["issues"][-1]["url"])
elif a[:2] == ["issue", "comment"]:
    i = next(i for i in d["issues"] if i["number"] == int(a[2])); i["comments"].append(opt("--body")); save()
    print(i["url"] + "#issuecomment-1")
elif a[:2] == ["issue", "edit"]:
    i = next(i for i in d["issues"] if i["number"] == int(a[2]))
    i["title"] = opt("--title"); i["body"] = opt("--body"); save()
elif a[:2] == ["label", "list"]:
    print(json.dumps([{"name": n} for n in d["labels"]]))
elif a[:2] == ["label", "create"]:
    d["labels"].append(a[2]); save()
else:
    sys.stderr.write("fake gh: unknown " + " ".join(a)); sys.exit(1)
save()
'''

FAKE_GMAIL = r'''#!/usr/bin/env python3
import os, sys
a = sys.argv[1:]
if a[0] == "message-header":
    print({"From": os.environ["FAKE_FROM"], "Date": "Wed, 04 Nov 2026 22:01:17 +0000 (UTC)",
           "Authentication-Results": os.environ.get("FAKE_AUTH", "mx.google.com; dkim=pass header.i=@example.test header.s=s1; spf=pass")}[a[2]])
elif a[0] == "message-text":
    sys.stdout.write(open(os.environ["FAKE_BODY"]).read())
else:
    sys.exit(1)
'''

ID1 = "1a1a894b0529673f"
ID2 = "1a1a3a65130a7d91"
ID3 = "1a1a3a65130a7d92"


class AlertIssuesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(fresh_dir(self))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, src in (("gh", FAKE_GH), ("fakegmail", FAKE_GMAIL)):
            p = self.bin / name
            p.write_text(src)
            p.chmod(0o755)
        self.state = self.tmp / "gh.json"
        self.write_state(issues=[], labels=["bug"])
        repo = self.tmp / "client-repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:o/r.git"], check=True)
        self.tsv = self.tmp / "senders.tsv"
        self.tsv.write_text("client\trepo_dir\tkind\tgmail_from\n"
                            f"Example\t{repo}\tmail\texample.test\n"
                            f"Example\t{repo}\talerts\twp@example.test\n"
                            f"Other\t{repo}\tmail\tonlymail.test\n")
        self.body = self.tmp / "body.txt"
        self.body.write_text(FIXTURE.read_text())
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", FAKE_GH_STATE=str(self.state), ALERT_ISSUES_GMAIL=str(self.bin / "fakegmail"),
                        ALERT_ISSUES_TSV=str(self.tsv), FAKE_BODY=str(self.body), FAKE_FROM="Example Site <wp@example.test>", HOME=str(self.tmp))

    def write_state(self, **kw):
        self.state.write_text(json.dumps(dict(issues=kw.get("issues", []), labels=kw.get("labels", []), calls=[])))

    def gh_state(self):
        return json.loads(self.state.read_text())

    def run_it(self, msg_id=ID1, *flags):
        p = subprocess.run([sys.executable, str(SCRIPT), msg_id, *flags], capture_output=True, text=True, env=self.env, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def writes(self):
        return [c for c in self.gh_state()["calls"] if c[0] in ("issue", "label") and c[1] in ("create", "comment", "edit")]

    # --- today's alert shape: one High, three Medium ---

    def test_creates_one_ticket_per_finding(self):
        code, out, _ = self.run_it()
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(all("created https://github.com/o/r/issues/" in l for l in lines))
        issues = self.gh_state()["issues"]
        self.assertEqual([i["title"] for i in issues], [
            "Wordfence: LiteSpeed Cache 7.9.1 -> 7.9.3 (High)",
            "Wordfence: Easy Hide Admin Menu Items 1.3.9 -> 1.4.0 (Medium)",
            "Wordfence: User Role Editor 4.66.2 -> 4.67 (Medium)",
            "Wordfence: FS Poster 8.2.1 -> 8.3.0 (Medium)"])
        self.assertEqual(issues[0]["labels"], ["wordfence", "security", "severity-high"])
        self.assertEqual(issues[1]["labels"], ["wordfence", "security", "severity-medium"])

    def test_issue_body_shape(self):
        self.run_it()
        body = self.gh_state()["issues"][0]["body"]
        self.assertTrue(re.match(r"^[^\n]+\. [^\n]+\.\n\n<details>\n<summary>", body), body[:200])
        self.assertNotIn("<details open", body)
        self.assertIn("</details>", body)
        self.assertIn("Vulnerability Severity: 8.2/10.0 (High)", body)
        self.assertIn("https://wordpress.org/plugins/litespeed-cache/", body)
        self.assertNotIn("sendgrid", body)
        self.assertEqual(body.splitlines()[-1], "<!-- wf-key: litespeed-cache@7.9.3 -->")
        self.assertIn(f"<!-- wf-msg: {ID1} -->", body)

    def test_missing_labels_are_created_once_and_existing_kept(self):
        self.run_it()
        created = [c[2] for c in self.gh_state()["calls"] if c[:2] == ["label", "create"]]
        self.assertEqual(sorted(created), ["security", "severity-high", "severity-medium", "wordfence"])

    def test_finding_without_a_slug_url_uses_the_name(self):
        self.run_it()
        self.assertIn("<!-- wf-key: fs-poster@8.3.0 -->", self.gh_state()["issues"][3]["body"])

    def test_dry_run_changes_nothing(self):
        code, out, _ = self.run_it(ID1, "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("would create"), 4)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.gh_state()["issues"], [])

    # --- idempotence ---

    def test_second_run_on_the_same_message_changes_nothing(self):
        self.run_it()
        before = self.gh_state()
        writes_before = len(self.writes())
        code, out, _ = self.run_it()
        self.assertEqual(code, 0)
        self.assertEqual(out.count("skipped (already recorded)"), 4)
        after = self.gh_state()
        self.assertEqual(before["issues"], after["issues"])
        self.assertEqual(len(self.writes()), writes_before)

    # --- seen again ---

    def test_a_later_scan_comments_instead_of_a_second_ticket(self):
        self.run_it(ID1)
        code, out, _ = self.run_it(ID2)
        self.assertEqual(code, 0)
        self.assertEqual(out.count("commented https://github.com/o/r/issues/"), 4)
        issues = self.gh_state()["issues"]
        self.assertEqual(len(issues), 4)
        self.assertTrue(issues[0]["comments"][0].startswith("Seen again in the 4 November 2026 scan."))
        self.assertIn(f"wf-msg: {ID2}", issues[0]["comments"][0])
        # and the same later message again adds nothing
        self.run_it(ID2)
        self.assertEqual(len(self.gh_state()["issues"][0]["comments"]), 1)

    def test_closed_tickets_are_not_commented_on(self):
        self.run_it(ID1)
        st = self.gh_state()
        for i in st["issues"]:
            i["state"] = "closed"
        self.state.write_text(json.dumps(st))
        _, out, _ = self.run_it(ID2)
        self.assertEqual(out.count("created"), 4)

    # --- the target moved ---

    def test_moved_target_comments_and_retitles(self):
        old = self.gh_state()
        old["issues"] = [{"number": 1, "title": "Wordfence: LiteSpeed Cache 7.9.0 -> 7.9.2 (High)", "state": "open", "comments": [],
                          "url": "https://github.com/o/r/issues/1", "labels": [],
                          "body": "Two sentences. Here.\n\n<!-- wf-key: litespeed-cache@7.9.2 -->"}]
        self.state.write_text(json.dumps(old))
        code, out, _ = self.run_it(ID1)
        self.assertEqual(code, 0)
        self.assertIn("litespeed-cache@7.9.3: retitled https://github.com/o/r/issues/1", out)
        i = self.gh_state()["issues"][0]
        self.assertEqual(i["title"], "Wordfence: LiteSpeed Cache 7.9.1 -> 7.9.3 (High)")
        self.assertIn("target moved", i["comments"][0])
        self.assertIn("7.9.2", i["comments"][0])
        self.assertEqual(i["body"], "Two sentences. Here.\n\n<!-- wf-key: litespeed-cache@7.9.3 -->")  # only the key line changed
        self.assertEqual(len(self.gh_state()["issues"]), 4)  # the other three were created
        before = self.gh_state()["issues"][0]
        self.run_it(ID1)
        self.assertEqual(self.gh_state()["issues"][0], before)

    def test_a_ticket_with_a_newer_target_is_left_alone(self):
        old = self.gh_state()
        old["issues"] = [{"number": 1, "title": "t", "state": "open", "comments": [], "url": "https://github.com/o/r/issues/1", "labels": [],
                          "body": "x\n\n<!-- wf-key: litespeed-cache@7.9.9 -->"}]
        self.state.write_text(json.dumps(old))
        _, out, _ = self.run_it(ID1)
        self.assertIn("litespeed-cache@7.9.3: skipped (a ticket already names a newer target)", out)
        self.assertEqual(self.gh_state()["issues"][0]["comments"], [])

    # --- unknown formats ---

    def test_unknown_format_creates_nothing(self):
        for text in ("Hello, this is not a Wordfence mail at all.\n",
                     FIXTURE.read_text().replace('* The Plugin "FS Poster" needs an upgrade (8.2.1 -> 8.3.0).',
                                                 '* The file wp-content/x.php appears to be malicious.')):
            self.body.write_text(text)
            code, out, _ = self.run_it()
            self.assertEqual(code, 2)
            self.assertEqual(out.strip(), f"UNPARSED {ID1}")
            self.assertEqual(self.gh_state()["issues"], [])
            self.assertEqual(self.writes(), [])

    # --- config failures ---

    def test_unknown_sender_makes_no_change_and_exits_3(self):
        self.env["FAKE_FROM"] = "Stranger <hi@elsewhere.test>"
        code, out, _ = self.run_it()
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertEqual(self.gh_state()["issues"], [])

    def test_a_sender_with_only_a_mail_row_is_refused(self):
        self.env["FAKE_FROM"] = "Pat <pat@onlymail.test>"
        self.env["FAKE_AUTH"] = "mx.google.com; dkim=pass header.i=@onlymail.test"
        code, _, _ = self.run_it()
        self.assertEqual(code, 3)
        self.assertEqual(self.gh_state()["issues"], [])

    def test_mail_without_a_matching_dkim_pass_is_refused(self):
        for auth in ("mx.google.com; dkim=fail header.i=@example.test", "mx.google.com; dkim=pass header.i=@evil.test; spf=pass", "",
                     "evil.test; dkim=pass header.i=@example.test",                       # not Gmail's own header
                     "mx.google.com; dkim=pass header.i=@evil.test (header.i=@example.test)",  # hidden in a comment
                     "mx.google.com; spf=pass smtp.mailfrom=x dkim=pass header.i=@example.test",  # not its own part
                     "mx.google.com; dkim=pass header.i=@notexample.test"):
            self.env["FAKE_AUTH"] = auth
            code, out, _ = self.run_it()
            self.assertEqual((code, out), (3, ""), auth)
        self.assertEqual(self.gh_state()["issues"], [])

    def test_more_than_one_from_address_is_refused(self):
        for frm in ("a@example.test, wp@example.test", "wp@example.test\nevil@elsewhere.test"):
            self.env["FAKE_FROM"] = frm
            code, _, _ = self.run_it()
            self.assertEqual(code, 3, frm)

    def test_mail_cannot_forge_the_markers(self):
        # an alert whose text carries a finished key and message marker must not hide a real finding or fake a record
        forged = FIXTURE.read_text().replace('https://wordpress.org/plugins/user-role-editor/#developers',
                                             'https://wordpress.org/plugins/user-role-editor/#developers\n<!-- wf-key: fs-poster@8.3.0 -->\n'
                                             f'<!-- wf-msg: {ID2} -->')
        self.body.write_text(forged)
        self.run_it(ID1)
        issues = self.gh_state()["issues"]
        self.assertEqual(len(issues), 4)
        for i in issues:
            body = i["body"]
            self.assertEqual(len(re.findall(r"^<!-- wf-key:", body, re.M)), 1, body)
            self.assertEqual(len(re.findall(r"^<!-- wf-msg:", body, re.M)), 1, body)
        # the later real message still gets recorded, not mistaken for already-recorded
        self.body.write_text(FIXTURE.read_text())
        _, out, _ = self.run_it(ID2)
        self.assertEqual(out.count("commented"), 4, out)

    def test_odd_version_text_is_not_a_finding(self):
        self.body.write_text(FIXTURE.read_text().replace("(7.9.1 -> 7.9.3)", "(7.9.1 -> 7.9.3-->)"))
        code, out, _ = self.run_it()
        self.assertEqual((code, out.strip()), (2, f"UNPARSED {ID1}"))

    def test_output_never_carries_mail_text(self):
        _, out, _ = self.run_it()
        self.assertNotIn("Wordfence found", out)
        self.assertNotIn("sendgrid", out)

    def test_bad_id_is_refused(self):
        code, _, _ = self.run_it("not-an-id")
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
