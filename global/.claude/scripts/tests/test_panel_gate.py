"""Tests for panel_gate.py. No network, no Docker: the secret scanner is always injected."""

import contextlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import panel_gate as g  # noqa: E402

CLEAN = lambda diff: "clean"  # noqa: E731


def red(text, terms=()):
    r = g.Redactor(terms)
    return r.redact(text), r


def gate(diff, cls="public", **kw):
    kw.setdefault("scanner", CLEAN)
    return g.run_gate(diff, cls, **kw)


class EmailTests(unittest.TestCase):
    def test_real_addresses_redacted(self):
        out, r = red("a jane.doe@planted-mail.org b bob@corp.io c 1234+x@users.noreply.github.com d")
        self.assertNotIn("gmail.com", out)
        self.assertNotIn("corp.io", out)
        self.assertNotIn("users.noreply.github.com", out)
        self.assertEqual(r.counts["EMAIL"], 3)

    def test_kept_domains_survive(self):
        keep = ["a@example.com", "a@example.org", "a@example.net", "a@test.com", "a@foo.test",
                "a@foo.invalid", "root@localhost", "noreply@anthropic.com", "a@mail.example.com"]
        text = " ".join(keep)
        out, r = red(text)
        self.assertEqual(out, text)
        self.assertEqual(r.counts["EMAIL"], 0)

    def test_lookalike_domains_are_not_kept(self):
        out, r = red("a@example.com.evil.io b@evil-example.com c@notanthropic.com noreply@github.com")
        self.assertEqual(r.counts["EMAIL"], 4)
        self.assertNotIn("evil", out)

    def test_code_that_is_not_an_email_survives(self):
        text = "m = a@b  # matmul\nnpm i foo@1.2.3 @types/node"
        out, r = red(text)
        self.assertEqual(out, text)
        self.assertEqual(r.total(), 0)


class PhoneTests(unittest.TestCase):
    def test_us_formats(self):
        for p in ["423-555-0199", "(423) 555-0199", "423.555.0199", "423 555 0199",
                  "+1 423 555 0199", "1-423-555-0199", "+14235550199"]:
            out, r = red(f"call {p} now")
            self.assertEqual(out, "call [PHONE_1] now", p)

    def test_same_number_in_two_formats_shares_a_placeholder(self):
        out, r = red("(423) 555-0199 and +1 423 555 0199 and 423-555-0111")
        self.assertEqual(out, "[PHONE_1] and [PHONE_1] and [PHONE_2]")

    def test_non_phones_survive(self):
        text = "id 12345678-1234-1234-1234-123456789012 ts 1700000000 date 2026-09-30 v1.2.3"
        out, r = red(text)
        self.assertEqual(out, text)


class GeoTests(unittest.TestCase):
    def test_pair_redacted(self):
        out, r = red("at 35.0456, -85.3097 here and [35.0456,-85.3097].")
        self.assertEqual(out, "at [GEO_1] here and [[GEO_1]].")

    def test_out_of_range_and_short_decimals_kept(self):
        for text in ["145.1234, 95.5678", "45.1234, 190.5678", "35.04, -85.30", "35.045, -85.309"]:
            out, r = red(text)
            self.assertEqual(out, text, text)

    def test_distinct_pairs_numbered_in_order(self):
        out, r = red("35.0456, -85.3097 then 40.7128, -74.0060")
        self.assertEqual(out, "[GEO_1] then [GEO_2]")


class AddressTests(unittest.TestCase):
    def test_suffixes(self):
        for s in ["St", "Street", "Ave", "Avenue", "Rd", "Road", "Dr", "Drive", "Ln", "Lane", "Blvd",
                  "Way", "Ct", "Court", "Pike", "Hwy", "Pl", "Place", "Cir"]:
            out, r = red(f"home 123 Main {s} end")
            self.assertEqual(out, "home [ADDR_1] end", s)

    def test_up_to_three_words(self):
        out, r = red("4 Old Mill Creek Rd.")
        self.assertEqual(out, "[ADDR_1].")
        text = "4 Very Old Mill Creek Rd"                # four words before the suffix: no match
        out, r = red(text)
        self.assertEqual(out, text)

    def test_non_address_survives(self):
        text = "retry 3 times via the main road, 5 steps"
        out, r = red(text)
        self.assertEqual(out, text)


