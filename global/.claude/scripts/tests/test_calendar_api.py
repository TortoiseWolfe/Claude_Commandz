"""calendar_api.py: the read-only calendar token (separate from Gmail's), the GET-only guard and event parsing, against a fake
Google (gmail_api.http_request is replaced; nothing here touches the network or the real ~/.config/afa/gmail)."""

import json
import os
import re
import stat
import sys
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import calendar_api as c  # noqa: E402
import gmail_api as g  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

SRC = (HERE.parent / "calendar_api.py").read_text()


class Creds(unittest.TestCase):
    def setUp(self):
        self.dir = Path(fresh_dir(self))
        env = mock.patch.dict(os.environ, {"AFA_GMAIL_DIR": str(self.dir)})
        env.start()
        self.addCleanup(env.stop)
        (self.dir / "client.json").write_text(json.dumps({"installed": {"client_id": "cid.apps.googleusercontent.com", "client_secret": "sec"}}))
        g.write_private("token.json", {"refresh_token": "gmail-r", "access_token": "gmail-a", "expires_at": 0})
        self.gmail_before = (self.dir / "token.json").read_text()


def reply(status, body):
    return (status, json.dumps(body).encode())


class AuthUrl(unittest.TestCase):
    def test_scope_is_calendar_readonly_and_not_gmail(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(c.build_auth_url("cid", "http://127.0.0.1:1", "CH", "ST")).query))
        self.assertEqual(q["scope"], "https://www.googleapis.com/auth/calendar.readonly")
        self.assertNotIn("gmail", q["scope"])
        self.assertEqual((q["code_challenge"], q["code_challenge_method"], q["state"]), ("CH", "S256", "ST"))
        self.assertEqual(q["access_type"], "offline")


class TokenFile(Creds):
    def test_missing_token_says_to_run_auth(self):
        with self.assertRaises(c.CalendarError) as cm:
            c.access_token()
        self.assertIn("calendar_api.py auth", str(cm.exception))

    def test_fresh_access_token_is_used_without_a_network_call(self):
        g.write_private(c.TOKEN, {"refresh_token": "r", "access_token": "live", "expires_at": time.time() + 600})
        with mock.patch.object(g, "http_request", side_effect=AssertionError("no network")):
            self.assertEqual(c.access_token(), "live")

    def test_expired_token_refreshes_and_rewrites_only_the_calendar_file(self):
        g.write_private(c.TOKEN, {"refresh_token": "cal-r", "access_token": "old", "expires_at": 0})
        with mock.patch.object(g, "http_request", return_value=reply(200, {"access_token": "new", "expires_in": 3600})) as h:
            self.assertEqual(c.access_token(), "new")
        sent = urllib.parse.parse_qs(h.call_args[0][3].decode())
        self.assertEqual(sent["refresh_token"], ["cal-r"])
        self.assertEqual(sent["grant_type"], ["refresh_token"])
        saved = json.loads((self.dir / c.TOKEN).read_text())
        self.assertEqual((saved["access_token"], saved["refresh_token"]), ("new", "cal-r"))
        self.assertEqual((self.dir / "token.json").read_text(), self.gmail_before)

    def test_dead_refresh_token_names_the_fix(self):
        g.write_private(c.TOKEN, {"refresh_token": "r", "access_token": "old", "expires_at": 0})
        with mock.patch.object(g, "http_request", return_value=reply(400, {"error": "invalid_grant"})):
            with self.assertRaises(c.CalendarError) as cm:
                c.access_token()
        self.assertIn("calendar_api.py auth", str(cm.exception))

    def test_other_token_errors_pass_through(self):
        g.write_private(c.TOKEN, {"refresh_token": "r", "access_token": "old", "expires_at": 0})
        with mock.patch.object(g, "http_request", return_value=reply(500, {"error": "backend_error"})):
            with self.assertRaises(c.CalendarError) as cm:
                c.access_token()
        self.assertIn("backend_error", str(cm.exception))
        self.assertNotIn("dead", str(cm.exception))

    def test_token_file_is_mode_600(self):
        g.write_private(c.TOKEN, {"refresh_token": "r", "access_token": "old", "expires_at": 0})
        with mock.patch.object(g, "http_request", return_value=reply(200, {"access_token": "new", "expires_in": 60})):
            c.access_token()
        self.assertEqual(stat.S_IMODE((self.dir / c.TOKEN).stat().st_mode), 0o600)


