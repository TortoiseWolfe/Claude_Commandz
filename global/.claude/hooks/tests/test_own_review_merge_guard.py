"""own-review-merge-guard.py: no session merges in a repo that keeps its own review.

The hook is run the way Claude Code runs it: as its own process, the tool call as JSON on
stdin, exit 2 with a reason on stderr to refuse. HOME points at a temp folder holding a
made-up list, and every repo here is a temp folder made with git init, so nothing reads the
real list or a real repo. All names are invented.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "own-review-merge-guard.py"
GUEST = "example-org/their-app"       # matches the list line example-org/*
NAMED = "example-owner/guest-repo"    # matches the list line that names one repo
OWN = "example-owner/own-app"         # on no list line
MERGE_QUERY = "mutation { %s(input: {pullRequestId: \"PR_x\"}) { clientMutationId } }"


def make_repo(folder, branch="main", **remotes):
    folder.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(folder)], check=True, capture_output=True)
    for name, repo in remotes.items():
        subprocess.run(["git", "-C", str(folder), "remote", "add", name, "https://github.com/%s.git" % repo],
                       check=True, capture_output=True)
    return str(folder)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="own-review-guard-"))
        cls.home = cls.tmp / "home"
        (cls.home / ".config" / "afa").mkdir(parents=True)
        (cls.home / ".config" / "afa" / "own-review-repos.txt").write_text(
            "# repos where we are a guest\nexample-org/*\n\nexample-owner/guest-repo\n")
        cls.no_list_home = cls.tmp / "no-list-home"
        cls.no_list_home.mkdir()
        cls.empty_list_home = cls.tmp / "empty-list-home"
        (cls.empty_list_home / ".config" / "afa").mkdir(parents=True)
        (cls.empty_list_home / ".config" / "afa" / "own-review-repos.txt").write_text("# nothing listed\n\n")
        cls.guest = make_repo(cls.tmp / "guest", origin=GUEST)
        cls.guest_feature = make_repo(cls.tmp / "guest-feature", branch="feature", origin=GUEST)
        cls.own = make_repo(cls.tmp / "own", origin=OWN)
        cls.fork = make_repo(cls.tmp / "fork", origin="example-owner/their-app", upstream=GUEST)
        cls.ssh_guest = cls.tmp / "ssh-guest"
        make_repo(cls.ssh_guest)
        subprocess.run(["git", "-C", str(cls.ssh_guest), "remote", "add", "origin", "git@github.com:%s.git" % GUEST],
                       check=True, capture_output=True)
        cls.ssh_guest = str(cls.ssh_guest)
        cls.home_guest = make_repo(cls.home / "code" / "their-app", origin=GUEST)
        (Path(cls.guest) / "src").mkdir()
        cls.plain = cls.tmp / "plain"
        cls.plain.mkdir()
        cls.plain = str(cls.plain)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_hook(self, command, cwd="OWN", home=None, env=None, payload=None):
        if payload is None:
            payload = {"tool_name": "Bash", "tool_input": {"command": command}}
            if cwd is not None:
                payload["cwd"] = self.own if cwd == "OWN" else cwd
            payload = json.dumps(payload)
        full_env = {
            "HOME": str(home or self.home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            # A temp folder that sits inside some other checkout must not read as that checkout.
            "GIT_CEILING_DIRECTORIES": str(self.tmp),
        }
        full_env.update(env or {})
        r = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, env=full_env)
        return r.returncode, r.stderr

    def refused(self, command, *needles, **kw):
        code, err = self.run_hook(command, **kw)
        self.assertEqual(code, 2, "allowed, but should be refused: %s\n%s" % (command, err))
        self.assertTrue(err.startswith("BLOCKED:"), err)
        for needle in needles:
            self.assertIn(needle, err)
        return err

    def allowed(self, command, **kw):
        code, err = self.run_hook(command, **kw)
        self.assertEqual((code, err), (0, ""), "refused, but should be allowed: %s" % command)


class MergeNamedInTheCommand(Base):
    def test_gh_pr_merge_with_the_repo_named_is_refused(self):
        for command in (
            "gh pr merge 5 -R %s" % GUEST,
            "gh pr merge 5 --squash --delete-branch -R %s" % GUEST,
            "gh pr merge 5 --auto --squash -R %s" % GUEST,
            "gh pr merge 5 --admin --repo=%s" % GUEST,
            "gh pr merge 5 --repo %s" % GUEST,
            "gh pr merge 5 -R github.com/%s" % GUEST,
            "gh pr merge 5 -R https://github.com/%s" % GUEST,
            "gh pr merge 5 -R '%s'" % GUEST,
            'gh pr merge 5 -R "%s"' % GUEST,
            "gh pr merge 5 -R%s" % GUEST,
            "gh pr merge 5 -sdR %s" % GUEST,
            "gh pr merge 5 -R Example-Org/Their-App",
            "gh -R %s pr merge 5" % GUEST,
            "gh.exe pr merge 5 -R %s" % GUEST,
            "/usr/bin/gh pr merge 5 -R %s" % GUEST,
            "GH_REPO=%s gh pr merge 5" % GUEST,
            "export GH_REPO=%s; gh pr merge 5 --squash" % GUEST,
            "gh pr merge https://github.com/%s/pull/5 --squash" % GUEST,
            "gh pr merge 5 -R %s" % NAMED,
            "gh pr view 5 -R %s && gh pr merge 5 -R %s" % (GUEST, GUEST),
            "gh pr merge 5 -R %s && gh pr merge 6 -R %s" % (OWN, GUEST),
            "bash -c 'gh pr merge 5 -R %s'" % GUEST,
            '"gh" pr merge 5 -R %s' % GUEST,
            "gh 'pr' merge 5 -R %s" % GUEST,
            "echo start\ngh pr merge 5 -R %s\necho done" % GUEST,
        ):
            with self.subTest(command=command):
                self.refused(command, "keeps its own review process", "Nobody here merges in it", "not the hub")

    def test_a_merge_in_a_repo_off_the_list_is_allowed_even_from_a_guest_folder(self):
        for command in (
            "gh pr merge 5 -R %s" % OWN,
            "gh pr merge 5 --squash --delete-branch --repo=%s" % OWN,
            "gh -R %s pr merge 5 --auto" % OWN,
            "GH_REPO=%s gh pr merge 5" % OWN,
            "gh pr merge https://github.com/%s/pull/5" % OWN,
            "bash -c 'gh pr merge 5 -R %s'" % OWN,
        ):
            with self.subTest(command=command):
                self.allowed(command, cwd=self.guest)

    def test_the_reason_names_the_repo_and_the_list_line_but_never_echoes_the_command(self):
        err = self.refused("gh pr merge 5 -R %s --subject ZEBRA-MARKER-1234" % GUEST, GUEST, "example-org/*")
        self.assertNotIn("ZEBRA", err)
        self.assertNotIn("gh pr merge 5", err)


class MergeByFolder(Base):
    def test_a_merge_with_no_repo_named_reads_the_folder_it_runs_in(self):
        for cwd in (self.guest, self.fork, self.ssh_guest, os.path.join(self.guest, "src")):
            for command in ("gh pr merge 5", "gh pr merge", "gh pr merge 5 --auto --squash", "bash -c 'gh pr merge 5'"):
                with self.subTest(cwd=os.path.basename(cwd), command=command):
                    self.refused(command, "keeps its own review process", cwd=cwd)

    def test_a_merge_after_a_look_at_another_repo_still_reads_the_folder(self):
        for command in ("gh pr view 5 -R %s; gh pr merge 5" % OWN, "gh pr view 5 -R %s && gh pr merge 5" % OWN):
            with self.subTest(command=command):
                self.refused(command, GUEST, cwd=self.guest)

    def test_a_folder_change_inside_the_command_counts(self):
        for command in (
            "cd %s && gh pr merge 5" % self.guest,
            "pushd %s; gh pr merge 5" % self.guest,
            "(cd %s && gh pr merge 5 --squash)" % self.guest,
            "cd '%s' && gh pr merge 5" % self.guest,
            "cd ../guest && gh pr merge 5",
            "cd ~/code/their-app && gh pr merge 5",
            "cd $HOME/code/their-app && gh pr merge 5",
        ):
            with self.subTest(command=command):
                self.refused(command, GUEST)

    def test_a_merge_in_our_own_folder_is_allowed(self):
        self.allowed("gh pr merge 5 --squash --delete-branch")
        self.allowed("cd %s && gh pr merge 5" % self.own, cwd=self.plain)

    def test_gh_repo_from_the_sessions_environment_counts(self):
        self.refused("gh pr merge 5", GUEST, env={"GH_REPO": GUEST})
        self.allowed("gh pr merge 5", env={"GH_REPO": OWN})
        self.refused("gh pr merge 5", "does not show which repo", env={"GH_REPO": "not a repo"})


class MergeThroughTheApi(Base):
    def test_a_write_to_the_merge_address_is_refused(self):
        for command in (
            "gh api -X PUT repos/%s/pulls/5/merge" % GUEST,
            "gh api -XPUT /repos/%s/pulls/5/merge" % GUEST,
            "gh api --method PUT repos/%s/pulls/5/merge -f merge_method=squash" % GUEST,
            "gh api --method put repos/%s/pulls/5/merge" % GUEST,
            "gh api repos/%s/pulls/5/merge -f merge_method=squash" % GUEST,
            "gh api repos/%s/merges -f base=main -f head=feature" % GUEST,
            "gh api repos/%s/pulls/5/merge --input body.json" % GUEST,
            "curl -s -X PUT https://api.github.com/repos/%s/pulls/5/merge" % GUEST,
            "curl -s https://api.github.com/repos/%s/pulls/5/merge -d '{}'" % GUEST,
            "curl --request PUT https://api.github.com/repos/%s/pulls/5/merge" % NAMED,
        ):
            with self.subTest(command=command):
                self.refused(command, "keeps its own review process")

    def test_a_read_of_the_same_address_and_a_write_elsewhere_are_allowed(self):
        for command in (
            "gh api repos/%s/pulls/5/merge" % GUEST,
            "gh api repos/%s/pulls/5" % GUEST,
            "gh api repos/%s/pulls/5/merge --jq .message" % GUEST,
            "curl -s https://api.github.com/repos/%s/pulls/5/merge" % GUEST,
            "gh api -X PUT repos/%s/pulls/5/merge" % OWN,
            "gh api repos/%s/issues/5/comments -f body='please merge when reviewed'" % GUEST,
        ):
            with self.subTest(command=command):
                self.allowed(command, cwd=self.guest)

    def test_a_graphql_merge_is_refused_in_any_repo(self):
        for command in (
            "gh api graphql -f query='%s'" % (MERGE_QUERY % "mergePullRequest"),
            "gh api graphql -f query='%s'" % (MERGE_QUERY % "enablePullRequestAutoMerge"),
            "gh api graphql -F query=@merge.graphql -f note=mergeBranch",
            "curl -s https://api.github.com/graphql -d '{\"query\": \"mutation { mergePullRequest }\"}'",
        ):
            with self.subTest(command=command):
                self.refused(command, "GraphQL")

    def test_a_graphql_read_is_allowed(self):
        self.allowed("gh api graphql -f query='query { repository(owner: \"example-org\", name: \"their-app\") "
                     "{ pullRequest(number: 5) { mergeable mergeStateStatus } } }'", cwd=self.guest)


class RepoCannotBeRead(Base):
    def test_a_merge_whose_repo_is_unknown_is_refused(self):
        for command, cwd in (
            ('gh pr merge 5 -R "$REPO"', "OWN"),
            ("gh pr merge 5 -R $OWNER/$NAME", "OWN"),
            ("gh pr merge 5 -R example-owner/guest-$SUFFIX", "OWN"),
            ("gh pr merge 5 --repo=$REPO", "OWN"),
            ("GH_REPO=$REPO gh pr merge 5", "OWN"),
            ('cd "$W" && gh pr merge 5', "OWN"),
            ("cd $W && gh pr merge 5", "OWN"),
            ("cd - && gh pr merge 5", "OWN"),
            ("gh api -X PUT repos/$O/$R/pulls/5/merge", "OWN"),
            ("gh api -X PUT repos/{owner}/{repo}/pulls/5/merge", "OWN"),
            ("gh pr merge 5", self.plain),
            ("gh pr merge 5", None),
            ("cd somewhere && gh pr merge 5", None),
        ):
            with self.subTest(command=command, cwd=cwd):
                self.refused(command, "does not show which repo", "gh pr merge N -R OWNER/REPO", cwd=cwd)

    def test_a_missing_list_refuses_every_merge(self):
        for command in ("gh pr merge 5 -R %s" % OWN, "gh pr merge 5", "gh api -X PUT repos/%s/pulls/5/merge" % OWN):
            with self.subTest(command=command):
                self.refused(command, "own-review list is missing or unreadable", home=self.no_list_home)

    def test_a_missing_list_stops_nothing_else(self):
        for command in ("ls -la", "gh pr view 5", "git status", "git push origin feature"):
            with self.subTest(command=command):
                self.allowed(command, home=self.no_list_home, cwd=self.guest)

    def test_a_list_with_no_repo_lines_refuses_nothing(self):
        self.allowed("gh pr merge 5 -R %s" % GUEST, home=self.empty_list_home)


class NotAMerge(Base):
    def test_everyday_commands_in_a_guest_folder_are_allowed(self):
        for command in (
            "gh pr view 5",
            "gh pr list --state merged",
            "gh pr checks 5 --watch",
            "gh pr view 5 --json mergeable,mergeStateStatus,mergeCommit",
            'gh pr create --title "Merge the two lists" --body "ready to merge after review"',
            'gh pr comment 5 --body "ready to merge when your team has reviewed it"',
            "gh pr review 5 --approve -R %s" % GUEST,
            "gh pr ready 5",
            "gh search prs --owner example-org --merged",
            "git merge main",
            "git fetch origin && git merge origin/main",
            "git log --oneline --merges -5",
            "python3 ~/.claude/scripts/lane_merge.py -R %s 5 --check" % GUEST,
            "grep -rn merge .",
            "ls -la",
            "",
        ):
            with self.subTest(command=command):
                self.allowed(command, cwd=self.guest)

    def test_other_tools_and_unreadable_input_are_allowed(self):
        for payload in (
            json.dumps({"tool_name": "Read", "tool_input": {"file_path": "gh pr merge 5 -R %s" % GUEST}}),
            json.dumps({"tool_name": "Bash", "tool_input": {"command": ["gh", "pr", "merge"]}}),
            json.dumps({"tool_name": "Bash", "tool_input": "gh pr merge"}),
            json.dumps({"tool_name": "Bash"}),
            json.dumps(["gh pr merge"]),
            "not json",
            "",
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.run_hook(None, payload=payload), (0, ""))


class PushToMain(Base):
    def test_a_push_to_main_of_a_guest_repo_is_refused(self):
        for command in (
            "git push",
            "git push origin",
            "git push origin main",
            "git push origin master",
            "git push origin HEAD:main",
            "git push origin feature:refs/heads/main",
            "git push origin +main",
            "git push origin :main",
            "git push --force origin main",
            "git push --all",
            "git push --mirror origin",
            "git push -u origin HEAD",
            "git commit -m done && git push",
        ):
            with self.subTest(command=command):
                self.refused(command, GUEST, "Nobody here pushes to its main branch", "not the hub", cwd=self.guest)

    def test_on_a_feature_branch_only_a_push_that_names_main_is_refused(self):
        for command in ("git push", "git push origin feature", "git push -u origin HEAD", "git push origin HEAD",
                        "git push --force-with-lease origin feature"):
            with self.subTest(command=command):
                self.allowed(command, cwd=self.guest_feature)
        for command in ("git push origin main", "git push origin feature:main", "git push --all"):
            with self.subTest(command=command):
                self.refused(command, GUEST, cwd=self.guest_feature)

    def test_git_dash_c_reads_the_folder_it_names(self):
        self.refused("git -C %s push origin main" % self.guest, GUEST)
        self.refused("git -C %s push" % self.guest, GUEST)
        self.allowed("git -C %s push origin main" % self.own, cwd=self.guest)
        self.refused("cd %s && git push origin main" % self.guest, GUEST)

    def test_pushes_that_do_not_reach_a_guest_main_are_allowed(self):
        for command, cwd in (
            ("git push origin main", "OWN"),
            ("git push", "OWN"),
            ("git push --tags", self.guest),
            ("git push origin v1.0.0", self.guest),
            ("git push origin HEAD:feature", self.guest),
            ("git push origin main-menu-fix", self.guest),
            ("git stash push -m wip", self.guest),
            ("git config push.default simple", self.guest),
            ("git log --grep push", self.guest),
            ('git commit -m "push to main"', self.guest),
        ):
            with self.subTest(command=command, cwd=cwd):
                self.allowed(command, cwd=cwd)


if __name__ == "__main__":
    unittest.main()