class UrlTests(unittest.TestCase):
    def test_query_stripped_path_kept(self):
        out, r = red("see https://x.io/a/b?token=abc&u=1#frag and https://y.io/p and http://z.io/?q=1")
        self.assertEqual(out, "see https://x.io/a/b and https://y.io/p and http://z.io/")
        self.assertEqual(r.counts["URLQ"], 2)

    def test_pii_inside_query_goes_with_it(self):
        out, r = red("https://x.io/cb?email=jon@gmail.com&p=423-555-0199")
        self.assertEqual(out, "https://x.io/cb")
        self.assertEqual(r.counts["EMAIL"] + r.counts["PHONE"], 0)

    def test_markdown_link_paren_not_swallowed(self):
        out, r = red("[docs](https://x.io/a?b=1) next")
        self.assertEqual(out, "[docs](https://x.io/a) next")


class StableNumberingTests(unittest.TestCase):
    def test_same_value_same_placeholder_across_lines(self):
        out, r = red("a@gmail.com\nb@gmail.com\nA@Gmail.com\nc@gmail.com")
        self.assertEqual(out.split("\n"), ["[EMAIL_1]", "[EMAIL_2]", "[EMAIL_1]", "[EMAIL_3]"])
        self.assertEqual(r.counts["EMAIL"], 4)   # occurrences, not distinct values

    def test_kinds_count_independently(self):
        out, r = red("a@gmail.com 423-555-0199 b@gmail.com 423-555-0111")
        self.assertEqual(out, "[EMAIL_1] [PHONE_1] [EMAIL_2] [PHONE_2]")


class TermsTests(unittest.TestCase):
    def test_case_insensitive_whole_word(self):
        out, r = red("Acme and ACME and acme-corp but not Acmetron or pacme", terms=["acme"])
        self.assertEqual(out, "[TERM_1] and [TERM_1] and [TERM_1]-corp but not Acmetron or pacme")

    def test_multiword_and_longest_first(self):
        out, r = red("Acme Corp and Acme", terms=["Acme", "Acme Corp"])
        self.assertEqual(out, "[TERM_1] and [TERM_2]")

    def test_terms_file_skips_comments_and_blanks(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "terms.txt"
            p.write_text("# clients\n\nAcme Corp\n  Jordan  \n#Ignored\n", encoding="utf-8")
            self.assertEqual(g.load_terms(str(p)), ["Acme Corp", "Jordan"])

    def test_missing_terms_file_is_fine(self):
        self.assertEqual(g.load_terms("/nonexistent/dir/terms.txt"), [])

    def test_unreadable_terms_file_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OSError):
                g.load_terms(d)   # a directory: exists, cannot be read as a file


SAMPLE = """diff --git a/src/app.py b/src/app.py
index 111..222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,4 @@
+# owner jane.doe@planted-mail.org phone (423) 555-0199
+# site 35.0456, -85.3097 at 123 Main Street, client Acme Corp
+# docs https://x.io/p?token=SECRETQ
"""
ORIGINALS = ["jane.doe@planted-mail.org", "555-0199", "35.0456", "-85.3097", "123 Main Street",
             "Acme Corp", "SECRETQ"]


class NeverSendTests(unittest.TestCase):
    DIFF = ("diff --git a/docs/a.md b/docs/a.md\n+++ b/docs/a.md\n"
            "diff --git a/old.txt b/new/.env.local\n+++ b/new/.env.local\n"
            "+++ /dev/null\n")

    def test_paths_parsed_from_both_line_kinds(self):
        self.assertEqual(g.diff_paths(self.DIFF), ["docs/a.md", "old.txt", "new/.env.local"])

    def test_glob_forms(self):
        self.assertTrue(g.glob_match("a/b/.env", ".env*"))            # basename
        self.assertTrue(g.glob_match("secrets/key.pem", "**/secrets/**"))
        self.assertTrue(g.glob_match("x/secrets/key.pem", "**/secrets/**"))
        self.assertTrue(g.glob_match("clients/acme/notes.md", "clients/"))
        self.assertTrue(g.glob_match("a/clients/x.md", "clients/"))
        self.assertFalse(g.glob_match("docs/a.md", "*.env"))
        self.assertFalse(g.glob_match("myclients/x.md", "clients/"))

    def test_match_routes_to_local(self):
        res = gate(self.DIFF, never_send=["**/.env*"])
        self.assertEqual(res.gate, "local")
        self.assertIn("never_send", res.reason)
        self.assertNotIn(".env", res.reason)   # reason names a count, never a path

    def test_no_match_still_sends(self):
        self.assertEqual(gate(self.DIFF, never_send=["*.pem"]).gate, "send")


