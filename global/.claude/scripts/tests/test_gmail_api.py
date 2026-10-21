"""gmail_api.py: OAuth/PKCE, MIME building, redirect unwrapping, the no-send guard, and the draft commands against a fake
Gmail (http_request is replaced; nothing here touches the network or the real ~/.config/afa/gmail)."""

import base64
import email
import email.policy
import hashlib
import io
import json
import os
import re
import stat
import sys
import threading
import unittest
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import gmail_api as g  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

SRC = (HERE.parent / "gmail_api.py").read_text()
TS = "&source=gmail&ust=1792618800000000&sa=E"


def wrap(target):
    return "https://www.google.com/url?q=" + target + TS


class Creds(unittest.TestCase):
    def setUp(self):
        self.dir = Path(fresh_dir(self))
        env = mock.patch.dict(os.environ, {"AFA_GMAIL_DIR": str(self.dir)})
        env.start()
        self.addCleanup(env.stop)
        (self.dir / "client.json").write_text(json.dumps({"installed": {"client_id": "cid.apps.googleusercontent.com", "client_secret": "sec"}}))
        g.write_private("token.json", {"refresh_token": "r1", "access_token": "a-old", "expires_at": 0})


class Pkce(unittest.TestCase):
    def test_challenge_is_s256_of_verifier(self):
        v, c = g.make_pkce()
        self.assertTrue(43 <= len(v) <= 128)
        want = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(c, want)
        self.assertNotIn("=", c)

    def test_auth_url_carries_pkce_state_scope_and_offline(self):
        u = g.build_auth_url("cid", "http://127.0.0.1:5000", "CHAL", "ST")
        p = urllib.parse.urlparse(u)
        self.assertEqual((p.scheme, p.netloc, p.path), ("https", "accounts.google.com", "/o/oauth2/v2/auth"))
        q = dict(urllib.parse.parse_qsl(p.query))
        self.assertEqual(q["code_challenge"], "CHAL")
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertEqual(q["state"], "ST")
        self.assertEqual(q["redirect_uri"], "http://127.0.0.1:5000")
        self.assertEqual(q["response_type"], "code")
        self.assertEqual(q["access_type"], "offline")
        self.assertEqual(q["scope"], "https://www.googleapis.com/auth/gmail.modify")


class AuthFlow(Creds):
    def test_loopback_flow_exchanges_code_with_verifier_and_stores_private_token(self):
        seen = {}

        def fake_open(url):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
            seen["challenge"] = q["code_challenge"]
            cb = q["redirect_uri"] + "?code=THECODE&state=" + q["state"]
            threading.Thread(target=lambda: urllib.request.urlopen(cb, timeout=10).read()).start()

        def fake_http(method, url, headers=None, body=None, timeout=60):
            seen["url"], seen["form"] = url, dict(urllib.parse.parse_qsl(body.decode()))
            return 200, json.dumps({"access_token": "A", "refresh_token": "R", "expires_in": 3600}).encode()

        with mock.patch.object(g.webbrowser, "open", fake_open), mock.patch.object(g, "http_request", fake_http), redirect_stdout(io.StringIO()):
            g.run_auth(timeout=20)
        self.assertEqual(seen["url"], g.TOKEN_URL)
        f = seen["form"]
        self.assertEqual((f["code"], f["grant_type"]), ("THECODE", "authorization_code"))
        chal = base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(chal, seen["challenge"])
        tok = self.dir / "token.json"
        self.assertEqual(json.loads(tok.read_text())["refresh_token"], "R")
        self.assertEqual(stat.S_IMODE(tok.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)

    def test_wrong_state_is_refused(self):
        def fake_open(url):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
            cb = q["redirect_uri"] + "?code=X&state=forged"
            threading.Thread(target=lambda: urllib.request.urlopen(cb, timeout=10).read()).start()

        with mock.patch.object(g.webbrowser, "open", fake_open), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(g.GmailError, "state mismatch"):
                g.run_auth(timeout=20)

    def test_expired_access_token_is_refreshed_and_saved(self):
        calls = []

        def fake_http(method, url, headers=None, body=None, timeout=60):
            calls.append(dict(urllib.parse.parse_qsl(body.decode())))
            return 200, json.dumps({"access_token": "a-new", "expires_in": 3600}).encode()

        with mock.patch.object(g, "http_request", fake_http):
            self.assertEqual(g.access_token(), "a-new")
            self.assertEqual(g.access_token(), "a-new")
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["grant_type"], calls[0]["refresh_token"]), ("refresh_token", "r1"))
        self.assertEqual(stat.S_IMODE((self.dir / "token.json").stat().st_mode), 0o600)

    def test_dead_refresh_token_says_to_reauth(self):
        with mock.patch.object(g, "http_request", lambda *a, **k: (400, b'{"error": "invalid_grant"}')):
            with self.assertRaisesRegex(g.GmailError, "gmail_api.py auth"):
                g.access_token()