class RunAuth(Creds):
    def _drive(self, code="CODE", state_ok=True, error=None, refresh="cal-r"):
        """Run run_auth with the browser replaced by a thread that hits the loopback redirect."""
        import threading
        import urllib.request

        def browser(url):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
            redirect = q["redirect_uri"]
            params = {"error": error} if error else {"code": code, "state": q["state"] if state_ok else "wrong"}
            threading.Thread(target=lambda: urllib.request.urlopen(redirect + "/?" + urllib.parse.urlencode(params)).read()).start()

        tok = {"access_token": "acc", "expires_in": 3600, "scope": c.SCOPE}
        if refresh:
            tok["refresh_token"] = refresh
        with mock.patch.object(c.webbrowser, "open", side_effect=browser), \
                mock.patch.object(g, "http_request", return_value=reply(200, tok)) as h, \
                mock.patch("builtins.print"):
            try:
                return c.run_auth(timeout=10), h
            finally:
                pass

    def test_writes_calendar_token_and_leaves_gmail_token_alone(self):
        ok, h = self._drive()
        self.assertTrue(ok)
        saved = json.loads((self.dir / c.TOKEN).read_text())
        self.assertEqual(saved["refresh_token"], "cal-r")
        self.assertEqual(stat.S_IMODE((self.dir / c.TOKEN).stat().st_mode), 0o600)
        self.assertEqual((self.dir / "token.json").read_text(), self.gmail_before)
        sent = urllib.parse.parse_qs(h.call_args[0][3].decode())
        self.assertEqual(sent["code"], ["CODE"])
        self.assertIn("code_verifier", sent)

    def test_refused_consent_raises_and_writes_nothing(self):
        with self.assertRaises(c.CalendarError) as cm:
            self._drive(error="access_denied")
        self.assertIn("access_denied", str(cm.exception))
        self.assertFalse((self.dir / c.TOKEN).exists())

    def test_state_mismatch_raises_and_writes_nothing(self):
        with self.assertRaises(c.CalendarError) as cm:
            self._drive(state_ok=False)
        self.assertIn("state mismatch", str(cm.exception))
        self.assertFalse((self.dir / c.TOKEN).exists())

    def test_no_refresh_token_in_the_answer_raises(self):
        with self.assertRaises(c.CalendarError) as cm:
            self._drive(refresh=None)
        self.assertIn("no refresh token", str(cm.exception))
        self.assertFalse((self.dir / c.TOKEN).exists())


class ApiGuard(Creds):
    def setUp(self):
        super().setUp()
        g.write_private(c.TOKEN, {"refresh_token": "r", "access_token": "live", "expires_at": time.time() + 600})

    def test_refuses_every_method_but_get(self):
        for m in ("POST", "PUT", "PATCH", "DELETE"):
            with mock.patch.object(g, "http_request", side_effect=AssertionError("must not call")):
                with self.assertRaises(c.CalendarError):
                    c.api("/calendars/primary/events", method=m)

    def test_get_sends_bearer_and_params(self):
        with mock.patch.object(g, "http_request", return_value=reply(200, {"id": "me@acme.example"})) as h:
            self.assertEqual(c.api("/calendars/primary", {"fields": "id"})["id"], "me@acme.example")
        method, url, headers = h.call_args[0][:3]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "https://www.googleapis.com/calendar/v3/calendars/primary?fields=id")
        self.assertEqual(headers["Authorization"], "Bearer live")

    def test_http_error_becomes_calendar_error(self):
        with mock.patch.object(g, "http_request", return_value=reply(403, {"error": {"message": "API not enabled"}})):
            with self.assertRaises(c.CalendarError) as cm:
                c.api("/calendars/primary")
        self.assertIn("403", str(cm.exception))
        self.assertIn("API not enabled", str(cm.exception))

    def test_source_has_no_write_verbs_besides_the_token_endpoint(self):
        verbs = re.findall(r'["\'](POST|PUT|PATCH|DELETE)["\']', SRC)
        self.assertEqual(verbs, [])
        self.assertEqual(re.findall(r'http_request\(\s*["\'](\w+)["\']', SRC), ["GET"])


def item(summary="Standup", start="2026-10-27T16:00:00-04:00", end="2026-10-27T16:30:00-04:00", **kw):
    d = {"status": "confirmed", "summary": summary, "start": {"dateTime": start}, "end": {"dateTime": end}}
    d.update(kw)
    return d