class RoutingTests(unittest.TestCase):
    def test_public_and_own_send(self):
        for cls in ("public", "own"):
            res = gate(SAMPLE, cls)
            self.assertEqual((res.gate, res.reason), ("send", "ok"), cls)

    def test_client_is_local_and_still_redacted(self):
        res = gate(SAMPLE, "client", terms=["Acme Corp"])
        self.assertEqual(res.gate, "local")
        self.assertEqual(res.reason, "client class")
        for original in ORIGINALS:
            self.assertNotIn(original, res.text)
        self.assertIn("[EMAIL_1]", res.text)

    def test_over_threshold_is_local(self):
        diff = "\n".join(f"+u{i}@gmail.com" for i in range(21))
        res = gate(diff)
        self.assertEqual((res.gate, res.count), ("local", 21))
        self.assertIn("21 redactions > max 20", res.reason)

    def test_at_threshold_still_sends_and_max_is_configurable(self):
        diff = "\n".join(f"+u{i}@gmail.com" for i in range(20))
        self.assertEqual(gate(diff).gate, "send")
        self.assertEqual(gate(diff, max_redactions=19).gate, "local")

    def test_reasons_combine(self):
        res = gate(SAMPLE, "client", never_send=["src/*"], max_redactions=1)
        self.assertEqual(res.gate, "local")
        self.assertEqual(res.reason.count(";"), 2)


class SecretScanTests(unittest.TestCase):
    def test_finding_means_skip_and_nothing_is_sent(self):
        res = gate(SAMPLE, scanner=lambda d: "found")
        self.assertEqual((res.gate, res.text), ("skip", ""))
        self.assertEqual(res.reason, "secret scan found a finding")

    def test_unavailable_means_skip(self):
        res = gate(SAMPLE, scanner=lambda d: "unavailable")
        self.assertEqual((res.gate, res.reason), ("skip", "secret scan unavailable"))

    def test_scanner_that_raises_means_skip(self):
        def boom(d):
            raise RuntimeError("docker exploded")
        res = gate(SAMPLE, scanner=boom)
        self.assertEqual((res.gate, res.reason), ("skip", "secret scan unavailable"))

    def test_garbage_status_fails_closed(self):
        self.assertEqual(gate(SAMPLE, scanner=lambda d: "maybe").gate, "skip")

    def test_skip_beats_client_class(self):
        self.assertEqual(gate(SAMPLE, "client", scanner=lambda d: "found").gate, "skip")

    def test_command_is_the_verified_one_and_diff_goes_on_stdin(self):
        seen = {}

        def runner(cmd, text):
            seen["cmd"], seen["text"] = cmd, text
            return 0, "no leaks found"
        self.assertEqual(g.scan_secrets(SAMPLE, runner), "clean")
        self.assertEqual(seen["cmd"][:4], ["docker", "run", "--rm", "-i"])
        i = seen["cmd"].index(g.GITLEAKS_IMAGE)
        self.assertEqual(seen["cmd"][i + 1:], ["stdin", "--no-banner", "--redact", "--exit-code", "1"])
        self.assertEqual(seen["text"], SAMPLE)

    def test_exit_code_reading(self):
        scan = lambda rc, out: g.scan_secrets("x", lambda c, t: (rc, out))  # noqa: E731
        self.assertEqual(scan(0, "INF no leaks found"), "clean")
        self.assertEqual(scan(1, "WRN leaks found: 2"), "found")
        # docker's own failures also exit 1; that must not be believed as a finding
        self.assertEqual(scan(1, "Cannot connect to the Docker daemon"), "unavailable")
        self.assertEqual(scan(125, ""), "unavailable")
        self.assertEqual(scan(127, ""), "unavailable")

    def test_missing_docker_or_timeout_is_unavailable(self):
        def no_docker(c, t):
            raise FileNotFoundError("docker")

        def too_slow(c, t):
            raise subprocess.TimeoutExpired(c, 1)
        self.assertEqual(g.scan_secrets("x", no_docker), "unavailable")
        self.assertEqual(g.scan_secrets("x", too_slow), "unavailable")