class Unwrap(unittest.TestCase):
    def test_samples_from_the_connector_memory(self):
        for target in ("https://asbuilt.expo.app/beta/", "https://cloud.chattanooga.digital/f/4170",
                       "https://chattanooga.digital/sponsors", "https://github.com/TortoiseWolfe/Claude_Commandz/pull/80"):
            self.assertEqual(g.unwrap_redirect(wrap(target)), target)

    def test_encoded_hash_and_ampersand(self):
        self.assertEqual(g.unwrap_redirect(wrap("https://x.com/a?b%3D1%26c%3D2%23frag")), "https://x.com/a?b=1&c=2#frag")

    def test_unencoded_hash_and_ampersand_survive(self):
        self.assertEqual(g.unwrap_redirect(wrap("https://x.com/a?b=1&c=2#frag")), "https://x.com/a?b=1&c=2#frag")

    def test_literal_percent_escapes_in_an_unencoded_target_are_kept(self):
        self.assertEqual(g.unwrap_redirect(wrap("https://x.com/a%20b")), "https://x.com/a%20b")

    def test_other_urls_are_left_alone(self):
        for u in ("https://chattanooga.digital/", "javascript:void(0);", "mailto:a@b.co", "https://google.com/search?q=1"):
            self.assertEqual(g.unwrap_redirect(u), u)

    def test_html_unwrap_changes_only_the_href(self):
        src = '<p>Hi <b><a href="%s" target="_blank">site</a></b> and <a href="https://ok.example/">ok</a></p>' % wrap("https://x.com/a?b=1&c=2").replace("&", "&amp;")
        out = g.unwrap_html(src)
        self.assertEqual(g.hrefs(out), ["https://x.com/a?b=1&c=2", "https://ok.example/"])
        self.assertEqual(re.sub(r'href="[^"]*"', "H", out), re.sub(r'href="[^"]*"', "H", src))
        self.assertNotIn("google.com/url", out)

    def test_plain_text_urls_unwrap(self):
        self.assertEqual(g.unwrap_plain("see " + wrap("https://x.com/p") + " ok"), "see https://x.com/p ok")


class Mime(unittest.TestCase):
    def parse(self, msg):
        return email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)

    def test_html_with_text_alternative(self):
        m = self.parse(g.build_message("a@b.co", "Hello", '<p>See <b><a href="https://chattanooga.digital/x">x</a></b></p>', cc="c@d.co"))
        self.assertEqual(m.get_content_type(), "multipart/alternative")
        kinds = [p.get_content_type() for p in m.iter_parts()]
        self.assertEqual(kinds, ["text/plain", "text/html"])
        self.assertIn("https://chattanooga.digital/x", m.get_body(("plain",)).get_content())
        self.assertEqual((m["To"], m["Cc"], m["Subject"]), ("a@b.co", "c@d.co", "Hello"))

    def test_given_text_is_used_as_is(self):
        m = self.parse(g.build_message("a@b.co", "S", "<p>h</p>", "plain version"))
        self.assertEqual(m.get_body(("plain",)).get_content().strip(), "plain version")

    def test_reply_headers(self):
        m = self.parse(g.build_message("a@b.co", "Re: S", None, "t", reply={"message_id": "<m2@x>", "references": "<m0@x> <m1@x>"}))
        self.assertEqual(m["In-Reply-To"], "<m2@x>")
        self.assertEqual(m["References"], "<m0@x> <m1@x> <m2@x>")

    def test_needs_a_body(self):
        with self.assertRaises(g.GmailError):
            g.build_message("a@b.co", "S")


