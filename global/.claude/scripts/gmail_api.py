#!/usr/bin/env python3
"""gmail_api.py: our own Gmail API client for DRAFTS, in place of Google's hosted Gmail MCP connector.

Why it exists (all three are the connector's rules, not Gmail's):
  1. The connector's create_draft rewrites every <a href> into a www.google.com/url?q=... redirect.
  2. Since 20 Oct 2026 it refuses every message-level call (label, trash, get_message) on a draft.
  3. Google says its MCP servers reject OAuth clients not marked agentic from 11 Nov 2026.

NO SEND, EVER. The gmail.modify scope this tool holds could send mail through the API. This tool never does:
no code path reaches a send endpoint, and tests/test_gmail_api.py fails the suite if a send endpoint
appears in this file's source. api() also refuses any path containing "send". Drafts are for Jonathan to send.

Stdlib only (urllib, http.server, hashlib): no pip installs on the host.

  gmail_api.py auth [--no-browser]       one-time OAuth consent (loopback + PKCE); writes token.json
  gmail_api.py whoami                    the signed-in address (proves the token works)
  gmail_api.py labels                    label names and ids
  gmail_api.py draft-create --to A [--cc B] [--bcc C] --subject S (--html FILE | --text FILE) [--reply-to MESSAGE_ID]
                             [--inline CID=FILE ...] [--attach FILE ...]
      --to/--cc/--bcc take one comma-separated string. --reply-to sets threadId, In-Reply-To and References.
      --inline puts an image where the HTML says <img src="cid:CID">; --attach adds an ordinary attachment.
      This is the ONLY route for a draft to anyone but Jonathan: the connector rewrites every link, and
      ~/.claude/hooks/block-outbound.sh refuses connector create_draft/update_draft to anyone else.
  gmail_api.py draft-fix-links DRAFT_ID  unwrap google.com/url redirects in place, thread and headers kept
  gmail_api.py draft-raw DRAFT_ID [--full]   decoded view: ids, headers, hrefs, google.com/url count
  gmail_api.py draft-label DRAFT_ID LABEL    LABEL is a name or an id
  gmail_api.py draft-unlabel DRAFT_ID LABEL
  gmail_api.py draft-delete DRAFT_ID
  gmail_api.py drafts-list [QUERY]
  gmail_api.py messages-list QUERY [--limit N]   (read-only: MSG id | address | From | Subject | Date | labels | threadId)

Credentials (mode 600, never printed): ~/.config/afa/gmail/client.json (OAuth Desktop client),
~/.config/afa/gmail/token.json. AFA_GMAIL_DIR overrides the folder (tests). AFA_GMAIL_ACCOUNT, if set, is the consent screen's login hint.
"""

import argparse
import base64
import email
import email.policy
import email.utils
import hashlib
import html
import html.parser
import http.server
import json
import mimetypes
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from email.message import EmailMessage

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.modify"
ACCOUNT = os.environ.get("AFA_GMAIL_ACCOUNT", "")  # optional login hint; the repo is public, so no address lives here


class GmailError(Exception):
    pass


# ---------------------------------------------------------------- credentials

def cred_dir():
    return os.environ.get("AFA_GMAIL_DIR") or os.path.expanduser("~/.config/afa/gmail")


def _read_json(name):
    path = os.path.join(cred_dir(), name)
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise GmailError(f"missing {path}")


def write_private(name, data):
    d = cred_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    path = os.path.join(d, name)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def client_info():
    data = _read_json("client.json")
    c = data.get("installed") or data.get("web") or {}
    if not c.get("client_id"):
        raise GmailError("client.json has no installed.client_id (is it a Desktop client?)")
    return c


# ---------------------------------------------------------------- OAuth (PKCE, loopback)