class CliTests(unittest.TestCase):
    def run_cli(self, diff_text, *extra, scanner=CLEAN, cls="public", terms="Acme Corp\n"):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        diff, out, tf = (os.path.join(d, n) for n in ("in.diff", "out.diff", "terms.txt"))
        Path(diff).write_text(diff_text, encoding="utf-8")
        Path(tf).write_text(terms, encoding="utf-8")
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = g.main(["--diff", diff, "--class", cls, "--terms-file", tf, "--out", out, *extra],
                        scanner=scanner)
        self.assertEqual(rc, 0)
        written = Path(out).read_text(encoding="utf-8") if os.path.exists(out) else None
        return buf.getvalue(), err.getvalue(), written, out

    def sentinels(self, stdout):
        return dict(line[2:-2].split("=", 1) for line in stdout.splitlines() if line.startswith("__"))

    def test_send_output_lines_and_file(self):
        stdout, _, written, out = self.run_cli(SAMPLE)
        s = self.sentinels(stdout)
        self.assertEqual((s["GATE"], s["GATE_REASON"]), ("send", "ok"))
        self.assertEqual(s["REDACTED"], "6")
        self.assertEqual(s["REDACT_KINDS"], "EMAIL:1,PHONE:1,GEO:1,ADDR:1,URLQ:1,TERM:1")
        self.assertIn("[EMAIL_1]", written)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)

    def test_original_values_never_reach_stdout_or_stderr(self):
        for kw in ({}, {"cls": "client"}, {"scanner": lambda d: "found"},
                   {"scanner": lambda d: "unavailable"}):
            stdout, stderr, _, _ = self.run_cli(SAMPLE, **kw)
            for original in ORIGINALS:
                self.assertNotIn(original, stdout, kw)
                self.assertNotIn(original, stderr, kw)

    def test_skip_writes_an_empty_file(self):
        stdout, _, written, _ = self.run_cli(SAMPLE, scanner=lambda d: "found")
        self.assertEqual(self.sentinels(stdout)["GATE"], "skip")
        self.assertEqual(written, "")

    def test_never_send_accepts_repeats_and_multiple_values(self):
        stdout, *_ = self.run_cli(SAMPLE, "--never-send", "*.pem", "--never-send", "docs/*", "src/*")
        self.assertEqual(self.sentinels(stdout)["GATE"], "local")

    def test_max_redactions_flag(self):
        stdout, *_ = self.run_cli(SAMPLE, "--max-redactions", "2")
        self.assertEqual(self.sentinels(stdout)["GATE"], "local")

    def test_unreadable_terms_file_fails_closed(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        diff, out = os.path.join(d, "in.diff"), os.path.join(d, "out.diff")
        Path(diff).write_text(SAMPLE, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            g.main(["--diff", diff, "--class", "public", "--terms-file", d, "--out", out], scanner=CLEAN)
        self.assertEqual(self.sentinels(buf.getvalue())["GATE"], "skip")

    def test_missing_diff_fails_closed(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            g.main(["--diff", "/nonexistent/x.diff", "--class", "public", "--terms-file", "/nonexistent/t",
                    "--out", os.path.join(d, "out.diff")], scanner=CLEAN)
        self.assertEqual(self.sentinels(buf.getvalue())["GATE"], "skip")


class AuditTermsFileTests(unittest.TestCase):
    def test_missing_terms_file_forces_local_for_own_and_client(self):
        for cls in ("own", "client"):
            res = gate("x = 1\n", cls, terms_missing=True)
            self.assertEqual(res.gate, "local", cls)
            self.assertIn("no terms file", res.reason)

    def test_missing_terms_file_lets_public_through(self):
        self.assertEqual(gate("x = 1\n", "public", terms_missing=True).gate, "send")

    def test_present_but_empty_file_is_present(self):
        with tempfile.TemporaryDirectory() as d:
            tf, diff, out = (os.path.join(d, n) for n in ("t.txt", "in.diff", "out"))
            Path(tf).write_text("", encoding="utf-8")
            Path(diff).write_text("x = 1\n", encoding="utf-8")
            a = g.parse_args(["--diff", diff, "--class", "own", "--terms-file", tf, "--out", out])
            self.assertEqual(g.gate_from_args(a, CLEAN).gate, "send")
            a = g.parse_args(["--diff", diff, "--class", "own", "--terms-file", os.path.join(d, "no"),
                              "--out", out])
            res = g.gate_from_args(a, CLEAN)
            self.assertEqual((res.gate, res.reason), ("local", "no terms file"))


class AuditTermBoundaryTests(unittest.TestCase):
    def test_identifier_shapes_match(self):
        for text in ["acme_corp", "AcmeClient", "getAcmeUser()", "x.acme.y", "acme2", "ACME_KEY"]:
            out, r = red(text, terms=["acme"])
            self.assertIn("[TERM_1]", out, text)

    def test_letters_either_side_do_not_match(self):
        for text in ["macmeal", "Acmetron", "pacme", "macmeal acmex"]:
            out, r = red(text, terms=["acme"])
            self.assertEqual(out, text, text)

    def test_overlapping_retry_after_a_rejected_hit(self):
        out, r = red("macmeal acme", terms=["acme"])
        self.assertEqual(out, "macmeal [TERM_1]")


class AuditNeverSendTests(unittest.TestCase):
    def test_mnemonic_and_no_prefix_headers(self):
        for hdr in ["diff --git c/.env w/.env", "diff --git i/.env o/.env", "diff --git .env .env"]:
            self.assertIn(".env", g.diff_paths(hdr + "\n@@ -1 +1 @@\n"), hdr)
            self.assertEqual(gate(hdr + "\n@@ -1 +1 @@\n", never_send=[".env*"]).gate, "local", hdr)

    def test_quoted_c_style_paths(self):
        self.assertEqual(g.diff_paths('diff --git "a/s\\303\\251/k.pem" "b/s\\303\\251/k.pem"\n'),
                         ["s\u00e9/k.pem"])
        self.assertEqual(g.diff_paths('+++ "b/a\\tb"\n'), ["a\tb"])
        d = 'diff --git "a/my secrets/x.txt" "b/my secrets/x.txt"\n@@ -1 +1 @@\n'
        self.assertEqual(gate(d, never_send=["my secrets/"]).gate, "local")

    def test_plus_minus_lines_without_prefix(self):
        self.assertEqual(g.diff_paths("--- .ENV\t2020\n+++ .ENV\n"), [".ENV"])

    def test_glob_is_case_insensitive(self):
        self.assertTrue(g.glob_match("Config/.ENV.Local", "**/.env*"))
        self.assertTrue(g.glob_match("SECRETS/a.txt", "secrets/"))
        res = gate("diff --git a/Data/X.CSV b/Data/X.CSV\n@@ -1 +1 @@\n", never_send=["**/*.csv"])
        self.assertEqual(res.gate, "local")

    def test_hunks_without_parsable_path_go_local(self):
        res = gate("@@ -1,2 +1,2 @@\n-a\n+b\n")
        self.assertEqual(res.gate, "local")
        self.assertEqual(res.reason, "unparsed paths")
        self.assertEqual(gate("just text, no hunks\n").gate, "send")


class AuditScannerTests(unittest.TestCase):
    def test_container_is_hardened_and_pinned(self):
        cmd = g.GITLEAKS_CMD
        self.assertIn("@sha256:", g.GITLEAKS_IMAGE)
        self.assertNotIn(":latest", g.GITLEAKS_IMAGE)
        self.assertEqual(cmd[cmd.index("--network") + 1], "none")
        self.assertEqual(cmd[cmd.index("--cap-drop") + 1], "ALL")
        self.assertEqual(cmd[cmd.index("--security-opt") + 1], "no-new-privileges")
        self.assertIn("--read-only", cmd)

    def test_allow_marker_is_stripped_before_scanning(self):
        seen = {}
        g.scan_secrets("key = 1  # gitleaks:allow\nx # GITLEAKS : Allow",
                       lambda c, t: (seen.setdefault("t", t), (0, "no leaks found"))[1])
        self.assertNotIn("allow", seen["t"].lower())

    def test_clean_needs_positive_evidence(self):
        scan = lambda rc, out: g.scan_secrets("x", lambda c, t: (rc, out))  # noqa: E731
        self.assertEqual(scan(0, ""), "unavailable")
        self.assertEqual(scan(0, "garbage"), "unavailable")
        self.assertEqual(scan(0, "WRN leaks found: 2"), "unavailable")
        self.assertEqual(scan(0, "INF no leaks found"), "clean")
        self.assertEqual(scan(1, "INF no leaks found"), "unavailable")

    def test_scans_arbitrary_text(self):
        self.assertEqual(g.scan_secrets("acceptance: ship it", lambda c, t: (0, "no leaks found")), "clean")


class AuditRedactionTests(unittest.TestCase):
    def test_url_userinfo_without_tld(self):
        for url in ["postgres://admin:hunter2@db/app", "redis://:pw0rd@cache:6379/0",
                    "https://bob:pw@localhost:8080/x", "https://tok@host/x"]:
            out, r = red(f"conn {url} end")
            for secret in ("hunter2", "pw0rd", "pw@", "tok@", "bob", "admin"):
                self.assertNotIn(secret, out, url)
            self.assertIn("[USERINFO]@", out)
            self.assertGreaterEqual(r.counts["URLQ"], 1)

    def test_percent_encoded_email(self):
        out, r = red("path /u/jane.doe%40gmail.com/x and a%40example.com")
        self.assertNotIn("jane", out)
        self.assertIn("[EMAIL_1]", out)
        self.assertIn("a%40example.com", out)   # kept domains stay kept

    def test_token_fragments(self):
        for frag in ["access_token", "id_token", "code"]:
            out, r = red(f"go https://app.io/cb#{frag}=abc.DEF-1&state=zz now")
            self.assertEqual(out, "go https://app.io/cb now", frag)
            self.assertEqual(r.counts["URLQ"], 1)
        out, _ = red("plain https://app.io/docs#install stays")
        self.assertIn("#install", out)

    def test_coordinates_in_either_order(self):
        out, r = red("lng-first [123.4567, 35.0456] and 35.0456,-85.3097")
        self.assertEqual(out, "lng-first [[GEO_1]] and [GEO_2]")

    def test_coordinate_keys(self):
        out, r = red('{"lat": 35.0456, "lng": -85.3097} latitude=35.04561 longitude: -85.30971')
        for n in ("35.04", "85.30"):
            self.assertNotIn(n, out)
        self.assertEqual(r.counts["GEO"], 4)
        out, _ = red('{"lat": 0, "flat": 1.2345, "lng": 12}')
        self.assertEqual(out, '{"lat": 0, "flat": 1.2345, "lng": 12}')

    def test_bare_ten_digit_phone(self):
        out, r = red("call 4235550199 now")
        self.assertEqual(out, "call [PHONE_1] now")
        same, r = red("423-555-0199 and 4235550199")
        self.assertEqual(same, "[PHONE_1] and [PHONE_1]")

    def test_timestamps_and_ids_survive(self):
        text = "ts 1700000000 ms 1700000000123 id 9999999999999 n 0123456789 v 4235550199123"
        out, _ = red(text)
        self.assertEqual(out, text)

    def test_international_numbers(self):
        for p in ["+44 20 7946 0958", "+442079460958", "+49 30 901820", "+33 1 23 45 67 89"]:
            out, r = red(f"tel {p} end")
            self.assertEqual(out, "tel [PHONE_1] end", p)
        text = "version +1.2.3 and +5 items"
        self.assertEqual(red(text)[0], text)

    def test_uppercase_street_address(self):
        for a in ["123 MAIN ST", "9 OLD MILL CREEK ROAD", "77 ELM AVE"]:
            out, r = red(f"ship to {a}.")
            self.assertEqual(out, "ship to [ADDR_1].", a)

    def test_author_and_coauthor_names(self):
        text = ("Author: Jane Q. Doe <jane@corp.io>\n+Co-authored-by: Bob Roe <bob@planted-mail.org>\n"
                "Co-Authored-By: Claude <noreply@anthropic.com>\n")
        out, r = red(text)
        for n in ("Jane", "Doe", "Bob", "Roe", "corp.io", "gmail.com"):
            self.assertNotIn(n, out)
        self.assertIn("Claude <noreply@anthropic.com>", out)
        self.assertEqual(r.counts["TERM"], 2)
        self.assertEqual(r.counts["EMAIL"], 2)


if __name__ == "__main__":
    unittest.main()


class UnlistedRepoTests(unittest.TestCase):
    def setUp(self):
        import tempfile, json
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "classes.json")
        with open(self.path, "w") as f:
            json.dump({"repos": {"PublicRepo": "public", "OwnRepo": "own"}}, f)

    def test_a_repo_the_file_does_not_list_is_client(self):
        self.assertEqual(g.effective_class("SomeClientRepo", "public", self.path), "client")

    def test_listed_repos_keep_the_stricter_class(self):
        self.assertEqual(g.effective_class("publicrepo", "public", self.path), "public")
        self.assertEqual(g.effective_class("OwnRepo", "public", self.path), "own")
        self.assertEqual(g.effective_class("PublicRepo", "client", self.path), "client")

    def test_no_repo_name_keeps_the_passed_class(self):
        self.assertEqual(g.effective_class(None, "public", self.path), "public")