class NoSend(unittest.TestCase):
    def test_source_has_no_send_endpoint(self):
        for needle in ("messages/send", "drafts/send", "/send", ":send"):
            self.assertNotIn(needle, SRC, needle)

    def test_api_refuses_any_send_path(self):
        for p in ("/messages/send", "/drafts/send", "/drafts/x/Send"):
            with self.assertRaisesRegex(g.GmailError, "never sends"):
                g.api("POST", p)


class FakeGmail:
    """Just enough of users.me for the draft commands."""

    def __init__(self):
        self.drafts, self.labels, self.log = {}, {"Label_31": "agent-notes"}, []
        self.n = 0

    def add(self, raw_bytes, thread="T1"):
        self.n += 1
        did = f"r{self.n}"
        self.drafts[did] = {"message": {"id": f"m{self.n}", "threadId": thread, "labelIds": ["DRAFT"], "raw": g.b64u_encode(raw_bytes)}}
        return did

    def __call__(self, method, url, headers=None, body=None, timeout=60):
        p = urllib.parse.urlparse(url)
        path, q = p.path.replace("/gmail/v1/users/me", ""), dict(urllib.parse.parse_qsl(p.query))
        data = json.loads(body) if body else None
        self.log.append((method, path))
        if method == "POST" and path == "/drafts":
            return 200, json.dumps(self._store(None, data)).encode()
        m = re.fullmatch(r"/drafts/(\w+)", path)
        if m and m[1] in self.drafts:
            d = self.drafts[m[1]]
            if method == "GET":
                return 200, json.dumps({"id": m[1], "message": d["message"]}).encode()
            if method == "PUT":
                return 200, json.dumps(self._store(m[1], data)).encode()
            if method == "DELETE":
                del self.drafts[m[1]]
                return 204, b""
        if path == "/labels":
            return 200, json.dumps({"labels": [{"id": i, "name": n} for i, n in self.labels.items()]}).encode()
        m = re.fullmatch(r"/messages/(\w+)/modify", path)
        if m and method == "POST":
            for d in self.drafts.values():
                if d["message"]["id"] == m[1]:
                    ids = d["message"]["labelIds"]
                    ids += [x for x in data.get("addLabelIds", []) if x not in ids]
                    d["message"]["labelIds"] = [x for x in ids if x not in data.get("removeLabelIds", [])]
            return 200, b"{}"
        return 404, json.dumps({"error": {"message": f"no {method} {path}"}}).encode()

    def _store(self, did, data):
        msg = data["message"]
        if did is None:
            self.n += 1
            did = f"r{self.n}"
        self.drafts[did] = {"message": {"id": f"m{self.n}x", "threadId": msg.get("threadId", f"T{self.n}"), "labelIds": ["DRAFT"], "raw": msg["raw"]}}
        return {"id": did, "message": self.drafts[did]["message"]}