def make_pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(client_id, redirect_uri, challenge, state):
    q = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    if ACCOUNT:
        q["login_hint"] = ACCOUNT
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def http_request(method, url, headers=None, body=None, timeout=60):
    """The only network call. Returns (status, bytes). Tests replace this."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _token_post(fields):
    status, raw = http_request(
        "POST", TOKEN_URL,
        {"Content-Type": "application/x-www-form-urlencoded"},
        urllib.parse.urlencode(fields).encode())
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    if status != 200:
        raise GmailError(f"token endpoint {status}: {data.get('error', '?')} {data.get('error_description', '')}".strip())
    return data


def run_auth(open_browser=True, timeout=300):
    c = client_info()
    verifier, challenge = make_pkce()
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
    print("Open this URL and approve access:\n" + url, flush=True)
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
        raise GmailError("no answer from the consent page before the timeout")
    if got.get("error"):
        raise GmailError(f"consent refused: {got['error']}")
    if got.get("state") != state:
        raise GmailError("state mismatch; ignoring the response")
    fields = {
        "client_id": c["client_id"], "code": got["code"], "code_verifier": verifier,
        "grant_type": "authorization_code", "redirect_uri": redirect,
    }
    if c.get("client_secret"):
        fields["client_secret"] = c["client_secret"]
    tok = _token_post(fields)
    if not tok.get("refresh_token"):
        raise GmailError("Google returned no refresh token; revoke the app at myaccount.google.com/permissions and retry")
    write_private("token.json", {
        "refresh_token": tok["refresh_token"], "access_token": tok.get("access_token"),
        "expires_at": time.time() + int(tok.get("expires_in", 0)) - 60, "scope": tok.get("scope", SCOPE),
    })
    return True


def access_token():
    t = _read_json("token.json")
    if t.get("access_token") and t.get("expires_at", 0) > time.time():
        return t["access_token"]
    c = client_info()
    fields = {"client_id": c["client_id"], "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}
    if c.get("client_secret"):
        fields["client_secret"] = c["client_secret"]
    try:
        tok = _token_post(fields)
    except GmailError as e:
        if "invalid_grant" in str(e):
            raise GmailError("the refresh token is dead (Testing-mode apps expire in 7 days); run: gmail_api.py auth")
        raise
    t["access_token"] = tok["access_token"]
    t["expires_at"] = time.time() + int(tok.get("expires_in", 0)) - 60
    write_private("token.json", t)
    return t["access_token"]


# ---------------------------------------------------------------- API

def api(method, path, params=None, body=None):
    # Belt and braces for the no-send rule (the suite also greps this file for the endpoints).
    if "send" in path.lower():
        raise GmailError("refusing: this tool never sends mail")
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    headers = {"Authorization": "Bearer " + access_token()}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    status, raw = http_request(method, url, headers, data)
    if status == 204 or not raw:
        out = {}
    else:
        try:
            out = json.loads(raw)
        except ValueError:
            out = {"_raw": raw.decode("utf-8", "replace")}
    if status >= 400:
        msg = (out.get("error") or {}).get("message", "") if isinstance(out.get("error"), dict) else str(out.get("error", ""))
        raise GmailError(f"Gmail API {status} on {method} {path}: {msg}")
    return out


def b64u_encode(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64u_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- link unwrapping

_REDIRECT_RE = re.compile(r"^https?://(?:www\.)?google\.com/url\?", re.I)
_TRAILER_RE = re.compile(r"&(?:source|ust|usg|sa|hl|rct|sntz)=")
_STRUCTURAL_ESC = re.compile(r"%(?:25|26|3D|3d|23|3F|3f)")


def unwrap_redirect(url):
    """google.com/url?q=<target>&source=gmail&ust=...&sa=E -> <target>. Anything else comes back unchanged.

    The target runs from q= to the first Google trailer key, so an unencoded & or # inside it survives;
    it is percent-decoded once only when it carries structural escapes (%26 %3D %23 %3F %25), which is how
    Gmail itself encodes it.
    """
    if not _REDIRECT_RE.match(url):
        return url
    query = url.split("?", 1)[1]
    m = re.match(r"(?:.*?&)?(?:q|url)=", query)
    if not m:
        return url
    rest = query[m.end():]
    t = _TRAILER_RE.search(rest)
    target = rest[:t.start()] if t else rest
    if _STRUCTURAL_ESC.search(target):
        target = urllib.parse.unquote(target)
    return target


_HREF_RE = re.compile(r"""(\bhref\s*=\s*)(["'])(.*?)\2""", re.I | re.S)


def unwrap_html(text):
    """Replace each google.com/url redirect href with its target; touch nothing else."""
    def sub(m):
        href = html.unescape(m.group(3))
        fixed = unwrap_redirect(href)
        if fixed == href:
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{html.escape(fixed, quote=True)}{m.group(2)}"
    return _HREF_RE.sub(sub, text)


_PLAIN_REDIRECT_RE = re.compile(r"https?://(?:www\.)?google\.com/url\?[^\s<>\"]+", re.I)


def unwrap_plain(text):
    return _PLAIN_REDIRECT_RE.sub(lambda m: unwrap_redirect(m.group(0)), text)


def hrefs(text):
    return [html.unescape(m.group(3)) for m in _HREF_RE.finditer(text)]


# ---------------------------------------------------------------- MIME

class _Strip(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.href = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            self.out.append(f" ({self.href})")
            self.href = None
        elif tag in ("p", "div", "li", "tr"):
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def html_to_text(src):
    p = _Strip()
    p.feed(src)
    text = "".join(p.out)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _guess_type(path):
    ctype, _ = mimetypes.guess_type(path)
    maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
    return maintype, subtype


def build_message(to, subject, html_body=None, text_body=None, cc=None, reply=None, bcc=None, inline=None, attach=None):
    """reply: dict with message_id, references, subject (of the message being answered).
    inline: {content-id: file path}, shown where the HTML says <img src="cid:content-id">.
    attach: [file path], ordinary attachments."""
    if html_body is None and text_body is None:
        raise GmailError("need --html or --text")
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    if reply:
        msg["In-Reply-To"] = reply["message_id"]
        msg["References"] = (reply.get("references", "") + " " + reply["message_id"]).strip()
    if html_body is not None:
        msg.set_content(text_body if text_body is not None else html_to_text(html_body))
        msg.add_alternative(html_body, subtype="html")
    else:
        msg.set_content(text_body)
    if inline:
        if html_body is None:
            raise GmailError("--inline needs --html")
        body = msg.get_body(("html",))
        for cid, path in inline.items():
            if f"cid:{cid}" not in html_body:
                raise GmailError(f"the HTML never uses cid:{cid}")
            maintype, subtype = _guess_type(path)
            with open(path, "rb") as f:
                body.add_related(f.read(), maintype, subtype, cid=f"<{cid}>", disposition="inline",
                                 filename=os.path.basename(path))
    for path in attach or []:
        maintype, subtype = _guess_type(path)
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(path))
    return msg


def reply_context(message_id):
    m = api("GET", f"/messages/{message_id}", {"format": "metadata", "metadataHeaders": ["Message-ID", "References", "Subject"]})
    h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
    if "message-id" not in h:
        raise GmailError("that message has no Message-ID header")
    return m["threadId"], {"message_id": h["message-id"], "references": h.get("references", ""), "subject": h.get("subject", "")}


# ---------------------------------------------------------------- commands

def draft_create(to, subject, html_body=None, text_body=None, cc=None, reply_to=None, bcc=None, inline=None, attach=None):
    thread, reply = None, None
    if reply_to:
        thread, reply = reply_context(reply_to)
        if not subject:
            s = reply["subject"]
            subject = s if s.lower().startswith("re:") else "Re: " + s
    msg = build_message(to, subject, html_body, text_body, cc, reply, bcc, inline, attach)
    body = {"message": {"raw": b64u_encode(msg.as_bytes())}}
    if thread:
        body["message"]["threadId"] = thread
    d = api("POST", "/drafts", body=body)
    return d


def fetch_draft_raw(draft_id):
    d = api("GET", f"/drafts/{draft_id}", {"format": "raw"})
    m = d["message"]
    return d, m, email.message_from_bytes(b64u_decode(m["raw"]), policy=email.policy.default)


def _text_parts(msg):
    return [p for p in msg.walk() if p.get_content_type() in ("text/html", "text/plain") and not p.is_multipart()]


def fix_message_links(msg):
    """Rewrite redirect links in the text parts of a parsed message. Returns the number of parts changed."""
    changed = 0
    for p in _text_parts(msg):
        old = p.get_content()
        new = unwrap_html(old) if p.get_content_type() == "text/html" else unwrap_plain(old)
        if new != old:
            p.set_content(new, subtype=p.get_content_subtype(), charset=p.get_content_charset() or "utf-8")
            changed += 1
    return changed


def draft_fix_links(draft_id):
    d, m, msg = fetch_draft_raw(draft_id)
    changed = fix_message_links(msg)
    if not changed:
        return {"id": draft_id, "changed": 0, "threadId": m.get("threadId")}
    body = {"message": {"raw": b64u_encode(msg.as_bytes())}}
    if m.get("threadId"):
        body["message"]["threadId"] = m["threadId"]
    out = api("PUT", f"/drafts/{draft_id}", body=body)
    return {"id": out["id"], "changed": changed, "threadId": out["message"].get("threadId")}


def draft_view(draft_id, full=False):
    d, m, msg = fetch_draft_raw(draft_id)
    lines = [f"draftId: {d['id']}", f"messageId: {m['id']}", f"threadId: {m.get('threadId')}", f"labels: {','.join(m.get('labelIds', []))}"]
    for h in ("To", "Cc", "Subject", "In-Reply-To", "References"):
        if msg[h]:
            lines.append(f"{h}: {msg[h]}")
    links, redirects = [], 0
    for p in _text_parts(msg):
        text = p.get_content()
        redirects += len(re.findall(r"google\.com/url\?", text))
        if p.get_content_type() == "text/html":
            links += hrefs(text)
        if full:
            lines += ["", f"--- {p.get_content_type()} ---", text.rstrip()]
    lines += ["", f"hrefs ({len(links)}):"] + [f"  {u}" for u in links]
    lines.append(f"google.com/url count: {redirects}")
    return "\n".join(lines), redirects


def resolve_label(name):
    labels = api("GET", "/labels").get("labels", [])
    for lab in labels:
        if lab["id"] == name or lab["name"].lower() == name.lower():
            return lab["id"]
    raise GmailError(f"no label {name!r}; try: gmail_api.py labels")


def draft_message_id(draft_id):
    return api("GET", f"/drafts/{draft_id}", {"format": "minimal"})["message"]["id"]


def draft_label(draft_id, label, remove=False):
    lid = resolve_label(label)
    mid = draft_message_id(draft_id)
    api("POST", f"/messages/{mid}/modify", body={"removeLabelIds": [lid]} if remove else {"addLabelIds": [lid]})
    return mid, lid


def draft_delete(draft_id):
    api("DELETE", f"/drafts/{draft_id}")


def drafts_list(query="", limit=25):
    params = {"maxResults": limit}
    if query:
        params["q"] = query
    out = []
    for d in api("GET", "/drafts", params).get("drafts", []):
        m = api("GET", f"/drafts/{d['id']}", {"format": "metadata", "metadataHeaders": ["To", "Subject"]})["message"]
        h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
        out.append((d["id"], m.get("threadId"), h.get("to", ""), h.get("subject", "")))
    return out


def _one_line(text):
    return " ".join((text or "").split())


def _api_retry(method, path, params=None, tries=4):
    """api() with a pause-and-retry on Gmail's per-minute quota (HTTP 429, or 403 'Quota exceeded'); anything else raises."""
    for n in range(tries):
        try:
            return api(method, path, params)
        except GmailError as e:
            busy = " 429 " in str(e) or "Quota exceeded" in str(e) or "rateLimitExceeded" in str(e)
            if not busy or n == tries - 1:
                raise
            time.sleep(2 ** n * 5)


def messages_list(query, limit=100):
    """Read-only. One 'MSG id | address | From | Subject | Date | labels | threadId' line per message matching the Gmail
    query, straight from the API (messages.list, then messages.get in metadata format: no body, no snippet), so a
    caller never has a model copy an ID."""
    ids, token = [], None
    while len(ids) < limit:
        params = {"q": query, "maxResults": min(500, limit - len(ids))}
        if token:
            params["pageToken"] = token
        page = _api_retry("GET", "/messages", params)
        ids += [m["id"] for m in page.get("messages", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    lines = []
    for mid in ids[:limit]:
        m = _api_retry("GET", f"/messages/{mid}", {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        h = {x["name"].lower(): _one_line(x["value"]) for x in m.get("payload", {}).get("headers", [])}
        addr = email.utils.parseaddr(h.get("from", ""))[1].lower()
        lines.append(" | ".join(["MSG " + m["id"], addr, h.get("from", ""), h.get("subject", ""), h.get("date", ""),
                                 ",".join(m.get("labelIds", [])), m.get("threadId", "")]))
    return lines


def _read_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Gmail drafts through the API (never sends).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("auth"); p.add_argument("--no-browser", action="store_true")
    sub.add_parser("whoami")
    sub.add_parser("labels")
    p = sub.add_parser("draft-create")
    p.add_argument("--to", required=True); p.add_argument("--cc"); p.add_argument("--bcc"); p.add_argument("--subject", default="")
    p.add_argument("--html"); p.add_argument("--text"); p.add_argument("--reply-to")
    p.add_argument("--inline", action="append", default=[], metavar="CID=FILE")
    p.add_argument("--attach", action="append", default=[], metavar="FILE")
    for n in ("draft-fix-links", "draft-delete"):
        sub.add_parser(n).add_argument("draft_id")
    p = sub.add_parser("draft-raw"); p.add_argument("draft_id"); p.add_argument("--full", action="store_true")
    for n in ("draft-label", "draft-unlabel"):
        p = sub.add_parser(n); p.add_argument("draft_id"); p.add_argument("label")
    p = sub.add_parser("drafts-list"); p.add_argument("query", nargs="?", default=""); p.add_argument("--limit", type=int, default=25)
    p = sub.add_parser("messages-list"); p.add_argument("query"); p.add_argument("--limit", type=int, default=100)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "auth":
            run_auth(open_browser=not a.no_browser)
            print("token saved")
        elif a.cmd == "whoami":
            print(api("GET", "/profile")["emailAddress"])
        elif a.cmd == "labels":
            for lab in sorted(api("GET", "/labels")["labels"], key=lambda x: x["name"].lower()):
                print(f"{lab['id']}\t{lab['name']}")
        elif a.cmd == "draft-create":
            if not (a.html or a.text):
                raise GmailError("need --html or --text")
            if not a.subject and not a.reply_to:
                raise GmailError("need --subject (or --reply-to to inherit one)")
            inline = {}
            for item in a.inline:
                cid, sep, path = item.partition("=")
                if not sep or not cid or not path:
                    raise GmailError(f"--inline wants CID=FILE, got {item!r}")
                inline[cid] = path
            d = draft_create(a.to, a.subject, _read_file(a.html) if a.html else None,
                             _read_file(a.text) if a.text else None, a.cc, a.reply_to, a.bcc, inline, a.attach)
            print(f"draftId: {d['id']}\nmessageId: {d['message']['id']}\nthreadId: {d['message'].get('threadId')}")
        elif a.cmd == "draft-fix-links":
            r = draft_fix_links(a.draft_id)
            print(f"draftId: {r['id']}\nparts changed: {r['changed']}\nthreadId: {r['threadId']}")
        elif a.cmd == "draft-raw":
            text, _ = draft_view(a.draft_id, a.full)
            print(text)
        elif a.cmd in ("draft-label", "draft-unlabel"):
            mid, lid = draft_label(a.draft_id, a.label, remove=a.cmd == "draft-unlabel")
            print(f"{'removed' if a.cmd == 'draft-unlabel' else 'added'} {lid} on message {mid}")
        elif a.cmd == "draft-delete":
            draft_delete(a.draft_id)
            print("deleted")
        elif a.cmd == "drafts-list":
            for did, tid, to, subj in drafts_list(a.query, a.limit):
                print(f"{did}\t{tid}\t{to}\t{subj}")
        elif a.cmd == "messages-list":
            for line in messages_list(a.query, a.limit):
                print(line)
    except GmailError as e:
        print(f"gmail_api: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
