"""repo_rebuild.py: the text helpers, the forbidden-object scan on real git folders, the secrets mapping, and every phase end to end
against a fake GitHub (run_gh is replaced; the repos' git side is real, in local bare folders). Nothing here touches the network,
the real gh login or the real ~/backups."""

import io
import json
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import repo_rebuild as rb  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def git(*args, cwd=None):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def make_remote(base, full, files_by_branch):
    """A bare repo at <base>/<full>.git with the given branches; returns {branch: tip}."""
    bare = Path(base) / f"{full}.git"
    work = Path(str(bare) + "-work")
    bare.parent.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "--bare", str(bare))
    git("init", "-q", "-b", "main", str(work))
    tips = {}
    for branch, files in files_by_branch.items():
        git("checkout", "-q", "-B", branch, *(["main"] if branch != "main" and tips else []), cwd=work)
        for name, text in files.items():
            (work / name).parent.mkdir(parents=True, exist_ok=True)
            (work / name).write_text(text)
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", f"work on {branch}", cwd=work)
        git("push", "-q", str(bare), f"{branch}:{branch}", cwd=work)
        tips[branch] = git("rev-parse", "HEAD", cwd=work)
    return tips


class FakeGitHub:
    """Just enough of GitHub's REST and GraphQL for repo_rebuild.py: repos, issues, comments, pulls, labels, milestones, releases."""

    def __init__(self, base, login="owner"):
        self.base, self.login = Path(base), login
        self.repos, self.calls, self.secrets_set, self.log = {}, [], [], []
        self.scopes = "repo, workflow"
        self.transfer_skew = 0
        self.fail_after_creates = None
        self.creates = 0
        self.rate_limit_next = 0
        self.deleted = []
        self.redirects = {}
        self.create_private = True
        self.fail_comment_after = None
        self.comment_posts = 0

    def add_repo(self, full, private=True, **kw):
        r = {"full": full, "name": full.split("/")[1], "private": private, "id": len(self.repos) + 1, "issues": {}, "next": 1,
             "comments": [], "review_comments": [], "pulls": {}, "reviews": {}, "commits": {}, "labels": [], "milestones": [], "releases": [],
             "variables": [], "collabs": [], "invites": [], "runners": [], "secrets": [], "actions_enabled": True, "settings": {"description": "d", "has_issues": True},
             "default_branch": "main", "objects": set(), "keys": [], **kw}
        self.repos[full] = r
        return r

    def issue(self, full, title, number=None, user="alice", body="b", state="open", pull=None, labels=(), comments=(), when="2026-01-0"):
        r = self.repos[full]
        n = number or r["next"]
        r["next"] = max(r["next"], n + 1)
        i = {"number": n, "title": title, "body": body, "state": state, "state_reason": "completed" if state == "closed" else None,
             "user": {"login": user}, "created_at": f"{when}{n % 9 + 1}T10:00:00Z", "updated_at": f"{when}{n % 9 + 1}T11:00:00Z",
             "closed_at": f"{when}{n % 9 + 1}T12:00:00Z" if state == "closed" else None, "labels": [{"name": l} for l in labels],
             "milestone": None, "node_id": f"I_{full}_{n}", "comments": len(comments)}
        if pull is not None:
            i["pull_request"] = {"url": "x"}
            r["pulls"][n] = {"number": n, "merged_at": pull.get("merged_at"), "head": {"ref": "feat"}, "base": {"ref": "main"}, "commits": 1,
                             "changed_files": 1, "additions": 2, "deletions": 1}
            r["reviews"][n] = pull.get("reviews", [])
            r["commits"][n] = [{"sha": "ab" * 20, "commit": {"message": "do it\n\nlong", "author": {"name": "Al", "date": "2026-01-01T00:00:00Z"}}}]
        r["issues"][n] = i
        for k, (who, text) in enumerate(comments):
            r["comments"].append({"id": n * 100 + k, "issue_url": f"https://api.github.com/repos/{full}/issues/{n}", "user": {"login": who},
                                  "body": text, "created_at": f"2026-01-0{k + 1}T09:00:00Z", "updated_at": f"2026-01-0{k + 1}T09:00:00Z"})
        return i

    # -- the gh entry point
    def __call__(self, args, input=None):
        self.calls.append((list(args), input))
        if args[:2] == ["secret", "set"]:
            repo = args[args.index("--repo") + 1]
            self.secrets_set.append((repo, args[2], input))
            self.repos[repo]["secrets"].append(args[2])
            return 0, "", ""
        if args[:2] == ["release", "upload"]:
            repo = args[args.index("--repo") + 1]
            rel = next(x for x in self.repos[repo]["releases"] if x["tag_name"] == args[2])
            rel["assets"].append({"id": 900 + len(rel["assets"]), "name": Path(args[3]).name, "size": Path(args[3]).stat().st_size})
            return 0, "", ""
        if args[:2] == ["repo", "delete"]:
            self.deleted.append(args[2])
            self.repos.pop(args[2])
            return 0, "", ""
        assert args[0] == "api", args
        if args[1] == "graphql":
            return self.graphql(json.loads(input))
        if args[1] == "-i":
            return 0, f"HTTP/2.0 200 OK\nX-Oauth-Scopes: {self.scopes}\n\n{{}}", ""
        method, path = args[2], args[3]
        body = json.loads(input) if input else None
        if self.rate_limit_next:
            self.rate_limit_next -= 1
            return 1, "", "gh: You have exceeded a secondary rate limit (HTTP 403)"
        return self.route(method, path, body)

    def err(self, status, msg="Not Found"):
        return 1, "", f"gh: {msg} (HTTP {status})"

    def ok(self, doc):
        return 0, json.dumps(doc), ""

    def find(self, full):
        if full in self.repos:
            return self.repos[full]
        return self.repos.get(self.redirects.get(full))  # GitHub follows a renamed repo's old name

    def route(self, method, path, body):
        q = {}
        if "?" in path:
            path, qs = path.split("?", 1)
            q = dict(kv.split("=", 1) for kv in qs.split("&"))
        m = re.fullmatch(r"repos/([^/]+/[^/]+)(?:/(.*))?", path)
        if path == "user":
            return self.ok({"login": self.login})
        if path.startswith("users/"):
            return self.ok({"type": "User"})
        if path == "user/repos":
            full = f"{self.login}/{body['name']}"
            self.redirects.pop(full, None)
            r = self.add_repo(full, private=body["private"])
            r["settings"]["description"] = body.get("description", "")
            bare = self.base / f"{full}.git"
            bare.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
            return self.ok({"name": r["name"], "private": self.create_private})
        if not m:
            return self.err(404)
        full, rest = m.group(1), m.group(2) or ""
        r = self.find(full)
        if r is None:
            return self.err(404)
        if rest == "":
            if method == "GET":
                return self.ok({"name": r["name"], "full_name": full, "private": r["private"], "node_id": f"R_{r['id']}", "full_name": r["full"], "default_branch": r["default_branch"],
                                "size": 1, "open_issues_count": 0, "topics": [], **r["settings"]})
            if "name" in body:
                new = f"{full.split('/')[0]}/{body['name']}"
                old_bare, new_bare = self.base / f"{full}.git", self.base / f"{new}.git"
                old_bare.rename(new_bare)
                self.repos[new] = self.repos.pop(full)
                self.redirects[full] = new
                r["name"], r["full"] = body["name"], new
                return self.ok({})
            if "default_branch" in body:
                r["default_branch"] = body["default_branch"]
            r["settings"].update({k: v for k, v in body.items() if k != "default_branch"})
            return self.ok({})
        mm = re.fullmatch(r"(commits|git/blobs)/([0-9a-f]+)", rest)
        if mm:
            return self.ok({"sha": mm.group(2)}) if mm.group(2) in r["objects"] else self.err(422 if mm.group(1) == "commits" else 404)
        if rest == "keys":
            if method == "POST":
                if any(body["key"] in x["key"] for rr in self.repos.values() for x in rr["keys"]):
                    return self.err(422, "key is already in use")
                r["keys"].append(dict(body))
                return self.ok({})
            return self.ok(r["keys"])
        if rest == "topics":
            return self.ok({})
        if rest == "actions/permissions":
            if method == "PUT":
                r["actions_enabled"] = body["enabled"]
                return self.ok(None)
            return self.ok({"enabled": r["actions_enabled"], "allowed_actions": "all"})
        if rest == "actions/permissions/workflow":
            return self.ok({"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False})
        if rest == "issues" and method == "GET":
            items = [i for i in r["issues"].values() if q.get("state") == "all" or i["state"] == q.get("state", "open")]
            key = "updated_at" if q.get("sort") == "updated" else "created_at"
            items.sort(key=lambda i: (i[key], i["number"]), reverse=q.get("direction") == "desc")
            return self.ok(items[: int(q["per_page"])] if "per_page" in q and "page" not in q and q["per_page"] == "1" else items)
        if rest == "issues" and method == "POST":
            if self.fail_after_creates is not None and self.creates >= self.fail_after_creates:
                return self.err(500, "boom")
            self.creates += 1
            i = self.issue(full, body["title"], user=self.login, body=body.get("body", ""), labels=body.get("labels", []))
            if body.get("milestone"):
                i["milestone"] = next(x for x in r["milestones"] if x["number"] == body["milestone"])
            return self.ok(i)
        mm = re.fullmatch(r"issues/(\d+)", rest)
        if mm:
            i = r["issues"].get(int(mm.group(1)))
            if i is None:
                return self.err(404)
            if method == "PATCH":
                for k in ("state", "state_reason"):
                    if k in body:
                        i[k] = body[k]
                if "labels" in body:
                    i["labels"] = [{"name": n} for n in body["labels"]]
                if "milestone" in body:
                    i["milestone"] = next(x for x in r["milestones"] if x["number"] == body["milestone"])
            return self.ok(i)
        if rest == "issues/comments":
            return self.ok(r["comments"])
        mm = re.fullmatch(r"issues/(\d+)/comments", rest)
        if mm:
            n = int(mm.group(1))
            if method == "POST":
                self.comment_posts += 1
                if self.fail_comment_after is not None and self.comment_posts > self.fail_comment_after:
                    return self.err(500, "boom")
                r["comments"].append({"id": 5000 + len(r["comments"]), "issue_url": f"https://api.github.com/repos/{full}/issues/{n}",
                                      "user": {"login": self.login}, "body": body["body"], "created_at": "2026-02-01T00:00:00Z",
                                      "updated_at": "2026-02-01T00:00:00Z"})
                return self.ok({})
            return self.ok([c for c in r["comments"] if c["issue_url"].endswith(f"/issues/{n}")])
        if rest == "pulls/comments":
            return self.ok(r["review_comments"])
        if rest == "pulls" and method == "GET":
            return self.ok([i for n, i in r["issues"].items() if n in r["pulls"] and i["state"] == "open"])
        mm = re.fullmatch(r"pulls/(\d+)(?:/(reviews|commits))?", rest)
        if mm:
            n, sub = int(mm.group(1)), mm.group(2)
            return self.ok(r["pulls"][n] if not sub else (r["reviews"][n] if sub == "reviews" else r["commits"][n]))
        if rest == "labels":
            if method == "POST":
                r["labels"].append({"name": body["name"], "color": body["color"], "description": body.get("description", "")})
                return self.ok({})
            return self.ok(r["labels"])
        mm = re.fullmatch(r"labels/(.+)", rest)
        if mm:
            name = mm.group(1).replace("%20", " ")
            if method == "DELETE":
                r["labels"] = [l for l in r["labels"] if l["name"] != name]
            else:
                for l in r["labels"]:
                    if l["name"] == name:
                        l.update(body)
            return self.ok(None)
        if rest.startswith("milestones"):
            if method == "POST":
                ms = {"number": len(r["milestones"]) + 1, "title": body["title"], "state": "open", "description": body.get("description", ""), "due_on": body.get("due_on")}
                r["milestones"].append(ms)
                return self.ok(ms)
            if method == "PATCH":
                next(x for x in r["milestones"] if x["number"] == int(rest.split("/")[1]))["state"] = body["state"]
                return self.ok({})
            return self.ok(r["milestones"])
        if rest == "releases":
            if method == "POST":
                rel = {"id": 70 + len(r["releases"]), "tag_name": body["tag_name"], "name": body["name"], "body": body["body"], "draft": body["draft"],
                       "prerelease": body["prerelease"], "assets": [], "published_at": "2026-03-01T00:00:00Z"}
                r["releases"].append(rel)
                return self.ok(rel)
            return self.ok(r["releases"])
        if rest.startswith("collaborators/") and method == "PUT":
            r["collabs"].append((rest.split("/")[1], body["permission"]))
            return self.ok(None)
        if rest.startswith("collaborators"):
            return self.ok([c for c in r["collabs"] if isinstance(c, dict)])
        if rest == "invitations":
            return self.ok(r["invites"])
        if rest in ("actions/secrets", "dependabot/secrets"):
            return self.ok({"secrets": [{"name": n} for n in r["secrets"]] if rest.startswith("actions") else []})
        if rest == "actions/variables":
            if method == "POST":
                r["variables"].append({"name": body["name"], "value": body["value"]})
                return self.ok(None)
            return self.ok({"variables": r["variables"]})
        if rest == "actions/runners":
            return self.ok({"runners": r["runners"]})
        return self.err(404)

    def graphql(self, doc):
        v = doc["variables"]
        src = next(r for r in self.repos.values() for i in r["issues"].values() if i["node_id"] == v["i"])
        dst = next(r for r in self.repos.values() if f"R_{r['id']}" == v["r"])
        issue = next(i for i in src["issues"].values() if i["node_id"] == v["i"])
        del src["issues"][issue["number"]]
        n = dst["next"] + self.transfer_skew
        dst["next"] = n + 1
        moved = dict(issue, number=n, node_id=f"I_{dst['full']}_{n}")
        dst["issues"][n] = moved
        for c in src["comments"]:
            if c["issue_url"].endswith(f"/issues/{issue['number']}"):
                dst["comments"].append(dict(c, issue_url=f"https://api.github.com/repos/{dst['full']}/issues/{n}"))
        for l in moved["labels"]:
            if not any(x["name"] == l["name"] for x in dst["labels"]):
                dst["labels"].append({"name": l["name"], "color": "ededed", "description": ""})
        moved["labels"] = [l for l in moved["labels"] if any(x["name"] == l["name"] for x in dst["labels"])]
        return 0, json.dumps({"data": {"transferIssue": {"issue": {"number": n}}}}), ""


class Quiet:
    def __enter__(self):
        self.o, self.e = io.StringIO(), io.StringIO()
        self.a, self.b = redirect_stdout(self.o), redirect_stderr(self.e)
        self.a.__enter__(), self.b.__enter__()
        return self

    def __exit__(self, *x):
        self.b.__exit__(*x), self.a.__exit__(*x)

    @property
    def text(self):
        return self.o.getvalue() + self.e.getvalue()


class Pure(unittest.TestCase):
    def test_defang_breaks_mentions_but_not_code_or_emails(self):
        out = rb.defang("hi @octocat and @org/team, mail a@b.com, `@code` and\n```\n@fence\n```")
        self.assertIn("@​octocat", out)
        self.assertIn("@​org/team", out)
        self.assertIn("a@b.com", out)
        self.assertIn("`@code`", out)
        self.assertIn("@fence", out)
        self.assertNotIn("​fence", out)

    def test_path_matching(self):
        self.assertTrue(rb.path_matches("secret-dump.sql.gz", "secret-dump.sql.gz"))
        self.assertTrue(rb.path_matches("old/dir/secret-dump.sql.gz", "secret-dump.sql.gz"))
        self.assertFalse(rb.path_matches("old/secret-dump.sql.gz.txt", "secret-dump.sql.gz"))
        self.assertTrue(rb.path_matches("a/b.sql.gz", "*.sql.gz"))
        self.assertFalse(rb.path_matches("a/b.sql", "*.sql.gz"))

    def test_chunks_pack_and_split(self):
        self.assertEqual(len(rb.chunks(["a" * 100, "b" * 100])), 1)
        self.assertEqual(len(rb.chunks(["a" * 40000, "b" * 40000], 60000)), 2)
        self.assertLessEqual(len(rb.chunks(["x" * 200000])[0]), 60100)

    def test_placeholder_for_gap_pull_and_issue(self):
        self.assertIn("nothing was here", rb.placeholder_title(None, 9))
        item = {"kind": "pull", "number": 5, "issue": {"title": "Fix it", "body": "see @bob", "state": "closed", "user": {"login": "alice"},
                                                         "created_at": "2026-01-02T00:00:00Z", "closed_at": "2026-01-03T00:00:00Z"},
                "pr": {"merged_at": "2026-01-03T00:00:00Z", "head": {"ref": "f"}, "base": {"ref": "main"}, "commits": 2, "changed_files": 3, "additions": 4, "deletions": 5},
                "comments": [{"user": {"login": "bob"}, "body": "ping @alice", "created_at": "2026-01-02T05:00:00Z"}],
                "reviews": [{"user": {"login": "bob"}, "state": "APPROVED", "body": "ok", "submitted_at": "2026-01-02T06:00:00Z"},
                            {"user": {"login": "x"}, "state": "COMMENTED", "body": "", "submitted_at": "2026-01-02T07:00:00Z"}],
                "review_comments": [{"path": "a.py", "line": 3, "user": {"login": "bob"}, "body": "nit", "created_at": "2026-01-02T06:30:00Z"}],
                "commits": [{"sha": "c" * 40, "commit": {"message": "msg\nmore", "author": {"name": "A", "date": "2026-01-01T00:00:00Z"}}}]}
        self.assertEqual(rb.placeholder_title(item, 5), "Pull request: Fix it")
        body = rb.placeholder_body(item, 5, "2026-11-10")
        for want in ("placeholder for pull request #5", "alice", "merged on 2026-01-03", "`f`", "@​bob"):
            self.assertIn(want, body)
        texts = rb.placeholder_comments(item)
        self.assertEqual(len(texts), 2)
        self.assertIn("Review summary", texts[0])
        self.assertIn("bob** approved", texts[0])
        self.assertNotIn("x** commented", texts[0])  # an empty COMMENTED review is only a holder for inline comments
        self.assertIn("`a.py`:3", texts[0])
        self.assertIn("`cccccccccc` msg", texts[0])
        self.assertIn("@​alice", texts[1])

    def test_collaborator_permission_and_protection(self):
        self.assertEqual(rb.collaborator_permission({"role_name": "write"}), "push")
        self.assertEqual(rb.collaborator_permission({"role_name": "read"}), "pull")
        self.assertEqual(rb.collaborator_permission({"permissions": {"admin": False, "maintain": False, "push": True, "pull": True}}), "push")
        self.assertIsNone(rb.collaborator_permission({"role_name": "custom-role"}))
        self.assertIsNone(rb.protection_to_put({"error": 403}))
        body = rb.protection_to_put({"required_status_checks": {"strict": True, "contexts": ["ci"]}, "enforce_admins": {"enabled": True},
                                     "required_pull_request_reviews": {"required_approving_review_count": 2, "url": "x"},
                                     "restrictions": {"users": [{"login": "u"}], "teams": [], "apps": []}, "allow_force_pushes": {"enabled": False}})
        self.assertEqual(body["required_status_checks"], {"strict": True, "contexts": ["ci"]})
        self.assertTrue(body["enforce_admins"])
        self.assertEqual(body["required_pull_request_reviews"], {"required_approving_review_count": 2})
        self.assertEqual(body["restrictions"]["users"], ["u"])

    def test_decode_all_reads_pages_back_to_back(self):
        self.assertEqual(rb.decode_all('[1,2]\n[3]'), [[1, 2], [3]])
        self.assertEqual(rb.decode_all(""), [])


class Secrets(unittest.TestCase):
    def setUp(self):
        self.dir = Path(fresh_dir(self))

    def test_every_source_kind_reads_and_errors_never_show_values(self):
        (self.dir / "whole").write_text("whole-secret\n")
        (self.dir / ".env").write_text("OTHER=x\nexport TOKEN='quoted-secret'\nEMPTY=\n")
        (self.dir / "j.json").write_text(json.dumps({"K": "json-secret"}))
        with mock.patch.dict(os.environ, {"MY_VAR": "env-secret"}):
            self.assertEqual(rb.read_secret({"file": str(self.dir / "whole")}, "A"), "whole-secret")
            self.assertEqual(rb.read_secret({"file": str(self.dir / ".env"), "key": "TOKEN"}, "A"), "quoted-secret")
            self.assertEqual(rb.read_secret({"file": str(self.dir / "j.json"), "key": "K"}, "A"), "json-secret")
            self.assertEqual(rb.read_secret({"env": "MY_VAR"}, "A"), "env-secret")
        for spec in ({"file": str(self.dir / "nope")}, {"file": str(self.dir / ".env"), "key": "EMPTY"}, {"file": str(self.dir / ".env"), "key": "MISSING"},
                     {"env": "NOT_SET_ANYWHERE"}):
            with self.assertRaises(rb.Stop) as cm:
                rb.read_secret(spec, "NAME")
            self.assertNotIn("quoted-secret", str(cm.exception))

    def test_map_must_cover_every_name(self):
        (self.dir / "secret-names.json").write_text(json.dumps({"actions": ["A", "B"], "dependabot": ["D"]}))
        (self.dir / "v").write_text("val")
        with self.assertRaises(rb.Stop):
            rb.check_secrets_map(self.dir, None)
        m = self.dir / "map.json"
        m.write_text(json.dumps({"A": {"file": str(self.dir / "v")}, "B": {"file": str(self.dir / "v")}}))
        with self.assertRaises(rb.Stop) as cm:
            rb.check_secrets_map(self.dir, m)
        self.assertIn("dependabot:D", str(cm.exception))
        m.write_text(json.dumps({n: {"file": str(self.dir / "v")} for n in ("A", "B", "dependabot:D")}))
        self.assertEqual(sorted(rb.check_secrets_map(self.dir, m)), ["A", "B", "dependabot:D"])


class Scan(unittest.TestCase):
    def test_scan_finds_path_blob_and_commit_in_any_history_and_passes_a_clean_repo(self):
        base = Path(fresh_dir(self))
        work = base / "w"
        git("init", "-q", "-b", "main", str(work))
        (work / "ok.txt").write_text("ok")
        (work / "sub").mkdir()
        (work / "sub" / "secret-dump.sql.gz").write_text("dump")
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", "oops", cwd=work)
        bad = git("rev-parse", "HEAD", cwd=work)
        blob = git("rev-parse", "HEAD:sub/secret-dump.sql.gz", cwd=work)
        git("rm", "-q", "-r", "sub", cwd=work)
        git("commit", "-q", "-m", "remove", cwd=work)  # gone from the tip, still in history
        _, hits = rb.scan_git(work / ".git", {"paths": ["secret-dump.sql.gz"], "blobs": [], "commits": []})
        self.assertEqual(len(hits), 1)
        _, hits = rb.scan_git(work / ".git", {"paths": [], "blobs": [blob[:8]], "commits": []})
        self.assertTrue(hits[0].startswith("blob " + blob))
        _, hits = rb.scan_git(work / ".git", {"paths": [], "blobs": [], "commits": [bad[:7]]})
        self.assertEqual(hits, [f"commit {bad}"])
        _, hits = rb.scan_git(work / ".git", {"paths": ["other.bin"], "blobs": ["0" * 40], "commits": ["1" * 8]})
        self.assertEqual(hits, [])


class RateLimit(unittest.TestCase):
    def test_api_waits_and_retries_a_secondary_rate_limit_but_raises_other_errors(self):
        fake = FakeGitHub(fresh_dir(self))
        fake.add_repo("owner/r")
        fake.rate_limit_next = 2
        sleeps = []
        with mock.patch.object(rb, "run_gh", fake), mock.patch.object(rb, "sleep", sleeps.append), Quiet():
            self.assertEqual(rb.api("GET", "user")["login"], "owner")
        self.assertEqual(sleeps, [60, 120])
        with mock.patch.object(rb, "run_gh", fake), mock.patch.object(rb, "sleep", sleeps.append):
            with self.assertRaises(rb.ApiError) as cm:
                rb.api("GET", "repos/owner/missing")
        self.assertEqual(cm.exception.status, 404)


class EndToEnd(unittest.TestCase):
    """Every phase against the fake GitHub; the git side is real."""

    def setUp(self):
        self.base = Path(fresh_dir(self))
        self.git_base = self.base / "remote"
        self.backups = self.base / "backups"
        self.home = self.base / "home"
        self.home.mkdir()
        self.gh = FakeGitHub(self.git_base)
        self.repo = "owner/proj"
        tips = make_remote(self.git_base, self.repo, {"main": {"a.txt": "a"}, "feature": {"b.txt": "b"}})
        git("tag", "v1", tips["main"], cwd=str(self.git_base / f"{self.repo}.git"))
        self.tips = tips
        r = self.gh.add_repo(self.repo)
        r["labels"] = [{"name": "bug", "color": "d73a4a", "description": "broken"}, {"name": "area x", "color": "00ff00", "description": ""}]
        r["milestones"] = [{"number": 1, "title": "v1", "state": "open", "description": "m", "due_on": None}]
        r["variables"] = [{"name": "RUNNER_LABEL", "value": "workstation"}]
        r["secrets"] = ["API_KEY"]
        r["collabs"] = [{"login": "owner", "role_name": "admin"}, {"login": "sam", "role_name": "write"}, {"login": "pat", "role_name": "maintain"}]
        r["invites"] = [{"invitee": {"login": "newbie"}, "permissions": "read"}]
        r["releases"] = [{"id": 11, "tag_name": "v1", "name": "one", "body": "notes", "draft": False, "prerelease": False,
                          "published_at": "2026-01-01T00:00:00Z", "assets": [{"id": 5, "name": "dump.sql.gz", "size": 4}]}]
        self.gh.issue(self.repo, "first issue", number=1, user="alice", labels=["bug"], comments=[("bob", "hello @alice")])
        self.gh.issue(self.repo, "a merged change", number=2, user="carol", state="closed", pull={"merged_at": "2026-01-05T00:00:00Z"}, comments=[("dave", "lgtm")])
        # number 3 was never used: a gap
        self.gh.issue(self.repo, "an unmerged change", number=4, user="erin", state="closed", pull={})
        self.gh.issue(self.repo, "closed issue", number=5, user="frank", state="closed", labels=["area x"], comments=[("g", "one"), ("h", "two")])
        r["issues"][1]["milestone"] = r["milestones"][0]
        self.secret_file = self.base / "secrets.env"
        self.secret_file.write_text("API_KEY=very-secret-value-123\n")
        self.map = self.base / "map.json"
        self.map.write_text(json.dumps({"API_KEY": {"file": str(self.secret_file), "key": "API_KEY"}}))
        self.patches = [mock.patch.object(rb, "run_gh", self.gh), mock.patch.object(rb, "sleep", lambda s: None),
                        mock.patch.object(rb, "download_asset", self.fake_download),
                        mock.patch.dict(os.environ, {"REPO_REBUILD_GIT_BASE": f"file://{self.git_base}/", "REPO_REBUILD_BACKUPS": str(self.backups),
                                                     "REPO_REBUILD_PACE": "0", "HOME": str(self.home), **GIT_ENV})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        rb.LOG_FILE = None
        self.dir = self.base / "export"

    def fake_download(self, repo, asset_id, dest):
        Path(dest).write_bytes(b"dump")
        return True

    def run_phase(self, phase, *extra, expect=0):
        with Quiet() as q:
            rc = rb.main([phase, self.repo, "--dir", str(self.dir), *extra])
        self.assertEqual(rc, expect, q.text)
        return q.text

    def export(self, *forbid):
        return self.run_phase("export", *(forbid or ("--forbid-path", "secret-dump.sql.gz")))

    def up_to_clean(self):
        self.export()
        self.run_phase("prove-clean")

    def swap(self, *extra, expect=0):
        return self.run_phase("swap", "--secrets-map", str(self.map), *extra, expect=expect)

    def full_run(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        self.run_phase("reattach", "--secrets-map", str(self.map))
        return self.run_phase("prove")

    # -- export
    def test_export_dry_run_writes_nothing_and_counts_everything(self):
        out = self.run_phase("export", "--dry-run", "--forbid-path", "x")
        self.assertIn("highest number 5; 2 issues, 2 pull requests", out)
        self.assertFalse(self.dir.exists())

    def test_export_keeps_everything_and_never_a_secret_value(self):
        self.export("--forbid-path", "secret-dump.sql.gz", "--forbid-commit", "abc123")
        for name in ("settings.json", "labels.json", "milestones.json", "releases.json", "collaborators.json", "invitations.json",
                     "variables.json", "secret-names.json", "runners.json", "extras.json", "forbidden.json", "meta.json", "release-assets.json"):
            self.assertTrue((self.dir / name).exists(), name)
        self.assertEqual(sorted(p.name for p in (self.dir / "items").glob("*.json")), ["1.json", "2.json", "4.json", "5.json"])
        self.assertEqual(json.loads((self.dir / "secret-names.json").read_text())["actions"], ["API_KEY"])
        pr = json.loads((self.dir / "items" / "2.json").read_text())
        self.assertEqual(pr["kind"], "pull")
        self.assertEqual(len(pr["comments"]), 1)
        self.assertEqual(len(pr["commits"]), 1)
        self.assertEqual((self.dir / "releases" / "11" / "dump.sql.gz").read_bytes(), b"dump")
        meta = json.loads((self.dir / "meta.json").read_text())
        self.assertEqual((meta["max_number"], meta["old_name"].startswith("proj-old-")), (5, True))
        self.assertIn("refs/heads/main", meta["heads"])
        self.assertNotIn("refs/pull", json.dumps(meta["heads"]))
        self.assertNotIn("very-secret-value-123", "".join(p.read_text() for p in self.dir.rglob("*.json")))

    def test_export_needs_a_forbidden_list_and_refuses_to_overwrite(self):
        out = self.run_phase("export", expect=1)
        self.assertIn("at least one --forbid", out)
        self.export()
        self.assertIn("already holds an export", self.run_phase("export", "--forbid-path", "x", expect=1))
        self.run_phase("export", "--forbid-path", "x", "--refresh")

    # -- prove-clean
    def test_prove_clean_stops_on_a_forbidden_commit_path_or_blob(self):
        self.export("--forbid-commit", self.tips["feature"][:8])
        self.assertIn("forbidden object", self.run_phase("prove-clean", expect=1))
        self.assertFalse((self.dir / "clean.json").exists())
        self.run_phase("export", "--refresh", "--forbid-path", "b.txt")
        self.assertIn("FOUND path b.txt", self.run_phase("prove-clean", expect=1))

    def test_prove_clean_dry_run_writes_no_marker(self):
        self.export()
        self.run_phase("prove-clean", "--dry-run")
        self.assertFalse((self.dir / "clean.json").exists())

    # -- swap
    def test_swap_needs_prove_clean_first(self):
        self.export()
        self.assertIn("prove-clean has not passed", self.run_phase("swap", "--skip-secrets-check", expect=1))

    def test_swap_dry_run_changes_nothing(self):
        self.up_to_clean()
        before = [c for c in self.gh.calls if c[0][2:3] in (["PATCH"], ["POST"], ["PUT"])]
        self.assertIn("swap plan: rename", self.swap("--dry-run"))
        self.assertEqual([c for c in self.gh.calls if c[0][2:3] in (["PATCH"], ["POST"], ["PUT"])], before)
        self.assertIn(self.repo, self.gh.repos)

    def test_swap_stops_when_github_moved_since_the_export(self):
        self.up_to_clean()
        self.gh.issue(self.repo, "late issue", number=6)
        self.assertIn("changed since the export", self.swap(expect=1))
        self.assertIn(self.repo, self.gh.repos)
        self.assertNotIn("owner/proj-old", " ".join(self.gh.repos))

    def test_swap_stops_on_a_moved_branch(self):
        self.up_to_clean()
        work = self.git_base / f"{self.repo}.git-work"
        (work / "late.txt").write_text("x")
        git("checkout", "-q", "main", cwd=work)
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", "late", cwd=work)
        git("push", "-q", str(self.git_base / f"{self.repo}.git"), "main:main", cwd=work)
        self.assertIn("moved since the export", self.swap(expect=1))

    def test_swap_refuses_open_pull_requests_and_missing_secret_sources(self):
        self.gh.issue(self.repo, "open pr", number=6, pull={})
        self.export()
        self.run_phase("prove-clean")
        self.assertIn("pull request(s) are open", self.swap(expect=1))
        self.gh.repos[self.repo]["issues"].pop(6)
        self.run_phase("export", "--refresh", "--forbid-path", "x")
        self.run_phase("prove-clean")
        self.assertIn("pass --secrets-map", self.run_phase("swap", expect=1))

    def test_swap_renames_creates_pushes_and_turns_actions_off(self):
        self.up_to_clean()
        out = self.swap()
        self.assertIn("matches the mirror", out)
        old = self.gh.repos[f"owner/{json.loads((self.dir / 'meta.json').read_text())['old_name']}"]
        self.assertTrue(old["private"])
        new = self.gh.repos[self.repo]
        self.assertTrue(new["private"])
        self.assertFalse(new["actions_enabled"])  # nothing may trigger a deploy while pushing
        heads = subprocess.run(["git", "ls-remote", f"file://{self.git_base}/{self.repo}.git"], capture_output=True, text=True).stdout
        self.assertIn(self.tips["main"], heads)
        self.assertIn("refs/tags/v1", heads)
        self.assertTrue((self.dir / "swap.json").exists())

    # -- rebuild
    def test_rebuild_lands_every_number_in_order_and_keeps_authors_and_dates(self):
        self.up_to_clean()
        self.swap()
        out = self.run_phase("rebuild")
        self.assertIn("#1..#5 in order", out)
        new = self.gh.repos[self.repo]["issues"]
        self.assertEqual(sorted(new), [1, 2, 3, 4, 5])
        self.assertEqual((new[1]["user"]["login"], new[1]["created_at"]), ("alice", "2026-01-02T10:00:00Z"))  # transferred: author and date kept
        self.assertEqual([l["name"] for l in new[1]["labels"]], ["bug"])
        self.assertEqual(new[1]["milestone"]["title"], "v1")
        self.assertEqual(new[1]["state"], "open")
        self.assertEqual(new[5]["state"], "closed")
        self.assertEqual(new[5]["user"]["login"], "frank")
        self.assertEqual(new[2]["title"], "Pull request: a merged change")
        self.assertEqual((new[2]["state"], new[2]["state_reason"]), ("closed", "completed"))
        self.assertIn("merged on 2026-01-05", new[2]["body"])
        self.assertEqual(new[3]["title"], "Number 3: nothing was here")
        self.assertEqual((new[4]["state"], new[4]["state_reason"]), ("closed", "not_planned"))
        mine = lambda n: [c["body"] for c in self.gh.repos[self.repo]["comments"] if c["issue_url"].endswith(f"/issues/{n}")]
        self.assertEqual(len(mine(1)), 1)  # moved with the issue
        self.assertEqual(len(mine(5)), 2)
        self.assertTrue(any("lgtm" in b for b in mine(2)))
        self.assertTrue(any("Commits" in b for b in mine(2)))
        self.assertEqual(len(self.gh.repos[f"owner/{json.loads((self.dir / 'meta.json').read_text())['old_name']}"]["issues"]), 2)  # PR numbers stay behind

    def test_rebuild_stops_at_the_first_wrong_number(self):
        self.up_to_clean()
        self.swap()
        self.gh.transfer_skew = 1
        out = self.run_phase("rebuild", expect=1)
        self.assertIn("came back as #2", out)
        self.assertEqual(self.gh.repos[self.repo]["issues"][2]["title"], "first issue")
        self.assertFalse((self.dir / "rebuild-state.json").exists())

    def test_rebuild_resumes_after_a_crash_in_the_middle_of_a_placeholder(self):
        self.up_to_clean()
        self.swap()
        self.gh.fail_after_creates = 2  # #1 transfers; #2 and #3 are created; #4 dies
        self.run_phase("rebuild", expect=1)
        self.assertEqual(json.loads((self.dir / "rebuild-state.json").read_text())["done"], 3)
        self.gh.fail_after_creates = None
        out = self.run_phase("rebuild")
        self.assertIn("#1..#5 in order", out)
        self.assertEqual(sorted(self.gh.repos[self.repo]["issues"]), [1, 2, 3, 4, 5])
        comments_on_2 = [c for c in self.gh.repos[self.repo]["comments"] if c["issue_url"].endswith("/issues/2")]
        self.assertEqual(len(comments_on_2), 2)  # not doubled by the resume

    def test_rebuild_resumes_when_the_state_file_is_one_behind(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        (self.dir / "rebuild-state.json").write_text(json.dumps({"done": 4}))
        self.run_phase("rebuild")
        self.assertEqual(len([c for c in self.gh.repos[self.repo]["comments"] if c["issue_url"].endswith("/issues/5")]), 2)

    def test_rebuild_no_transfer_copies_issues_as_text(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild", "--no-transfer")
        i1 = self.gh.repos[self.repo]["issues"][1]
        self.assertEqual(i1["user"]["login"], "owner")
        self.assertIn("Originally opened by alice", i1["body"])
        self.assertEqual(i1["title"], "first issue")

    # -- reattach, prove, delete
    def test_reattach_sets_everything_and_never_prints_the_secret(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        out = self.run_phase("reattach", "--secrets-map", str(self.map))
        new = self.gh.repos[self.repo]
        self.assertTrue(new["actions_enabled"])
        self.assertEqual(new["variables"], [{"name": "RUNNER_LABEL", "value": "workstation"}])
        self.assertEqual(sorted(new["collabs"]), [("newbie", "pull"), ("pat", "maintain"), ("sam", "push")])
        self.assertEqual([r["tag_name"] for r in new["releases"]], ["v1"])
        self.assertEqual([a["name"] for a in new["releases"][0]["assets"]], ["dump.sql.gz"])
        self.assertEqual(self.gh.secrets_set, [(self.repo, "API_KEY", "very-secret-value-123")])
        self.assertNotIn("very-secret-value-123", out)
        self.assertNotIn("very-secret-value-123", (self.dir / "rebuild.log").read_text())
        self.assertNotIn("very-secret-value-123", json.dumps([c[0] for c in self.gh.calls]))  # never on a command line

    def test_reattach_can_leave_backup_assets_out(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        self.run_phase("reattach", "--secrets-map", str(self.map), "--skip-assets", "*.sql.gz")
        self.assertEqual(self.gh.repos[self.repo]["releases"][0]["assets"], [])

    def test_reattach_before_rebuild_is_refused(self):
        self.up_to_clean()
        self.swap()
        self.assertIn("rebuild has not finished", self.run_phase("reattach", "--secrets-map", str(self.map), expect=1))

    def test_reattach_dry_run_changes_nothing(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        before = len(self.gh.calls)
        out = self.run_phase("reattach", "--secrets-map", str(self.map), "--dry-run")
        self.assertIn("collaborators: 3 invited", out)
        self.assertEqual([c for c in self.gh.calls[before:] if c[0][2:3] in (["PUT"], ["POST"], ["PATCH"]) or c[0][:1] == ["secret"]], [])

    def test_runner_is_stopped_removed_and_registered_to_the_new_repo(self):
        rdir = self.home / "actions-runner-proj"
        rdir.mkdir()
        (rdir / ".runner").write_text('﻿{"agentName": "ws"}')
        for n in ("config.sh", "run.sh"):
            (rdir / n).write_text(f'#!/bin/sh\necho "$@" >> "{rdir}/{n}.args"\necho "$ACTIONS_RUNNER_INPUT_TOKEN" >> "{rdir}/{n}.token"\n')
            (rdir / n).chmod(0o755)
        self.gh.repos[self.repo]["runners"] = []
        old_runners = [{"name": "ws", "status": "online", "labels": [{"name": "self-hosted", "type": "read-only"}, {"name": "workstation", "type": "custom"}]}]
        self.up_to_clean()
        (self.dir / "runners.json").write_text(json.dumps(old_runners))
        self.swap()
        self.run_phase("rebuild")
        self.gh.repos[self.repo]["runners"] = old_runners  # as if GitHub now lists it
        real_route = self.gh.route

        def route(method, path, body=None):
            if path.endswith("registration-token"):
                return 0, json.dumps({"token": "TOK123"}), ""
            return real_route(method, path, body)
        self.gh.route = route
        with mock.patch.object(Path, "home", return_value=self.home):
            out = self.run_phase("reattach", "--secrets-map", str(self.map))
        self.assertIn("'ws' registered", out)
        args = (rdir / "config.sh.args").read_text().splitlines()
        self.assertEqual(args[0], "remove --local")
        self.assertIn(f"--url https://github.com/{self.repo}", args[1])
        self.assertIn("--name ws --labels workstation --replace", args[1])
        self.assertNotIn("TOK123", out)
        self.assertNotIn("TOK123", (self.dir / "rebuild.log").read_text())
        self.assertNotIn("TOK123", " ".join(args))  # not on the command line, where other users can read it
        self.assertEqual((rdir / "config.sh.token").read_text().splitlines(), ["", "TOK123"])  # the remove call has none; the register call has it in its environment

    # -- the review's findings
    def test_delete_old_refuses_after_a_manual_roll_back_because_github_follows_the_old_name(self):
        self.full_run()
        old = f"owner/{json.loads((self.dir / 'meta.json').read_text())['old_name']}"
        self.gh.scopes = "repo, delete_repo"
        # the operator rolls back by hand: the new repo steps aside, the old one takes its name back
        self.gh.route("PATCH", f"repos/{self.repo}", {"name": "proj-bad"})
        self.gh.route("PATCH", f"repos/{old}", {"name": "proj"})
        out = self.run_phase("delete-old", "--yes-delete", expect=1)
        self.assertIn("refusing to delete", out)
        self.assertEqual(self.gh.deleted, [])
        self.assertIn("owner/proj", self.gh.repos)

    def test_rebuild_resumes_a_placeholder_without_doubling_its_comments(self):
        self.up_to_clean()
        self.swap()
        self.gh.fail_comment_after = 1  # the first comment lands, the second fails: stopped in the middle of #2
        self.run_phase("rebuild", expect=1)
        on2 = lambda: [c["body"] for c in self.gh.repos[self.repo]["comments"] if c["issue_url"].endswith("/issues/2")]
        self.assertEqual(len(on2()), 1)
        self.gh.fail_comment_after = None
        self.run_phase("rebuild")
        self.assertEqual(len(on2()), 2)
        self.assertEqual(len(set(on2())), 2)
        self.assertEqual(self.gh.repos[self.repo]["issues"][2]["state"], "closed")

    def test_rebuild_stops_when_a_number_was_taken_by_something_else(self):
        self.up_to_clean()
        self.swap()
        self.gh.fail_after_creates = 0  # #1 transfers, creating #2 fails
        self.run_phase("rebuild", expect=1)
        self.gh.fail_after_creates = None
        self.gh.issue(self.repo, "Bump a dependency", number=2, user="dependabot[bot]", pull={})
        out = self.run_phase("rebuild", expect=1)
        self.assertIn("is not our placeholder", out)
        self.assertEqual(self.gh.repos[self.repo]["issues"][2]["state"], "open")  # nothing written onto it
        self.assertEqual([c for c in self.gh.repos[self.repo]["comments"] if c["issue_url"].endswith("/issues/2")], [])

    def test_reattach_never_claims_actions_are_on_when_they_are_not(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        extras = json.loads((self.dir / "extras.json").read_text())
        extras["actions_permissions"] = {"error": 403}
        (self.dir / "extras.json").write_text(json.dumps(extras))
        out = self.run_phase("reattach", "--secrets-map", str(self.map), expect=1)
        self.assertIn("ACTIONS ARE STILL OFF", out)
        self.assertIn("Actions is still off", out)
        self.assertFalse(self.gh.repos[self.repo]["actions_enabled"])
        self.assertNotIn("back on", out)

    def test_export_stops_when_a_list_cannot_be_read(self):
        real = self.gh.route

        def route(method, path, body=None):
            if path.startswith(f"repos/{self.repo}/actions/secrets"):
                return self.gh.err(403, "Must have admin rights")
            return real(method, path, body)
        self.gh.route = route
        self.assertIn("could not read Actions secrets", self.run_phase("export", "--forbid-path", "x", expect=1))
        self.assertFalse((self.dir / "meta.json").exists())

    def test_export_keeps_only_the_host_of_a_webhook(self):
        real = self.gh.route

        def route(method, path, body=None):
            if path == f"repos/{self.repo}/hooks?per_page=100":
                return self.gh.ok([{"id": 1, "name": "web", "config": {"url": "https://hooks.example.com/services/T000/B000/SECRETPART"},
                                    "events": ["push"], "active": True}])
            return real(method, path, body)
        self.gh.route = route
        self.export()
        text = (self.dir / "extras.json").read_text()
        self.assertIn("hooks.example.com", text)
        self.assertNotIn("SECRETPART", text)

    def test_swap_stops_when_the_new_repo_comes_back_public(self):
        self.up_to_clean()
        self.gh.create_private = False
        out = self.swap(expect=1)
        self.assertIn("came back public", out)
        self.assertIn("to undo", out)

    def test_a_failed_push_says_what_state_things_are_in(self):
        self.up_to_clean()
        real = rb.run_git

        def failing(*args, **kw):
            if args[:3] == ("-C", str(self.dir / "repo.git"), "push"):
                raise rb.Stop("git push exited 1: denied")
            return real(*args, **kw)
        with mock.patch.object(rb, "run_git", failing):
            out = self.swap(expect=1)
        self.assertIn("partly filled", out)
        self.assertIn("to undo", out)

    def test_prove_passes_when_the_old_repo_serves_the_object_and_the_new_one_does_not(self):
        commit, blob = "a" * 40, "b" * 40
        self.gh.repos[self.repo]["objects"] |= {commit, blob}  # after the swap these stay with the old repo
        self.export("--forbid-commit", commit, "--forbid-blob", blob)
        self.run_phase("prove-clean")
        self.swap()
        self.run_phase("rebuild")
        self.run_phase("reattach", "--secrets-map", str(self.map))
        out = self.run_phase("prove")
        self.assertIn(f"old commit {commit[:10]} -> 422", out)
        self.assertIn(f"old blob {blob[:10]} -> 404", out)
        self.assertIn("control: the old repo still serves", out)
        self.gh.repos[self.repo]["objects"] |= {commit}  # now the new repo serves it: the proof must fail
        self.assertIn("FAIL old commit", self.run_phase("prove", expect=1))

    def test_prove_fails_when_the_new_repo_is_public(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        self.gh.repos[self.repo]["private"] = True
        self.run_phase("prove")
        self.gh.repos[self.repo]["private"] = False
        self.assertIn("FAIL the new repo is private", self.run_phase("prove", expect=1))

    def test_prove_compares_every_number_not_a_sample(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        self.gh.repos[self.repo]["issues"][3]["title"] = "tampered"  # a gap placeholder, unlikely to be in any sample
        out = self.run_phase("prove", expect=1)
        self.assertIn("#3 does not match", out)

    def test_swap_is_stopped_by_an_edit_made_after_the_export(self):
        self.up_to_clean()
        self.gh.repos[self.repo]["issues"][1]["updated_at"] = "2099-01-01T00:00:00Z"
        self.assertIn("changed since the export", self.swap(expect=1))

    def test_reattach_adds_only_the_missing_asset_of_a_half_uploaded_release(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        self.gh.repos[self.repo]["releases"] = [{"id": 5, "tag_name": "v1", "name": "one", "body": "", "draft": False, "prerelease": False, "assets": []}]
        self.run_phase("reattach", "--secrets-map", str(self.map))
        self.assertEqual([a["name"] for a in self.gh.repos[self.repo]["releases"][0]["assets"]], ["dump.sql.gz"])
        self.assertEqual(len(self.gh.repos[self.repo]["releases"]), 1)

    def test_reattach_skips_an_email_invitation_and_a_deploy_key_the_old_repo_holds(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        (self.dir / "invitations.json").write_text(json.dumps([{"invitee": None, "email": "x@example.com", "permissions": "read"}]))
        extras = json.loads((self.dir / "extras.json").read_text())
        extras["deploy_keys"] = [{"title": "ci", "key": "ssh-ed25519 AAAA", "read_only": True}]
        (self.dir / "extras.json").write_text(json.dumps(extras))
        old = next(r for k, r in self.gh.repos.items() if k != self.repo)
        old["keys"].append({"title": "ci", "key": "ssh-ed25519 AAAA"})
        out = self.run_phase("reattach", "--secrets-map", str(self.map))
        self.assertIn("x@example.com", out)
        self.assertIn("deploy keys still held by the old repo", out)

    def test_full_run_then_prove_and_delete_old(self):
        out = self.full_run()
        self.assertIn("prove: every check passed", out)
        self.assertTrue((self.dir / "proved.json").exists())
        old = f"owner/{json.loads((self.dir / 'meta.json').read_text())['old_name']}"
        self.assertIn("add --yes-delete", self.run_phase("delete-old", expect=1))
        self.assertIn(old, self.gh.repos)
        self.gh.scopes = "repo, workflow"
        self.assertIn("lacks the delete_repo scope", self.run_phase("delete-old", "--yes-delete", expect=1))
        self.assertIn(old, self.gh.repos)
        self.gh.scopes = "repo, delete_repo"
        self.run_phase("delete-old", "--dry-run")
        self.assertIn(old, self.gh.repos)
        self.run_phase("delete-old", "--yes-delete")
        self.assertEqual(self.gh.deleted, [old])
        self.assertIn(self.repo, self.gh.repos)

    def test_delete_old_refuses_without_a_passed_prove(self):
        self.up_to_clean()
        self.swap()
        self.gh.scopes = "repo, delete_repo"
        self.assertIn("prove has not passed", self.run_phase("delete-old", "--yes-delete", expect=1))
        self.assertEqual(self.gh.deleted, [])

    def test_delete_old_refuses_a_proof_from_another_swap(self):
        self.full_run()
        pr = json.loads((self.dir / "proved.json").read_text())
        pr["swap_at"] = "1999-01-01T00:00:00"
        (self.dir / "proved.json").write_text(json.dumps(pr))
        self.gh.scopes = "repo, delete_repo"
        self.assertIn("prove has not passed", self.run_phase("delete-old", "--yes-delete", expect=1))

    def test_prove_fails_when_the_new_repo_still_holds_a_forbidden_path(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        work = self.git_base / f"{self.repo}.git-work"
        (work / "secret-dump.sql.gz").write_text("dump")
        git("checkout", "-q", "main", cwd=work)
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", "bad", cwd=work)
        git("push", "-q", str(self.git_base / f"{self.repo}.git"), "main:main", cwd=work)
        out = self.run_phase("prove", expect=1)
        self.assertIn("FAIL forbidden objects", out)
        self.assertFalse((self.dir / "proved.json").exists())

    def test_prove_fails_on_a_wrong_number_and_on_a_short_blob_id(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        self.gh.repos[self.repo]["issues"][5]["title"] = "tampered"
        out = self.run_phase("prove", expect=1)
        self.assertIn("#5 does not match", out)
        self.gh.repos[self.repo]["issues"][5]["title"] = "closed issue"
        out = self.run_phase("prove", "--forbid-blob", "abcd1234", expect=1)
        self.assertIn("needs all 40 characters", out)

    def test_prove_needs_the_old_repo_to_serve_the_object_or_the_check_proves_nothing(self):
        self.up_to_clean()
        self.swap()
        self.run_phase("rebuild")
        out = self.run_phase("prove", "--forbid-commit", "deadbeef", expect=1)
        self.assertIn("control failed", out)  # the old repo doesn't serve it either, so a 'gone' answer means nothing


if __name__ == "__main__":
    unittest.main()