class ListEvents(Creds):
    def setUp(self):
        super().setUp()
        g.write_private(c.TOKEN, {"refresh_token": "r", "access_token": "live", "expires_at": time.time() + 600})
        sleep = mock.patch.object(c.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def run_list(self, *pages):
        with mock.patch.object(g, "http_request", side_effect=[reply(*p) if isinstance(p, tuple) else reply(200, p) for p in pages]) as h:
            return c.list_events(1_000_000_000, 1_000_100_000), h

    def test_flattens_titles_lowercases_attendees_and_keeps_times(self):
        evs, _ = self.run_list({"items": [item("C.D\tsysadmin\nsync ", attendees=[{"email": "Bob@Acme.Example"}, {"email": "me@acme.example", "self": True}])]})
        self.assertEqual(evs, [{"start": "2026-10-27T16:00:00-04:00", "end": "2026-10-27T16:30:00-04:00", "title": "C.D sysadmin sync",
                                "attendees": ["bob@acme.example", "me@acme.example"], "self_declined": False}])

    def test_skips_cancelled_and_all_day_events(self):
        evs, _ = self.run_list({"items": [
            item("kept"), item("gone", status="cancelled"),
            {"status": "confirmed", "summary": "holiday", "start": {"date": "2026-10-27"}, "end": {"date": "2026-10-28"}},
        ]})
        self.assertEqual([e["title"] for e in evs], ["kept"])

    def test_flags_only_my_own_decline(self):
        evs, _ = self.run_list({"items": [
            item("mine", attendees=[{"email": "me@acme.example", "self": True, "responseStatus": "declined"}]),
            item("theirs", attendees=[{"email": "bob@acme.example", "responseStatus": "declined"}, {"email": "me@acme.example", "self": True, "responseStatus": "accepted"}]),
            item("none"),
        ]})
        self.assertEqual({e["title"]: e["self_declined"] for e in evs}, {"mine": True, "theirs": False, "none": False})

    def test_missing_summary_is_an_empty_title(self):
        evs, _ = self.run_list({"items": [item(summary=None)]})
        self.assertEqual(evs[0]["title"], "")

    def test_follows_next_page_token(self):
        evs, h = self.run_list({"items": [item("one")], "nextPageToken": "P2"}, {"items": [item("two")]})
        self.assertEqual([e["title"] for e in evs], ["one", "two"])
        self.assertIn("pageToken=P2", h.call_args_list[1][0][1])
        self.assertNotIn("pageToken", h.call_args_list[0][0][1])

    def test_asks_for_expanded_recurring_events_in_the_window(self):
        _, h = self.run_list({"items": []})
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(h.call_args[0][1]).query))
        self.assertEqual((q["singleEvents"], q["orderBy"]), ("true", "startTime"))
        self.assertEqual((q["timeMin"], q["timeMax"]), ("2001-09-09T01:46:40Z", "2001-09-10T05:33:20Z"))

    def test_retries_429_and_5xx_then_succeeds(self):
        evs, h = self.run_list((429, {"error": {"message": "slow down"}}), (503, {}), {"items": [item("ok")]})
        self.assertEqual([e["title"] for e in evs], ["ok"])
        self.assertEqual(h.call_count, 3)

    def test_gives_up_after_three_tries(self):
        with mock.patch.object(g, "http_request", return_value=reply(500, {})) as h:
            with self.assertRaises(c.CalendarError):
                c.list_events(1, 2)
        self.assertEqual(h.call_count, 3)

    def test_a_403_is_not_retried(self):
        with mock.patch.object(g, "http_request", return_value=reply(403, {"error": {"message": "nope"}})) as h:
            with self.assertRaises(c.CalendarError):
                c.list_events(1, 2)
        self.assertEqual(h.call_count, 1)


class Cli(Creds):
    def setUp(self):
        super().setUp()
        g.write_private(c.TOKEN, {"refresh_token": "r", "access_token": "live", "expires_at": time.time() + 600})

    def test_whoami_prints_the_calendar_id(self):
        with mock.patch.object(g, "http_request", return_value=reply(200, {"id": "me@acme.example"})), mock.patch("builtins.print") as p:
            self.assertEqual(c.main(["whoami"]), 0)
        p.assert_called_with("me@acme.example")

    def test_events_prints_one_line_per_event_with_attendee_domains(self):
        page = {"items": [item("Sync", attendees=[{"email": "a@acme.example"}, {"email": "b@other.example"}])]}
        with mock.patch.object(g, "http_request", return_value=reply(200, page)), mock.patch("builtins.print") as p:
            self.assertEqual(c.main(["events", "--since", "2026-10-01", "--until", "2026-11-01"]), 0)
        p.assert_called_with("2026-10-27T16:00:00-04:00 | 2026-10-27T16:30:00-04:00 | Sync | acme.example,other.example")

    def test_errors_exit_1_without_a_traceback(self):
        with mock.patch.object(g, "http_request", return_value=reply(403, {"error": {"message": "off"}})), \
                mock.patch.object(sys, "stderr"):
            self.assertEqual(c.main(["whoami"]), 1)


if __name__ == "__main__":
    unittest.main()
