#!/usr/bin/env python3
"""Rebuild a GitHub repo under the same name, keep every issue and pull-request number, and leave nothing forbidden behind.

Why: GitHub keeps every old pull request's snapshot (refs/pull/*/head) and serves any commit or file by its id, even after a
history rewrite, and Support will not remove them. The one way to get them off GitHub from our side is a new repo. This is
the tool for that, extended from the recreate.py that rebuilt Claude_Commandz on 10 Oct 2026. It adds pull requests,
releases, secrets, collaborators and the self-hosted runner, and it keeps the old repo (renamed, private) until the proofs pass.

  repo_rebuild.py export OWNER/REPO       copy everything to ~/backups/<repo>-rebuild-<date>/ (never a secret value)
  repo_rebuild.py prove-clean OWNER/REPO  nothing that will be pushed matches a forbidden path, blob or commit
  repo_rebuild.py swap OWNER/REPO         rename the old repo to <repo>-old-<date>, create the new private repo, push branches and tags
  repo_rebuild.py rebuild OWNER/REPO      numbers 1..N in order: issues move over, pull requests and gaps become closed placeholders
  repo_rebuild.py reattach OWNER/REPO     labels, milestones, releases, collaborators, variables, secrets, branch protection, runner
  repo_rebuild.py prove OWNER/REPO        old commit gone, old blob gone, forbidden paths 0 on every ref, numbers spot-checked
  repo_rebuild.py delete-old OWNER/REPO   delete the renamed old repo; needs --yes-delete and a passed prove in the same folder

Every phase takes --dry-run (reads only, prints the plan, changes nothing) and runs on its own. Each stops at the first surprise:
nothing is guessed and nothing is retried across a changed state. Never prints a token or a secret value.

Forbidden items are given at export and kept in <folder>/forbidden.json, so every later phase checks the same list:
  --forbid-path db-snapshot.sql.gz   a file name or path (globs allowed), matched anywhere in any pushed commit
  --forbid-blob <40-hex id>          a file's content id; the live proof needs all 40 characters (a short id gets a false 404)
  --forbid-commit <hex id>           a commit id or prefix

Secrets (re-attach): --secrets-map FILE, a JSON object the hub fills in, e.g.
  {"SMTP_PASSWORD": {"file": "~/.config/x/.env", "key": "SMTP_PASSWORD"}, "TOKEN": {"file": "/run/x"}, "K": {"env": "VAR"}}
"dependabot:NAME" keys map Dependabot secrets. A value is read inside the script, handed to `gh secret set` on stdin, never shown.

Exit 0 = done, 1 = stopped (STOP: <why>), 2 = bad usage. Tests: tests/test_repo_rebuild.py (a fake GitHub and local git folders).
"""
import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ZWSP = "​"
CHUNK = 60000  # GitHub's issue and comment body limit is 65536 characters
DEFAULT_LABELS_OWNED_BY_GITHUB = True  # a new repo comes with GitHub's default labels; the old repo's exact set replaces them


class Stop(Exception):
    """The one fatal kind: print STOP: <why> and exit 1."""


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


# ---------------------------------------------------------------- plumbing

def pace():
    return float(os.environ.get("REPO_REBUILD_PACE", "1.0"))


def git_url(repo):
    """The URL git pushes to and fetches from. Tests point REPO_REBUILD_GIT_BASE at a local folder."""
    return f"{os.environ.get('REPO_REBUILD_GIT_BASE', 'git@github.com:')}{repo}.git"


LOG_FILE = None


def log(msg):
    line = f"{datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(msg, flush=True)
    if LOG_FILE:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")


def run_gh(args, input=None):
    """The one place the gh program is started. Returns (exit code, stdout, stderr). Tests replace this."""
    p = subprocess.run(["gh", *args], capture_output=True, text=True, input=input, timeout=900)
    return p.returncode, p.stdout, p.stderr


def run_git(*args, cwd=None, ok=(0,), input=None):
    p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd, input=input)
    if p.returncode not in ok:
        raise Stop(f"git {' '.join(args[:3])} exited {p.returncode}: {p.stderr.strip()[:300]}")
    return p


def sleep(seconds):
    time.sleep(seconds)


def parse_status(err):
    m = re.search(r"HTTP (\d{3})", err or "")
    return int(m.group(1)) if m else 0


def decode_all(text):
    """gh --paginate prints one JSON document per page, back to back."""
    dec, i, out = json.JSONDecoder(), 0, []
    text = text or ""
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return out
        doc, i = dec.raw_decode(text, i)
        out.append(doc)


def api(method, path, body=None, paginate=False):
    """One GitHub REST call through gh. Waits and retries on a rate limit; reads retry on a server error; raises ApiError."""
    args = ["api", "-X", method, path]
    if paginate:
        args.append("--paginate")
    if body is not None:
        args += ["--input", "-"]
    for attempt in range(1, 13):
        rc, out, err = run_gh(args, input=json.dumps(body) if body is not None else None)
        if rc == 0:
            if method != "GET" and pace():
                sleep(pace())
            docs = decode_all(out)
            if paginate:
                return docs
            return docs[0] if docs else None
        status, low = parse_status(err), (err or "").lower()
        if status == 429 or (status == 403 and ("rate limit" in low or "abuse" in low or "secondary" in low)):
            wait = min(60 * attempt, 900)
            log(f"  rate limited on {method} {path.split('?')[0]}; waiting {wait}s (attempt {attempt})")
            sleep(wait)
            continue
        if method == "GET" and status in (500, 502, 503, 504) and attempt < 4:
            sleep(5 * attempt)
            continue
        raise ApiError(status, (err or "").strip()[:300])
    raise ApiError(429, "still rate limited after 12 waits")


def paged(path, key=None):
    """Every item of a list endpoint. key names the list inside an object answer (variables, secrets, runners)."""
    sep = "&" if "?" in path else "?"
    items = []
    for doc in api("GET", f"{path}{sep}per_page=100", paginate=True):
        items.extend(doc.get(key, []) if key and isinstance(doc, dict) else doc)
    return items


def graphql(query, variables):
    rc, out, err = run_gh(["api", "graphql", "--input", "-"], input=json.dumps({"query": query, "variables": variables}))
    if pace():
        sleep(pace())
    if rc != 0:
        raise ApiError(parse_status(err), (err or "").strip()[:300])
    doc = json.loads(out)
    if doc.get("errors"):
        raise ApiError(200, "; ".join(e.get("message", "?") for e in doc["errors"])[:300])
    return doc["data"]


def maybe(fn, *a, **kw):
    """The call's answer, or {'error': status} when GitHub says no (a missing feature, a plan limit)."""
    try:
        return fn(*a, **kw)
    except ApiError as e:
        return {"error": e.status}


def split_repo(repo):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        raise Stop(f"'{repo}' is not OWNER/REPO")
    return repo.split("/")


def today():
    return datetime.date.today().isoformat()