class Commands(Creds):
    def setUp(self):
        super().setUp()
        g.write_private("token.json", {"refresh_token": "r1", "access_token": "a", "expires_at": 9e12})
        self.fake = FakeGmail()
        p = mock.patch.object(g, "http_request", self.fake)
        p.start()
        self.addCleanup(p.stop)

    def raw_with_redirects(self):
        html = ('<div>Greg, <b><a href="%s" target="_blank">cloud</a></b> and '
                '<b><a href="%s">repo</a></b></div>') % (wrap("https://cloud.chattanooga.digital/f/4170"), wrap("https://github.com/o/r/pull/1?a=1%26b%3D2%23x"))
        m = g.build_message("jonpohlner@gmail.com", "Re: Greg", html, reply={"message_id": "<abc@mail.gmail.com>", "references": "<abc@mail.gmail.com>"})
        m["X-Custom"] = "keep-me"
        return m.as_bytes()

    def test_fix_links_unwraps_everywhere_and_keeps_thread_and_headers(self):
        did = self.fake.add(self.raw_with_redirects(), thread="THREAD9")
        before = email.message_from_bytes(g.b64u_decode(self.fake.drafts[did]["message"]["raw"]), policy=email.policy.default)
        r = g.draft_fix_links(did)
        self.assertEqual((r["changed"], r["threadId"]), (2, "THREAD9"))
        text, redirects = g.draft_view(did)
        self.assertEqual(redirects, 0)
        self.assertIn("https://cloud.chattanooga.digital/f/4170", text)
        self.assertIn("https://github.com/o/r/pull/1?a=1&b=2#x", text)
        after = email.message_from_bytes(g.b64u_decode(self.fake.drafts[did]["message"]["raw"]), policy=email.policy.default)
        for h in ("To", "Subject", "In-Reply-To", "References", "X-Custom"):
            self.assertEqual(after[h], before[h], h)
        self.assertEqual(self.fake.drafts[did]["message"]["threadId"], "THREAD9")

    def test_fix_links_is_a_no_op_on_a_clean_draft(self):
        did = self.fake.add(g.build_message("a@b.co", "S", '<a href="https://ok.example/">x</a>').as_bytes())
        self.assertEqual(g.draft_fix_links(did)["changed"], 0)
        self.assertNotIn(("PUT", f"/drafts/{did}"), self.fake.log)

    def test_create_keeps_hrefs_and_sets_reply_thread(self):
        def with_msg(method, url, headers=None, body=None, timeout=60):
            if "/messages/orig" in url:
                return 200, json.dumps({"id": "orig", "threadId": "TH7", "payload": {"headers": [
                    {"name": "Message-ID", "value": "<o1@x>"}, {"name": "References", "value": "<o0@x>"}, {"name": "Subject", "value": "Hello"}]}}).encode()
            return self.fake(method, url, headers, body, timeout)

        with mock.patch.object(g, "http_request", with_msg):
            d = g.draft_create("jonpohlner@gmail.com", "", '<b><a href="https://chattanooga.digital/a">a</a></b>', reply_to="orig")
        self.assertEqual(d["message"]["threadId"], "TH7")
        m = email.message_from_bytes(g.b64u_decode(self.fake.drafts[d["id"]]["message"]["raw"]), policy=email.policy.default)
        self.assertEqual((m["In-Reply-To"], m["References"], m["Subject"]), ("<o1@x>", "<o0@x> <o1@x>", "Re: Hello"))

    def test_label_unlabel_by_name_and_id(self):
        did = self.fake.add(b"Subject: s\r\nTo: a@b.co\r\n\r\nhi\r\n")
        g.draft_label(did, "agent-notes")
        self.assertIn("Label_31", self.fake.drafts[did]["message"]["labelIds"])
        g.draft_label(did, "Label_31", remove=True)
        self.assertNotIn("Label_31", self.fake.drafts[did]["message"]["labelIds"])
        with self.assertRaises(g.GmailError):
            g.draft_label(did, "nope")

    def test_delete(self):
        did = self.fake.add(b"Subject: s\r\n\r\nhi\r\n")
        g.draft_delete(did)
        self.assertNotIn(did, self.fake.drafts)

    def test_no_command_touches_a_send_path(self):
        did = self.fake.add(self.raw_with_redirects())
        g.draft_fix_links(did)
        g.draft_label(did, "agent-notes")
        g.draft_delete(did)
        self.assertFalse([c for c in self.fake.log if "send" in c[1].lower()])

    def test_cli_draft_raw_prints_ids_hrefs_and_count_but_no_token(self):
        did = self.fake.add(self.raw_with_redirects())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(g.main(["draft-raw", did]), 0)
        s = out.getvalue()
        self.assertIn("google.com/url count: 4", s)
        self.assertIn(f"draftId: {did}", s)
        self.assertNotIn("Bearer", s)


if __name__ == "__main__":
    unittest.main()
