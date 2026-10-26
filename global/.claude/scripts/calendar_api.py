#!/usr/bin/env python3
"""calendar_api.py: read-only Google Calendar for the scripts (the hours timer reads it).

Why a second token: gmail_api.py's token holds gmail.modify only. Calendar gets its OWN token file with the
calendar.readonly scope, so consenting here never touches the Gmail token. Same OAuth client (client.json), same
folder, same stdlib-only rules; the helpers come from gmail_api.py.

READ ONLY, EVER. The scope cannot write, and api() refuses any method but GET, so a bug cannot either.

  calendar_api.py auth [--no-browser]            one-time consent (loopback + PKCE); writes calendar-token.json
  calendar_api.py whoami                         the calendar's owner address (proves the token works)
  calendar_api.py events [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--calendar ID]
                                                 start | end | title | attendee domains, one event per line

Credentials (mode 600, never printed): ~/.config/afa/gmail/client.json and calendar-token.json.
AFA_GMAIL_DIR overrides the folder (tests); AFA_GMAIL_ACCOUNT is the consent screen's login hint.
"""
import argparse
import datetime as dt
import http.server
import json
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gmail_api as g  # noqa: E402

API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
TOKEN = "calendar-token.json"


class CalendarError(Exception):
    pass


# ---------------------------------------------------------------- OAuth (PKCE, loopback), a separate token file

def build_auth_url(client_id, redirect_uri, challenge, state):
    q = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent",
    }
    if g.ACCOUNT:
        q["login_hint"] = g.ACCOUNT
    return g.AUTH_URL + "?" + urllib.parse.urlencode(q)


def run_auth(open_browser=True, timeout=300):
    c = g.client_info()
    verifier, challenge = g.make_pkce()
    state = secrets.token_urlsafe(16)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q or "error" in q:
                got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    srv.timeout = 1
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    url = build_auth_url(c["client_id"], redirect, challenge, state)
    print("Open this URL and approve read-only calendar access:\n" + url, flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    deadline = time.time() + timeout
    while not got and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if not got:
        raise CalendarError("no answer from the consent page before the timeout")
    if got.get("error"):
        raise CalendarError(f"consent refused: {got['error']}")
    if got.get("state") != state:
        raise CalendarError("state mismatch; ignoring the response")
    fields = {"client_id": c["client_id"], "code": got["code"], "code_verifier": verifier,
              "grant_type": "authorization_code", "redirect_uri": redirect}
    if c.get("client_secret"):
        fields["client_secret"] = c["client_secret"]
    tok = _token_post(fields)
    if not tok.get("refresh_token"):
        raise CalendarError("Google returned no refresh token; revoke the app at myaccount.google.com/permissions and retry")
    g.write_private(TOKEN, {
        "refresh_token": tok["refresh_token"], "access_token": tok.get("access_token"),
        "expires_at": time.time() + int(tok.get("expires_in", 0)) - 60, "scope": tok.get("scope", SCOPE),
    })
    return True


def _token_post(fields):
    try:
        return g._token_post(fields)
    except g.GmailError as e:
        raise CalendarError(str(e))


def access_token():
    try:
        t = g._read_json(TOKEN)
    except g.GmailError:
        raise CalendarError("no calendar-token.json yet; run: calendar_api.py auth")
    if t.get("access_token") and t.get("expires_at", 0) > time.time():
        return t["access_token"]
    c = g.client_info()
    fields = {"client_id": c["client_id"], "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}
    if c.get("client_secret"):
        fields["client_secret"] = c["client_secret"]
    try:
        tok = _token_post(fields)
    except CalendarError as e:
        if "invalid_grant" in str(e):
            raise CalendarError("the calendar refresh token is dead (Testing-mode apps expire in 7 days); run: calendar_api.py auth")
        raise
    t["access_token"] = tok["access_token"]
    t["expires_at"] = time.time() + int(tok.get("expires_in", 0)) - 60
    g.write_private(TOKEN, t)
    return t["access_token"]


# ---------------------------------------------------------------- API (GET only)

def api(path, params=None, method="GET"):
    if method != "GET":
        raise CalendarError("refusing: this tool only reads the calendar")
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    status, raw = g.http_request("GET", url, {"Authorization": "Bearer " + access_token()})
    try:
        out = json.loads(raw) if raw else {}
    except ValueError:
        out = {}
    if status >= 400:
        err = out.get("error") if isinstance(out, dict) else None
        msg = err.get("message", "") if isinstance(err, dict) else str(err or "")
        raise CalendarError(f"Calendar API {status} on GET {path}: {msg}")
    return out


def list_events(since, until, calendar="primary", tries=3):
    """Timed events starting in [since, until) (epoch seconds), recurring ones expanded, cancelled and all-day ones dropped.
       Each event: {"start": iso, "end": iso, "title": str, "attendees": [emails], "self_declined": bool}."""
    iso = lambda ts: dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"timeMin": iso(since), "timeMax": iso(until), "singleEvents": "true", "orderBy": "startTime",
              "maxResults": 250, "fields": "nextPageToken,items(status,summary,start,end,attendees(email,self,responseStatus))"}
    out, page = [], None
    while True:
        if page:
            params["pageToken"] = page
        for attempt in range(tries):
            try:
                data = api("/calendars/" + urllib.parse.quote(calendar, safe="") + "/events", params)
                break
            except CalendarError as e:
                if attempt == tries - 1 or not any(f" {c} " in str(e) for c in (429, 500, 502, 503)):
                    raise
                time.sleep(2 ** attempt)
        for ev in data.get("items", []):
            s, e = (ev.get("start") or {}).get("dateTime"), (ev.get("end") or {}).get("dateTime")
            if ev.get("status") == "cancelled" or not s or not e:
                continue
            att = ev.get("attendees") or []
            out.append({"start": s, "end": e, "title": (ev.get("summary") or "").replace("\t", " ").replace("\n", " ").strip(),
                        "attendees": [a["email"].lower() for a in att if a.get("email")],
                        "self_declined": any(a.get("self") and a.get("responseStatus") == "declined" for a in att)})
        page = data.get("nextPageToken")
        if not page:
            return out


def _day(s):
    return dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Google Calendar, read-only.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("auth"); p.add_argument("--no-browser", action="store_true")
    sub.add_parser("whoami")
    p = sub.add_parser("events"); p.add_argument("--since"); p.add_argument("--until"); p.add_argument("--calendar", default="primary")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "auth":
            run_auth(open_browser=not a.no_browser)
            print("calendar token saved")
        elif a.cmd == "whoami":
            print(api("/calendars/primary", {"fields": "id"})["id"])
        else:
            now = time.time()
            since = _day(a.since) if a.since else now - 14 * 86400
            until = _day(a.until) if a.until else now + 7 * 86400
            for ev in list_events(since, until, a.calendar):
                doms = sorted({x.rsplit("@", 1)[-1] for x in ev["attendees"]})
                print(f"{ev['start']} | {ev['end']} | {ev['title']} | {','.join(doms)}")
    except (CalendarError, g.GmailError) as e:
        print(f"calendar_api: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