def load(path, default=None):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def save(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def resolve_dir(args):
    """The export folder: --dir, or for export itself ~/backups/<repo>-rebuild-<date>, or for later phases the newest one."""
    owner, name = split_repo(args.repo)
    if args.dir:
        return Path(args.dir).expanduser()
    base = Path(os.environ.get("REPO_REBUILD_BACKUPS", Path.home() / "backups"))
    if args.phase == "export":
        return base / f"{name}-rebuild-{args.date}"
    found = sorted(base.glob(f"{name}-rebuild-*"))
    if not found:
        raise Stop(f"no export folder {base}/{name}-rebuild-*; run export first or pass --dir")
    return found[-1]


def need_meta(d):
    meta = load(d / "meta.json")
    if not meta or not meta.get("complete"):
        raise Stop(f"{d} holds no finished export (meta.json); run export first")
    return meta


def user_login():
    return api("GET", "user")["login"]


def token_scopes():
    rc, out, err = run_gh(["api", "-i", "user"])
    for line in (out or "").splitlines():
        if line.lower().startswith("x-oauth-scopes:"):
            return [s.strip() for s in line.split(":", 1)[1].split(",") if s.strip()]
    return []


# ---------------------------------------------------------------- forbidden-object matching (prove-clean and prove)

def is_hex(s):
    return bool(re.fullmatch(r"[0-9a-f]{4,40}", s or ""))


def forbidden_from(args, d):
    saved = load(d / "forbidden.json", {"paths": [], "blobs": [], "commits": []})
    for key, attr in (("paths", "forbid_path"), ("blobs", "forbid_blob"), ("commits", "forbid_commit")):
        for v in getattr(args, attr, None) or []:
            v = v.lower() if key != "paths" else v
            if key != "paths" and not is_hex(v):
                raise Stop(f"--forbid-{key[:-1]} '{v}' is not a hex id")
            if v not in saved[key]:
                saved[key].append(v)
    return saved


def path_matches(path, pat):
    base = path.rsplit("/", 1)[-1]
    if any(c in pat for c in "*?["):
        return fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(base, pat)
    return path == pat or base == pat or path.endswith("/" + pat)


def scan_git(gitdir, forbid):
    """Every object reachable from any ref in a git folder, against the forbidden list. Returns (objects scanned, hits)."""
    hits, scanned = [], 0
    out = run_git("-C", str(gitdir), "rev-list", "--objects", "--all").stdout
    for line in out.splitlines():
        sha, _, path = line.partition(" ")
        scanned += 1
        for b in forbid["blobs"]:
            if sha.startswith(b):
                hits.append(f"blob {sha} ({path or 'no path'})")
        for pat in forbid["paths"]:
            if path and path_matches(path, pat):
                hits.append(f"path {path} (object {sha[:10]})")
    for sha in run_git("-C", str(gitdir), "rev-list", "--all").stdout.split():
        for c in forbid["commits"]:
            if sha.startswith(c):
                hits.append(f"commit {sha}")
    return scanned, sorted(set(hits))


def heads_digest(gitdir):
    refs = run_git("-C", str(gitdir), "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags").stdout
    return hashlib.sha256(refs.encode()).hexdigest(), dict(l.split() for l in refs.splitlines() if l)


# ---------------------------------------------------------------- text rendering (placeholders and copied comments)

CODE = re.compile(r"(```.*?```|`[^`\n]*`)", re.S)
MENTION = re.compile(r"(?<![\w`@])@([A-Za-z0-9][A-Za-z0-9-]*(?:/[A-Za-z0-9_-]+)?)")


def defang(text):
    """Break @mentions (outside code) so copied text can not notify anyone again. A zero-width space after the @."""
    if not text:
        return ""
    parts = CODE.split(text)
    return "".join(p if i % 2 else MENTION.sub(lambda m: f"@{ZWSP}{m.group(1)}", p) for i, p in enumerate(parts))


def day(ts):
    return (ts or "")[:10] or "an unknown date"


def who(user):
    return (user or {}).get("login") or "a deleted account"


def clip(text, limit=CHUNK):
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n\n_[cut here: the original was {len(text):,} characters]_"


def chunks(blocks, limit=CHUNK):
    """Pack text blocks into as few strings as fit under the limit."""
    out, cur = [], ""
    for b in blocks:
        b = clip(b, limit)
        if cur and len(cur) + len(b) + 2 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        out.append(cur)
    return out


def pr_state_line(item):
    pr = item.get("pr") or {}
    issue = item["issue"]
    if pr.get("merged_at"):
        return f"merged on {day(pr['merged_at'])}"
    if issue["state"] == "closed":
        return f"closed without merging on {day(issue.get('closed_at'))}"
    return "open"


def placeholder_title(item, n):
    if item is None:
        return f"Number {n}: nothing was here"
    t = item["issue"]["title"]
    return t[:250] if item["kind"] == "issue" else f"Pull request: {t}"[:255]


def placeholder_body(item, n, recreated):
    if item is None:
        return (f"_This number was never used on the original repo (or its issue was deleted). This closed placeholder holds the "
                f"number so every later issue keeps its original number. Rebuilt {recreated}._")
    issue, pr = item["issue"], item.get("pr") or {}
    if item["kind"] == "pull":
        head = [f"**This is a closed placeholder for pull request #{n}.** GitHub can't carry pull requests across a rebuild, so the "
                f"text of the original is kept here and \"PR {n}\" links and numbers still land on something. Nothing here can be merged.",
                f"Original: **{defang(issue['title'])}**, opened by {who(issue.get('user'))} on {day(issue['created_at'])}, {pr_state_line(item)}."]
        if pr:
            head.append(f"From `{(pr.get('head') or {}).get('ref', '?')}` into `{(pr.get('base') or {}).get('ref', '?')}`: "
                        f"{pr.get('commits', len(item.get('commits', [])))} commits, {pr.get('changed_files', '?')} files, "
                        f"+{pr.get('additions', '?')} -{pr.get('deletions', '?')} lines (a diff is not kept).")
    else:
        head = [f"_Originally opened by {who(issue.get('user'))} on {day(issue['created_at'])}; "
                f"state when rebuilt: {issue['state']}. Copied as text when the repo was rebuilt {recreated}._"]
    return "\n\n".join(head) + "\n\n---\n\n" + defang(clip(issue.get("body") or "_(no description)_", 50000))


def comment_text(c):
    return f"_{who(c.get('user'))} commented on {day(c.get('created_at'))}:_\n\n{defang(c.get('body'))}"


def review_blocks(item):
    out = []
    if item.get("reviews"):
        lines = []
        for r in sorted(item["reviews"], key=lambda r: r.get("submitted_at") or ""):
            if r.get("state") == "COMMENTED" and not (r.get("body") or "").strip():
                continue  # only a holder for inline comments, which are listed below
            line = f"- **{who(r.get('user'))}** {r.get('state', '?').lower().replace('_', ' ')} on {day(r.get('submitted_at'))}"
            if (r.get("body") or "").strip():
                line += ":\n  > " + defang(clip(r["body"], 4000)).strip().replace("\n", "\n  > ")
            lines.append(line)
        if lines:
            out.append("## Review summary\n\n" + "\n".join(lines))
    if item.get("review_comments"):
        lines = [f"- `{c.get('path', '?')}`{(':' + str(c['line'])) if c.get('line') else ''} by {who(c.get('user'))} on "
                 f"{day(c.get('created_at'))}:\n  > " + defang(clip(c.get("body"), 3000)).strip().replace("\n", "\n  > ")
                 for c in sorted(item["review_comments"], key=lambda c: c.get("created_at") or "")]
        out.append("## Inline review comments\n\n" + "\n".join(lines))
    return out


def commit_blocks(item):
    if not item.get("commits"):
        return []
    lines = [f"- `{c['sha'][:10]}` {defang((c['commit']['message'] or '').splitlines()[0][:150] if c['commit']['message'] else '')} "
             f"({c['commit']['author'].get('name', '?')}, {day(c['commit']['author'].get('date'))})" for c in item["commits"]]
    return ["## Commits\n\n" + "\n".join(lines)]


def placeholder_comments(item):
    """The comment bodies to post, in order: review summary, commit list (pull requests), then the conversation."""
    if item is None:
        return []
    out = chunks(review_blocks(item) + commit_blocks(item)) if item["kind"] == "pull" else []
    out += [clip(comment_text(c)) for c in sorted(item.get("comments", []), key=lambda c: c.get("created_at") or "")]
    return out


# ---------------------------------------------------------------- phase 1: export

def download_asset(repo, asset_id, dest):
    """Save one release asset's bytes to dest. True when gh exited 0. Tests replace this."""
    with open(dest, "wb") as f:
        p = subprocess.run(["gh", "api", "-H", "Accept: application/octet-stream", f"repos/{repo}/releases/assets/{asset_id}"],
                           stdout=f, stderr=subprocess.PIPE)
    return p.returncode == 0


def release_digest(releases):
    return sorted((r["id"], tuple(sorted(a["id"] for a in r.get("assets", [])))) for r in releases)


def fetch_activity(repo):
    """The newest update on any issue, pull request or comment, and the highest number in use."""
    newest, top = "", 0
    for path in (f"repos/{repo}/issues?state=all&sort=updated&direction=desc&per_page=1",
                 f"repos/{repo}/issues/comments?sort=updated&direction=desc&per_page=1",
                 f"repos/{repo}/pulls/comments?sort=updated&direction=desc&per_page=1"):
        got = api("GET", path)
        if got:
            newest = max(newest, got[0].get("updated_at", ""))
    got = api("GET", f"repos/{repo}/issues?state=all&sort=created&direction=desc&per_page=1")
    if got:
        top = got[0]["number"]
    return newest, top


def counts_line(c):
    return ", ".join(f"{v} {k}" for k, v in c.items())


def phase_export(args, d):
    repo = args.repo
    issues = paged(f"repos/{repo}/issues?state=all&sort=created&direction=asc")
    by_n = {i["number"]: i for i in issues}
    pulls = [n for n, i in by_n.items() if "pull_request" in i]
    comments = paged(f"repos/{repo}/issues/comments")
    review_comments = paged(f"repos/{repo}/pulls/comments")
    labels = paged(f"repos/{repo}/labels")
    milestones = paged(f"repos/{repo}/milestones?state=all")
    releases = paged(f"repos/{repo}/releases")
    collabs = maybe(paged, f"repos/{repo}/collaborators?affiliation=direct")
    invites = maybe(paged, f"repos/{repo}/invitations")
    secrets = maybe(paged, f"repos/{repo}/actions/secrets", "secrets")
    dep_secrets = maybe(paged, f"repos/{repo}/dependabot/secrets", "secrets")
    variables = maybe(paged, f"repos/{repo}/actions/variables", "variables")
    runners = maybe(paged, f"repos/{repo}/actions/runners", "runners")
    open_prs = [n for n in pulls if by_n[n]["state"] == "open"]
    c = {"issues": len(by_n) - len(pulls), "pull requests": len(pulls), "open pull requests": len(open_prs),
         "issue comments": len(comments), "review comments": len(review_comments), "labels": len(labels),
         "milestones": len(milestones), "releases": len(releases),
         "release assets": sum(len(r.get("assets", [])) for r in releases),
         "collaborators": len(collabs) if isinstance(collabs, list) else "?",
         "invitations": len(invites) if isinstance(invites, list) else "?",
         "secret names": len(secrets) if isinstance(secrets, list) else "?",
         "variables": len(variables) if isinstance(variables, list) else "?",
         "runners": len(runners) if isinstance(runners, list) else "?"}
    top = max(by_n) if by_n else 0
    log(f"{repo}: highest number {top}; {counts_line(c)}")
    if args.dry_run:
        refs = run_git("ls-remote", git_url(repo)).stdout.split("\n")
        heads = [r for r in refs if "\trefs/heads/" in r]
        tags = [r for r in refs if "\trefs/tags/" in r and not r.endswith("^{}")]
        log(f"git: {len(heads)} branches, {len(tags)} tags, {len([r for r in refs if '\trefs/pull/' in r])} pull-request refs (never copied)")
        log(f"dry run: would write {d} (mirror without refs/pull, {top} item files, release assets, settings, people, names)")
        return 0
    if (d / "meta.json").exists() and not args.refresh:
        raise Stop(f"{d} already holds an export; use --refresh to replace it, or --dir for a new folder")
    d.mkdir(parents=True, exist_ok=True)
    forbid = forbidden_from(args, d)
    if not (forbid["paths"] or forbid["blobs"] or forbid["commits"]):
        raise Stop("give at least one --forbid-path, --forbid-blob or --forbid-commit: prove-clean needs something to check")
    save(d / "forbidden.json", forbid)
    (d / "meta.json").unlink(missing_ok=True)
    global LOG_FILE
    LOG_FILE = d / "rebuild.log"

    # git: heads and tags only; the old pull-request refs are what we are leaving behind
    mirror = d / "repo.git"
    if not mirror.exists():
        run_git("init", "--bare", str(mirror))
    run_git("-C", str(mirror), "fetch", "--prune", "--force", git_url(repo), "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
    digest, heads = heads_digest(mirror)
    other = sorted({l.split("\t")[1] for l in run_git("ls-remote", git_url(repo)).stdout.splitlines()
                    if not l.split("\t")[1].startswith(("refs/heads/", "refs/tags/", "refs/pull/")) and l.split("\t")[1] != "HEAD"})
    log(f"git mirror: {len(heads)} refs" + (f"; refs not carried: {other}" if other else ""))

    # items
    (d / "items").mkdir(exist_ok=True)
    for old in (d / "items").glob("*.json"):
        old.unlink()
    grouped, rgroup = {}, {}
    for cm in comments:
        grouped.setdefault(int(cm["issue_url"].rsplit("/", 1)[1]), []).append(cm)
    for cm in review_comments:
        rgroup.setdefault(int(cm["pull_request_url"].rsplit("/", 1)[1]), []).append(cm)
    for n, issue in sorted(by_n.items()):
        item = {"kind": "pull" if n in pulls else "issue", "number": n, "issue": issue, "comments": grouped.get(n, [])}
        if item["kind"] == "pull":
            item["pr"] = api("GET", f"repos/{repo}/pulls/{n}")
            item["reviews"] = paged(f"repos/{repo}/pulls/{n}/reviews")
            item["review_comments"] = rgroup.get(n, [])
            item["commits"] = paged(f"repos/{repo}/pulls/{n}/commits")
        save(d / "items" / f"{n}.json", item)
    log(f"items: {len(by_n)} files")

    # releases and their asset files
    manifest = {}
    for r in releases:
        for a in r.get("assets", []):
            dest = d / "releases" / str(r["id"]) / a["name"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            ok_dl = download_asset(repo, a["id"], dest)
            if not ok_dl or dest.stat().st_size != a["size"]:
                raise Stop(f"release asset {r['tag_name']}/{a['name']}: wanted {a['size']} bytes, got {dest.stat().st_size if dest.exists() else 0}")
            manifest[f"{r['id']}/{a['name']}"] = hashlib.sha256(dest.read_bytes()).hexdigest()
    save(d / "release-assets.json", manifest)
    log(f"release assets: {len(manifest)} files")

    settings = api("GET", f"repos/{repo}")
    default = settings.get("default_branch", "main")
    save(d / "settings.json", settings)
    save(d / "labels.json", labels)
    save(d / "milestones.json", milestones)
    save(d / "releases.json", releases)
    save(d / "collaborators.json", collabs)
    save(d / "invitations.json", invites)
    save(d / "variables.json", variables)  # names and values; Actions variables are not secret
    save(d / "secret-names.json", {"actions": [s["name"] for s in secrets] if isinstance(secrets, list) else [],
                                   "dependabot": [s["name"] for s in dep_secrets] if isinstance(dep_secrets, list) else []})
    save(d / "runners.json", runners)
    hooks = maybe(paged, f"repos/{repo}/hooks")
    save(d / "extras.json", {
        "actions_permissions": maybe(api, "GET", f"repos/{repo}/actions/permissions"),
        "workflow_permissions": maybe(api, "GET", f"repos/{repo}/actions/permissions/workflow"),
        "hooks": [{"id": h["id"], "name": h.get("name"), "url": (h.get("config") or {}).get("url"), "events": h.get("events"),
                   "active": h.get("active")} for h in hooks] if isinstance(hooks, list) else hooks,
        "deploy_keys": maybe(paged, f"repos/{repo}/keys"),
        "environments": maybe(api, "GET", f"repos/{repo}/environments"),
        "branch_protection": maybe(api, "GET", f"repos/{repo}/branches/{default}/protection"),
        "rulesets": maybe(api, "GET", f"repos/{repo}/rulesets"),
        "pages": maybe(api, "GET", f"repos/{repo}/pages"),
    })
    newest, top_live = fetch_activity(repo)
    save(d / "meta.json", {"repo": repo, "exported_at": datetime.datetime.now().isoformat(timespec="seconds"), "date": args.date,
                           "old_name": f"{repo.split('/')[1]}-old-{args.date}", "max_number": top, "counts": c,
                           "default_branch": default, "heads": heads, "heads_digest": digest, "latest_activity": newest,
                           "releases_digest": [list(map(str, x)) for x in release_digest(releases)], "complete": True})
    log(f"export finished: {d}")
    return 0


# ---------------------------------------------------------------- phase 2: prove-clean

def phase_prove_clean(args, d):
    meta = need_meta(d)
    forbid = forbidden_from(args, d)
    if not (forbid["paths"] or forbid["blobs"] or forbid["commits"]):
        raise Stop("no forbidden list: pass --forbid-path / --forbid-blob / --forbid-commit")
    digest, heads = heads_digest(d / "repo.git")
    if digest != meta["heads_digest"]:
        raise Stop("the mirror's branches differ from the export's record; re-run export --refresh")
    scanned, hits = scan_git(d / "repo.git", forbid)
    log(f"prove-clean: {scanned} objects in {len(heads)} refs against {len(forbid['paths'])} paths, {len(forbid['blobs'])} blobs, "
        f"{len(forbid['commits'])} commits")
    if hits:
        for h in hits[:20]:
            log(f"  FOUND {h}")
        raise Stop(f"{len(hits)} forbidden object(s) are in what would be pushed; nothing was changed")
    log("prove-clean: nothing forbidden in what will be pushed")
    if not args.dry_run:
        save(d / "clean.json", {"ok": True, "heads_digest": digest, "at": datetime.datetime.now().isoformat(timespec="seconds")})
    return 0


# ---------------------------------------------------------------- secrets mapping (values never printed)

def read_secret(spec, name):
    """The value for one secret from its named local source. Error text names the secret and the source, never a value."""
    if "env" in spec:
        v = os.environ.get(spec["env"])
        if not v:
            raise Stop(f"secret {name}: environment variable {spec['env']} is empty")
        return v
    path = Path(spec.get("file", "")).expanduser()
    if not path.is_file():
        raise Stop(f"secret {name}: source file {path} does not exist")
    text = path.read_text()
    key = spec.get("key")
    if not key:
        return text.rstrip("\r\n")
    try:
        doc = json.loads(text)
        if isinstance(doc, dict):
            if key in doc and isinstance(doc[key], str) and doc[key]:
                return doc[key]
            raise Stop(f"secret {name}: key {key} is not in {path}")
    except ValueError:
        pass
    for line in text.splitlines():
        m = re.match(rf"\s*(?:export\s+)?{re.escape(key)}\s*=\s*(.*)$", line)
        if m:
            v = m.group(1).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if v:
                return v
    raise Stop(f"secret {name}: key {key} is not in {path}")


def check_secrets_map(d, mapfile):
    """Every exported secret name has a source that resolves. Returns the mapping; raises Stop naming what is missing."""
    names = load(d / "secret-names.json", {"actions": [], "dependabot": []})
    wanted = names["actions"] + [f"dependabot:{n}" for n in names["dependabot"]]
    if not wanted:
        return {}
    if not mapfile:
        raise Stop(f"{len(wanted)} secret name(s) need a source: pass --secrets-map FILE (names: {', '.join(wanted)})")
    mapping = load(Path(mapfile).expanduser())
    if not isinstance(mapping, dict):
        raise Stop(f"{mapfile} is not a JSON object")
    missing = [n for n in wanted if n not in mapping]
    if missing:
        raise Stop(f"the secrets map has no entry for: {', '.join(missing)}")
    for n in wanted:
        read_secret(mapping[n], n)  # raises if unreadable; the value is dropped at once
    return {n: mapping[n] for n in wanted}


# ---------------------------------------------------------------- phase 3: swap

SETTINGS_KEYS = ("has_issues", "has_projects", "has_wiki", "has_discussions", "allow_squash_merge", "allow_merge_commit",
                 "allow_rebase_merge", "allow_auto_merge", "delete_branch_on_merge", "allow_update_branch",
                 "web_commit_signoff_required", "is_template", "squash_merge_commit_title", "squash_merge_commit_message",
                 "merge_commit_title", "merge_commit_message")


def settings_patch(settings):
    return {k: settings[k] for k in SETTINGS_KEYS if settings.get(k) is not None}


def repo_exists(repo):
    try:
        return api("GET", f"repos/{repo}")
    except ApiError as e:
        if e.status == 404:
            return None
        raise


def preflight_fresh(repo, meta, d, allow_open_prs):
    """Nobody has pushed, commented or opened anything since the export; no open pull request would be lost."""
    _, heads = heads_digest(d / "repo.git")
    remote = {}
    for line in run_git("ls-remote", "--heads", "--tags", git_url(repo)).stdout.splitlines():
        sha, ref = line.split("\t")
        if not ref.endswith("^{}"):
            remote[ref] = sha
    local = {r: s for r, s in heads.items()}
    peeled = {}
    for line in run_git("ls-remote", "--tags", git_url(repo)).stdout.splitlines():
        sha, ref = line.split("\t")
        if ref.endswith("^{}"):
            peeled[ref[:-3]] = sha
    if remote != local:
        diff = sorted(set(remote.items()) ^ set(local.items()))[:6]
        raise Stop(f"GitHub's branches or tags moved since the export ({diff}); re-run export --refresh and prove-clean")
    newest, top = fetch_activity(repo)
    if newest > meta["latest_activity"] or top != meta["max_number"]:
        raise Stop(f"issues or comments changed since the export (activity {newest}, highest number {top}); re-run export --refresh")
    rel = [list(map(str, x)) for x in release_digest(paged(f"repos/{repo}/releases"))]
    if rel != meta["releases_digest"]:
        raise Stop("releases changed since the export (a new weekly backup?); re-run export --refresh")
    open_prs = [i["number"] for i in paged(f"repos/{repo}/pulls?state=open")]
    if open_prs and not allow_open_prs:
        raise Stop(f"{len(open_prs)} pull request(s) are open ({open_prs[:10]}); merge or close them first, they can't be carried across")


def phase_swap(args, d):
    repo = args.repo
    owner, name = split_repo(repo)
    meta = need_meta(d)
    clean = load(d / "clean.json")
    if not clean or clean.get("heads_digest") != meta["heads_digest"]:
        raise Stop("prove-clean has not passed on this export; run prove-clean first")
    scanned, hits = scan_git(d / "repo.git", forbidden_from(args, d))  # again, now: the folder may have changed since
    if hits:
        raise Stop(f"{len(hits)} forbidden object(s) in the mirror: {hits[:3]}")
    old_name = args.old_name or meta["old_name"]
    old_repo = f"{owner}/{old_name}"
    mapping = check_secrets_map(d, args.secrets_map) if not args.skip_secrets_check else {}
    old = repo_exists(repo)
    if not old:
        raise Stop(f"{repo} does not exist (a swap that already ran? check {old_repo})")
    if not old.get("private"):
        raise Stop(f"{repo} is public; this tool only rebuilds private repos")
    if repo_exists(old_repo):
        raise Stop(f"{old_repo} already exists")
    preflight_fresh(repo, meta, d, args.allow_open_prs)
    owner_type = api("GET", f"users/{owner}")["type"]
    if owner_type != "Organization" and owner != user_login():
        raise Stop(f"the token's account is not {owner}; it can not create a repo there")
    settings = load(d / "settings.json")
    log(f"swap plan: rename {repo} -> {old_repo} (stays private); create private {repo}; apply settings; disable Actions; "
        f"push {len(heads_digest(d / 'repo.git')[1])} branches and tags; secrets sources checked: {len(mapping)}")
    if args.dry_run:
        log("dry run: nothing changed")
        return 0
    api("PATCH", f"repos/{repo}", {"name": old_name})
    log(f"renamed {repo} -> {old_repo}")
    for attempt in range(10):
        try:
            body = {"name": name, "private": True, "description": settings.get("description") or "", "homepage": settings.get("homepage") or ""}
            api("POST", f"orgs/{owner}/repos" if owner_type == "Organization" else "user/repos", body)
            break
        except ApiError as e:
            if attempt == 9:
                raise Stop(f"could not create {repo} ({e}); the old repo is {old_repo}, rename it back to undo")
            sleep(3 if pace() else 0)
    log(f"created private {repo}")
    api("PATCH", f"repos/{repo}", settings_patch(settings))
    if settings.get("topics"):
        api("PUT", f"repos/{repo}/topics", {"names": settings["topics"]})
    # no push may start a workflow: the swap must not trigger a deploy. Reattach turns Actions back on.
    api("PUT", f"repos/{repo}/actions/permissions", {"enabled": False})
    default = meta["default_branch"]
    heads = heads_digest(d / "repo.git")[1]
    mirror = str(d / "repo.git")
    order = [f"refs/heads/{default}"] + [r for r in heads if r.startswith("refs/heads/") and r != f"refs/heads/{default}"]
    order += [r for r in heads if r.startswith("refs/tags/")]
    for ref in order:
        if ref in heads:
            run_git("-C", mirror, "push", git_url(repo), f"{ref}:{ref}")
    api("PATCH", f"repos/{repo}", {"default_branch": default})
    after = {}
    for line in run_git("ls-remote", "--heads", "--tags", git_url(repo)).stdout.splitlines():
        sha, ref = line.split("\t")
        if not ref.endswith("^{}"):
            after[ref] = sha
    if after != heads:
        raise Stop(f"after the push the new repo's branches differ from the mirror: {sorted(set(after.items()) ^ set(heads.items()))[:6]}")
    save(d / "swap.json", {"old_name": old_name, "old_repo": old_repo, "new_repo": repo, "pushed": len(heads),
                           "at": datetime.datetime.now().isoformat(timespec="seconds")})
    log(f"pushed {len(heads)} branches and tags; the new repo matches the mirror; Actions is off until reattach")
    return 0


# ---------------------------------------------------------------- phase 4: rebuild numbers 1..N in order

def need_swap(d):
    sw = load(d / "swap.json")
    if not sw:
        raise Stop("swap has not run on this export (no swap.json)")
    return sw


def encode(name):
    return name.replace(" ", "%20")


def ensure_labels(repo, labels, dry=False):
    have = {l["name"]: l for l in paged(f"repos/{repo}/labels")}
    want = {l["name"]: l for l in labels}
    for name in sorted(have.keys() - want.keys()):
        if not dry:
            api("DELETE", f"repos/{repo}/labels/{encode(name)}")
    for name, l in want.items():
        body = {"name": name, "color": l["color"], "description": l.get("description") or ""}
        if dry:
            continue
        if name in have:
            if (have[name]["color"], have[name].get("description") or "") != (l["color"], l.get("description") or ""):
                api("PATCH", f"repos/{repo}/labels/{encode(name)}", body)
        else:
            api("POST", f"repos/{repo}/labels", body)
    return len(want)


def ensure_milestones(repo, milestones, dry=False):
    """Create the old repo's milestones (oldest number first) and return {title: new number}."""
    have = {m["title"]: m for m in paged(f"repos/{repo}/milestones?state=all")}
    out = {}
    for m in sorted(milestones, key=lambda m: m["number"]):
        if m["title"] in have:
            out[m["title"]] = have[m["title"]]["number"]
            continue
        if dry:
            continue
        body = {"title": m["title"], "state": "open", "description": m.get("description") or ""}
        if m.get("due_on"):
            body["due_on"] = m["due_on"]
        made = api("POST", f"repos/{repo}/milestones", body)
        if m["state"] == "closed":
            api("PATCH", f"repos/{repo}/milestones/{made['number']}", {"state": "closed"})
        out[m["title"]] = made["number"]
    return out


def issue_exists(repo, n):
    """Does issue number n exist? Asked by number: a transferred issue keeps its old creation date, so "newest" can't find the top."""
    try:
        api("GET", f"repos/{repo}/issues/{n}")
        return True
    except ApiError as e:
        if e.status == 404:
            return False
        raise


def expected_title(item, n):
    return placeholder_title(item, n)


def finish_issue(new, n, item, milestones, label_names):
    """The parts of an item that follow its creation: labels and milestone, idempotent."""
    if item is None:
        return
    issue = item["issue"]
    now = api("GET", f"repos/{new}/issues/{n}")
    patch = {}
    want_labels = sorted(l["name"] for l in issue.get("labels", []) if l["name"] in label_names)
    if sorted(l["name"] for l in now.get("labels", [])) != want_labels:
        patch["labels"] = want_labels
    ms = (issue.get("milestone") or {}).get("title")
    if ms and ms in milestones and (now.get("milestone") or {}).get("number") != milestones[ms]:
        patch["milestone"] = milestones[ms]
    if patch:
        api("PATCH", f"repos/{new}/issues/{n}", patch)


def transfer(old, new_id, node_id):
    q = ("mutation($i:ID!,$r:ID!){transferIssue(input:{issueId:$i,repositoryId:$r,createLabelsIfMissing:true})"
         "{issue{number title createdAt author{login}}}}")
    return graphql(q, {"i": node_id, "r": new_id})["transferIssue"]["issue"]


def build_placeholder(new, n, item, milestones, label_names, recreated, resume):
    """Create (or resume) one placeholder: issue, then every comment, then close. Resuming counts the comments already there."""
    if not resume:
        body = {"title": placeholder_title(item, n), "body": placeholder_body(item, n, recreated)}
        if item:
            body["labels"] = [l["name"] for l in item["issue"].get("labels", []) if l["name"] in label_names]
            ms = (item["issue"].get("milestone") or {}).get("title")
            if ms in milestones:
                body["milestone"] = milestones[ms]
        made = api("POST", f"repos/{new}/issues", body)
        if made["number"] != n:
            raise Stop(f"placeholder for #{n} came back as #{made['number']}; stopped. The new repo holds items up to #{made['number']}")
        posted = 0
    else:
        posted = len(paged(f"repos/{new}/issues/{n}/comments"))
    texts = placeholder_comments(item)
    for text in texts[posted:]:
        api("POST", f"repos/{new}/issues/{n}/comments", {"body": text})
    closing = item is None or item["kind"] == "pull" or item["issue"]["state"] == "closed"
    if closing:
        reason = "completed" if item and item["kind"] == "pull" and (item.get("pr") or {}).get("merged_at") else \
            ("completed" if item and item["kind"] == "issue" and item["issue"].get("state_reason") != "not_planned" else "not_planned")
        api("PATCH", f"repos/{new}/issues/{n}", {"state": "closed", "state_reason": reason})
    if item is not None:
        finish_issue(new, n, item, milestones, label_names)
    return len(texts)


def phase_rebuild(args, d):
    repo = args.repo
    meta, sw = need_meta(d), need_swap(d)
    labels, milestones_x = load(d / "labels.json"), load(d / "milestones.json")
    top = meta["max_number"]
    state = load(d / "rebuild-state.json", {"done": 0})
    if not repo_exists(sw["old_repo"]):
        raise Stop(f"{sw['old_repo']} is gone; issues can't be transferred from it")
    if args.dry_run:
        kinds = {"issue": 0, "pull": 0, "gap": 0}
        for n in range(1, top + 1):
            p = d / "items" / f"{n}.json"
            kinds["gap" if not p.exists() else load(p)["kind"]] += 1
        log(f"dry run: would rebuild #{state['done'] + 1}..#{top}: {kinds['issue']} issues "
            f"({'text copies' if args.no_transfer else 'transferred'}), {kinds['pull']} pull-request placeholders, {kinds['gap']} gap placeholders; "
            f"{len(labels)} labels and {len(milestones_x)} milestones first")
        return 0
    global LOG_FILE
    LOG_FILE = d / "rebuild.log"
    n_labels = ensure_labels(repo, labels)
    milestones = ensure_milestones(repo, milestones_x)
    label_names = {l["name"] for l in labels}
    log(f"labels {n_labels}, milestones {len(milestones)} in place")
    new_id = api("GET", f"repos/{repo}")["node_id"]
    recreated = f"{today()}"
    if state["done"] and not issue_exists(repo, state["done"]):
        raise Stop(f"the state file says #{state['done']} is done but the new repo has no #{state['done']}; check the repo by hand")
    if issue_exists(repo, state["done"] + 2):
        raise Stop(f"the new repo is more than one item ahead of the state file (#{state['done'] + 2} exists); check the repo by hand")
    started = time.time()
    for n in range(state["done"] + 1, top + 1):
        p = d / "items" / f"{n}.json"
        item = load(p) if p.exists() else None
        resume = issue_exists(repo, n)  # made by an earlier run that stopped before it wrote its state
        if item is not None and item["kind"] == "issue" and not args.no_transfer:
            if resume:
                pass
            else:
                moved = transfer(sw["old_repo"], new_id, item["issue"]["node_id"])
                if moved["number"] != n:
                    raise Stop(f"issue #{n} came back as #{moved['number']}; stopped. The new repo holds items up to #{moved['number']}")
            now = api("GET", f"repos/{repo}/issues/{n}")
            if now["title"] != item["issue"]["title"] or who(now.get("user")) != who(item["issue"].get("user")) or \
                    now["created_at"] != item["issue"]["created_at"]:
                raise Stop(f"#{n} is not the original issue (title, author or date differ); stopped")
            finish_issue(repo, n, item, milestones, label_names)
            how = "transferred"
        else:
            nc = build_placeholder(repo, n, item, milestones, label_names, recreated, resume)
            how = "gap placeholder" if item is None else (f"{'pull request' if item['kind'] == 'pull' else 'issue'} text copy, {nc} comments")
        if not issue_exists(repo, n) or issue_exists(repo, n + 1):
            raise Stop(f"after #{n} the new repo's numbering is not #1..#{n}; stopped")
        state["done"] = n
        save(d / "rebuild-state.json", state)
        log(f"  #{n} ok ({how})")
    log(f"rebuild finished: #1..#{top} in order, {time.time() - started:.0f}s")
    return 0


# ---------------------------------------------------------------- phase 5: re-attach

PERMISSION = {"read": "pull", "pull": "pull", "triage": "triage", "write": "push", "push": "push", "maintain": "maintain", "admin": "admin"}


def collaborator_permission(c):
    role = c.get("role_name") or next((k for k in ("admin", "maintain", "push", "triage", "pull") if (c.get("permissions") or {}).get(k)), None)
    return PERMISSION.get(role)


def protection_to_put(p):
    """GitHub answers a protection rule in one shape and wants it back in another."""
    if not isinstance(p, dict) or "error" in p:
        return None
    rs = p.get("required_status_checks")
    rr = p.get("required_pull_request_reviews")
    rest = p.get("restrictions")
    flag = lambda k: (p.get(k) or {}).get("enabled", False)
    body = {
        "required_status_checks": {"strict": rs.get("strict", False), "contexts": rs.get("contexts", [])} if rs else None,
        "enforce_admins": flag("enforce_admins"),
        "required_pull_request_reviews": {k: rr[k] for k in ("dismiss_stale_reviews", "require_code_owner_reviews",
                                                             "required_approving_review_count", "require_last_push_approval") if k in rr} if rr else None,
        "restrictions": {"users": [u["login"] for u in rest.get("users", [])], "teams": [t["slug"] for t in rest.get("teams", [])],
                         "apps": [a["slug"] for a in rest.get("apps", [])]} if rest else None,
        "required_linear_history": flag("required_linear_history"),
        "allow_force_pushes": flag("allow_force_pushes"),
        "allow_deletions": flag("allow_deletions"),
        "required_conversation_resolution": flag("required_conversation_resolution"),
        "block_creations": flag("block_creations"),
        "lock_branch": flag("lock_branch"),
    }
    return body


def apply_releases(repo, d, default, skip_patterns, dry):
    releases = sorted(load(d / "releases.json"), key=lambda r: r["id"])
    have = {r["tag_name"] for r in paged(f"repos/{repo}/releases")}
    made = uploaded = skipped = 0
    for r in releases:
        tag = r["tag_name"]
        if tag in have:
            continue
        note = f"\n\n_Originally published {day(r.get('published_at') or r.get('created_at'))}; re-created when the repo was rebuilt._"
        skip = lambda a: any(fnmatch.fnmatch(f"{tag}/{a['name']}", pat) or fnmatch.fnmatch(a["name"], pat) for pat in skip_patterns)
        if dry:
            made += 1
            uploaded += sum(1 for a in r.get("assets", []) if not skip(a))
            skipped += sum(1 for a in r.get("assets", []) if skip(a))
            continue
        body = {"tag_name": tag, "target_commitish": default, "name": r.get("name") or tag, "body": (r.get("body") or "") + note,
                "draft": bool(r.get("draft")), "prerelease": bool(r.get("prerelease")), "make_latest": "false"}
        api("POST", f"repos/{repo}/releases", body)
        made += 1
        for a in r.get("assets", []):
            if skip(a):
                skipped += 1
                continue
            f = d / "releases" / str(r["id"]) / a["name"]
            rc, out, err = run_gh(["release", "upload", tag, str(f), "--repo", repo, "--clobber"])
            if rc != 0:
                raise Stop(f"upload of {tag}/{a['name']} failed: {err.strip()[:200]}")
            uploaded += 1
    return made, uploaded, skipped


def listener_pids(rdir):
    out = []
    for p in Path("/proc").glob("[0-9]*"):
        try:
            if p.joinpath("comm").read_text().strip() == "Runner.Listener" and os.readlink(p / "cwd") == str(rdir):
                out.append(int(p.name))
        except OSError:
            continue
    return out


def reattach_runner(repo, d, dry):
    rdir = Path.home() / f"actions-runner-{repo.split('/')[1].lower()}"
    if not (rdir / "config.sh").exists():
        return f"runner: no folder {rdir} on this machine; nothing to re-register"
    meta_f = rdir / ".runner"
    agent = json.loads(meta_f.read_text().lstrip("﻿")).get("agentName") if meta_f.exists() else None
    runners = load(d / "runners.json", [])
    mine = next((r for r in runners if r.get("name") == agent), None) if isinstance(runners, list) else None
    labels = [l["name"] for l in (mine or {}).get("labels", []) if l.get("type") == "custom"] or ["workstation"]
    if not agent:
        return f"runner: {rdir} has no .runner file; skipped"
    if dry:
        return f"runner: would stop the listener in {rdir}, remove its old registration, register '{agent}' with labels {','.join(labels)} to {repo}, start it"
    for pid in listener_pids(rdir):
        os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not listener_pids(rdir):
            break
        sleep(1)
    subprocess.run(["./config.sh", "remove", "--local"], cwd=rdir, capture_output=True)
    tok = api("POST", f"repos/{repo}/actions/runners/registration-token")["token"]
    p = subprocess.run(["./config.sh", "--unattended", "--url", f"https://github.com/{repo}", "--token", tok, "--name", agent,
                        "--labels", ",".join(labels), "--replace"], cwd=rdir, capture_output=True, text=True)
    if p.returncode != 0:
        raise Stop(f"runner registration failed (exit {p.returncode}); the folder is {rdir}")
    with open(rdir / "runner-stdout.log", "a") as lf:
        subprocess.Popen(["./run.sh"], cwd=rdir, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(30):
        sleep(2 if pace() else 0)
        now = maybe(paged, f"repos/{repo}/actions/runners", "runners")
        if isinstance(now, list) and any(r["name"] == agent and r["status"] == "online" for r in now):
            return f"runner: '{agent}' registered to {repo} and online"
        if not pace():
            break
    return f"runner: '{agent}' registered to {repo}; not online yet (the runner watch will start it)"


def phase_reattach(args, d):
    repo = args.repo
    meta, sw = need_meta(d), need_swap(d)
    if meta["max_number"] and not issue_exists(repo, meta["max_number"]):
        raise Stop(f"rebuild has not finished: the new repo has no #{meta['max_number']}")
    dry = args.dry_run
    global LOG_FILE
    LOG_FILE = None if dry else d / "rebuild.log"
    mapping = check_secrets_map(d, args.secrets_map) if not args.skip_secrets_check else {}
    extras = load(d / "extras.json", {})
    out = []
    out.append(f"labels: {ensure_labels(repo, load(d / 'labels.json'), dry)}; milestones: {len(ensure_milestones(repo, load(d / 'milestones.json'), dry))}")
    made, up, skipped = apply_releases(repo, d, meta["default_branch"], args.skip_assets or [], dry)
    out.append(f"releases: {made} created, {up} asset files {"to upload" if dry else "uploaded"}, {skipped} assets left out on request")
    # collaborators and pending invitations, same permission
    owner = repo.split("/")[0]
    todo = []
    collabs, invites = load(d / "collaborators.json", []), load(d / "invitations.json", [])
    for c in collabs if isinstance(collabs, list) else []:
        if c["login"] != owner:
            todo.append((c["login"], collaborator_permission(c)))
    for i in invites if isinstance(invites, list) else []:
        todo.append((i["invitee"]["login"], PERMISSION.get(i.get("permissions"))))
    for login, perm in todo:
        if not perm:
            raise Stop(f"collaborator {login} has a role this tool can't map; add them by hand")
        if not dry:
            api("PUT", f"repos/{repo}/collaborators/{login}", {"permission": perm})
    out.append(f"collaborators: {len(todo)} invited ({', '.join(f'{l}={p}' for l, p in todo)}); GitHub emails each invitee")
    variables = load(d / "variables.json", [])
    for v in variables if isinstance(variables, list) else []:
        if not dry:
            try:
                api("POST", f"repos/{repo}/actions/variables", {"name": v["name"], "value": v["value"]})
            except ApiError as e:
                if e.status != 409 and e.status != 422:
                    raise
                api("PATCH", f"repos/{repo}/actions/variables/{v['name']}", {"name": v["name"], "value": v["value"]})
    out.append(f"variables: {len(variables) if isinstance(variables, list) else 0} ({', '.join(v['name'] for v in variables) if isinstance(variables, list) else ''})")
    for name, spec in mapping.items():
        if dry:
            continue
        value = read_secret(spec, name)
        cmd = ["secret", "set", name.split(":")[-1], "--repo", repo] + (["--app", "dependabot"] if name.startswith("dependabot:") else [])
        rc, o, err = run_gh(cmd, input=value)
        del value
        if rc != 0:
            raise Stop(f"gh secret set {name} failed: {err.strip()[:200].replace(chr(10), ' ')}")
    out.append(f"secrets: {len(mapping)} set from local sources ({', '.join(mapping)}); values not shown")
    # Actions back on, as it was
    ap = extras.get("actions_permissions") or {}
    if not dry and isinstance(ap, dict) and "error" not in ap:
        body = {"enabled": ap.get("enabled", True)}
        if body["enabled"] and ap.get("allowed_actions"):
            body["allowed_actions"] = ap["allowed_actions"]
        api("PUT", f"repos/{repo}/actions/permissions", body)
    wp = extras.get("workflow_permissions") or {}
    if not dry and isinstance(wp, dict) and "error" not in wp:
        api("PUT", f"repos/{repo}/actions/permissions/workflow", {k: wp[k] for k in ("default_workflow_permissions", "can_approve_pull_request_reviews") if k in wp})
    out.append(f"actions: back {'on' if (ap or {}).get('enabled', True) else 'off'} as it was")
    keys = extras.get("deploy_keys")
    for k in keys if isinstance(keys, list) else []:
        if not dry:
            api("POST", f"repos/{repo}/keys", {"title": k["title"], "key": k["key"], "read_only": k.get("read_only", True)})
    body = protection_to_put(extras.get("branch_protection"))
    if body:
        if not dry:
            api("PUT", f"repos/{repo}/branches/{meta['default_branch']}/protection", body)
        out.append(f"branch protection: re-applied on {meta['default_branch']}")
    not_carried = [k for k in ("hooks", "environments", "rulesets", "pages") if isinstance(extras.get(k), (list, dict)) and extras.get(k)
                   and not (isinstance(extras[k], dict) and ("error" in extras[k] or extras[k].get("total_count") == 0))]
    if not_carried:
        out.append(f"NOT carried (set by hand): {', '.join(not_carried)}")
    out.append(reattach_runner(repo, d, dry))
    for line in out:
        log(("dry run: " if dry else "") + line)
    return 0


# ---------------------------------------------------------------- phase 6: prove

def gone(path):
    """True when GitHub answers 404 or 422 for this object (served = False). Returns (gone, status)."""
    try:
        api("GET", path)
        return False, 200
    except ApiError as e:
        if e.status in (404, 422):
            return True, e.status
        raise


def phase_prove(args, d):
    repo = args.repo
    meta = need_meta(d)
    sw = need_swap(d)
    forbid = forbidden_from(args, d)
    problems, ok = [], []
    old_exists = bool(repo_exists(sw["old_repo"]))
    for c in forbid["commits"]:
        g, st = gone(f"repos/{repo}/commits/{c}")
        (ok if g else problems).append(f"old commit {c[:10]} -> {st}")
        if old_exists and not gone(f"repos/{sw['old_repo']}/commits/{c}")[0]:
            ok.append(f"  control: the old repo still serves {c[:10]}, so the check can see it")
        elif old_exists:
            problems.append(f"control failed: even the old repo does not serve commit {c[:10]} (a short id? a wrong id?)")
    for b in forbid["blobs"]:
        if len(b) != 40:
            problems.append(f"blob {b} is a short id; the live check needs all 40 characters (GitHub answers 404 to a short blob id)")
            continue
        g, st = gone(f"repos/{repo}/git/blobs/{b}")
        (ok if g else problems).append(f"old blob {b[:10]} -> {st}")
        if old_exists and not gone(f"repos/{sw['old_repo']}/git/blobs/{b}")[0]:
            ok.append(f"  control: the old repo still serves {b[:10]}, so the check can see it")
        elif old_exists:
            problems.append(f"control failed: even the old repo does not serve blob {b[:10]}")
    # every ref the new repo has, scanned the way prove-clean scans the mirror
    tmp = Path(tempfile.mkdtemp(prefix="repo-rebuild-prove-"))
    try:
        run_git("init", "--bare", str(tmp))
        run_git("-C", str(tmp), "fetch", "--force", git_url(repo), "+refs/*:refs/live/*")
        refs = run_git("-C", str(tmp), "for-each-ref", "--format=%(refname)").stdout.split()
        pull_refs = [r for r in refs if r.startswith("refs/live/pull/")]
        scanned, hits = scan_git(tmp, forbid)
        (ok if not hits else problems).append(f"forbidden objects across {len(refs)} refs ({scanned} objects): {len(hits)}")
        problems.extend(f"  FOUND {h}" for h in hits[:10])
        if pull_refs:
            problems.append(f"{len(pull_refs)} pull-request refs exist on the new repo")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # numbers
    top = meta["max_number"]
    total = len(paged(f"repos/{repo}/issues?state=all"))
    (ok if total == top else problems).append(f"issues on the new repo: {total} (wanted {top})")
    nums = sorted({1, top, *random.sample(range(1, top + 1), min(top, args.spot_checks))}) if top else []
    bad = 0
    for n in nums:
        p = d / "items" / f"{n}.json"
        item = load(p) if p.exists() else None
        try:
            now = api("GET", f"repos/{repo}/issues/{n}")
        except ApiError:
            problems.append(f"#{n} is missing on the new repo")
            bad += 1
            continue
        if item and item["kind"] == "issue" and not args.no_transfer:
            same = now["title"] == item["issue"]["title"] and who(now.get("user")) == who(item["issue"].get("user")) \
                and now["created_at"] == item["issue"]["created_at"]
        else:
            same = now["title"] == placeholder_title(item, n)
        if not same:
            problems.append(f"#{n} does not match the original ('{now['title'][:50]}')")
            bad += 1
    (ok if not bad else problems).append(f"spot-checked numbers {nums}: {len(nums) - bad} of {len(nums)} match")
    for line in ok:
        log(("     " if line.startswith("  ") else "PASS ") + line)
    for line in problems:
        log(("     " if line.startswith("  ") else "FAIL ") + line)
    if problems:
        raise Stop(f"prove failed on {len([p for p in problems if not p.startswith('  ')])} check(s); the old repo was not touched")
    if not args.dry_run:
        save(d / "proved.json", {"ok": True, "repo": repo, "old_repo": sw["old_repo"], "at": datetime.datetime.now().isoformat(timespec="seconds"),
                                 "swap_at": sw["at"]})
    log("prove: every check passed")
    return 0


# ---------------------------------------------------------------- phase 7: delete the old repo

def phase_delete_old(args, d):
    sw = need_swap(d)
    pr = load(d / "proved.json")
    old = sw["old_repo"]
    if not pr or not pr.get("ok") or pr.get("old_repo") != old or pr.get("swap_at") != sw["at"] or pr.get("repo") != args.repo:
        raise Stop("prove has not passed for this swap in this folder; run prove first")
    info = repo_exists(old)
    if not info:
        log(f"{old} is already gone")
        return 0
    if "-old-" not in old or old == args.repo or not info.get("private"):
        raise Stop(f"refusing to delete {old}: it must be the renamed private old copy")
    log(f"delete-old plan: delete {old} ({info.get('size', '?')} KB, {info.get('open_issues_count', '?')} open issues there); the new repo and the export folder stay")
    if args.dry_run:
        log("dry run: nothing deleted")
        return 0
    if not args.yes_delete:
        raise Stop(f"add --yes-delete to delete {old}; this can't be undone")
    if "delete_repo" not in token_scopes():
        raise Stop("the gh token lacks the delete_repo scope. Run: gh auth refresh -h github.com -s delete_repo "
                   "(a device code; or node ~/.claude/scripts/gh_sudo.js https://github.com/login/device in the fleet window)")
    rc, out, err = run_gh(["repo", "delete", old, "--yes"])
    if rc != 0:
        raise Stop(f"gh repo delete failed: {err.strip()[:200]}")
    save(d / "deleted.json", {"repo": old, "at": datetime.datetime.now().isoformat(timespec="seconds")})
    log(f"deleted {old}")
    return 0


# ---------------------------------------------------------------- command line

PHASES = {"export": phase_export, "prove-clean": phase_prove_clean, "swap": phase_swap, "rebuild": phase_rebuild,
          "reattach": phase_reattach, "prove": phase_prove, "delete-old": phase_delete_old}


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=PHASES)
    ap.add_argument("repo", help="OWNER/REPO (the name the repo has now and keeps)")
    ap.add_argument("--dry-run", action="store_true", help="read only: print the plan and the counts, change nothing")
    ap.add_argument("--dir", help="the export folder (default ~/backups/<repo>-rebuild-<date>; later phases use the newest)")
    ap.add_argument("--date", default=today(), help="date in the folder and old-repo names (default today)")
    ap.add_argument("--forbid-path", action="append", help="export, prove-clean, prove: a forbidden file name or path")
    ap.add_argument("--forbid-blob", action="append", help="a forbidden file content id (40 hex for the live proof)")
    ap.add_argument("--forbid-commit", action="append", help="a forbidden commit id or prefix")
    ap.add_argument("--refresh", action="store_true", help="export: replace an existing export in the folder")
    ap.add_argument("--old-name", help="swap: name for the old repo (default <repo>-old-<date>)")
    ap.add_argument("--allow-open-prs", action="store_true", help="swap: go ahead although pull requests are open (they are lost)")
    ap.add_argument("--secrets-map", help="swap, reattach: JSON file naming where each secret's value is read")
    ap.add_argument("--skip-secrets-check", action="store_true", help="swap, reattach: carry no secrets (names are listed, not set)")
    ap.add_argument("--no-transfer", action="store_true", help="rebuild, prove: copy issues as text instead of transferring them")
    ap.add_argument("--skip-assets", action="append", help="reattach: release asset pattern (tag/name or name) not to upload")
    ap.add_argument("--spot-checks", type=int, default=8, help="prove: how many random numbers to compare, besides first and last")
    ap.add_argument("--yes-delete", action="store_true", help="delete-old: really delete")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        d = resolve_dir(args)
        global LOG_FILE
        LOG_FILE = d / "rebuild.log" if d.exists() and not args.dry_run else None
        return PHASES[args.phase](args, d)
    except Stop as e:
        print(f"STOP: {e}", file=sys.stderr)
        return 1
    except ApiError as e:
        print(f"STOP: GitHub said {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
