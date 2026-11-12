#!/usr/bin/env python3
"""Claude Code PreToolUse hook (Bash): no session merges in a repo that keeps its own review.

~/.config/afa/own-review-repos.txt lists repos where we are a guest on someone else's team,
and that team does the reviewing. Nobody here merges to their main branch, not a lane and
not the hub. lane_merge.py already refuses those repos; this hook refuses the plain commands
a session could type instead. The list is private: one pattern per line (OWNER/REPO or
OWNER/*, any letter case, a line starting with # is a comment).

stdin: {"tool_name": "Bash", "tool_input": {"command": ...}, "cwd": ...}
block: reason on stderr, exit 2.  allow: exit 0.  The reason never echoes the command.

Refused when the repo matches the list:
  - gh pr merge, with any flag (--auto, --squash, --admin and the rest);
  - gh api, or curl to the GitHub API, that writes to repos/OWNER/REPO/pulls/N/merge or
    repos/OWNER/REPO/merges (a plain read of the same address is allowed);
  - git push to main or master: named in the command, pushed with --all or --mirror, or the
    branch the checkout is on.
Refused whatever the repo, because the repo cannot be read from the command:
  - gh api graphql with a merge mutation (mergePullRequest, enablePullRequestAutoMerge,
    mergeBranch, enqueuePullRequest).

Which repo: -R or --repo, GH_REPO=, or a github.com address in the same command part; with
none of those, every GitHub remote of the session's folder and of each folder the command
changes into (cd, pushd, git -C), plus GH_REPO when the session's own environment sets it.
A merge fails closed: it is refused when the list file is
missing or unreadable, when the repo is written as a variable, when a folder change goes
through a variable, and when no repo can be found at all. The fix for those is to name the
repo: gh pr merge N -R OWNER/REPO. A push is only refused when the repo is known to match.

Honest limit: this reads the command's text. A script file that runs the merge, a gh alias,
or a mutation read from a file is not seen. Branch protection on the other team's repo and
lane_merge.py's own refusal are the other layers.

Unreadable or non-JSON stdin, or a tool other than Bash: allow. An unexpected error while
checking a command already recognised as a merge refuses it; anywhere else it allows after
one line on stderr, so a fault here cannot stop every shell call.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import time

LIST_PATH = os.path.expanduser("~/.config/afa/own-review-repos.txt")
HOME = os.path.expanduser("~")
MAX_FOLDERS = 8
STARTED = time.monotonic()
GIT_BUDGET = 3.5  # seconds for every git call together; the hook's own timeout in settings is 5

QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
SEPARATOR = re.compile(r"[\n;|&]")
SEG = r"[^\n;|&]*?"
GH = r"(?<![\w.-])gh(?:\.exe)?(?![\w.-])"


def word(text):
    return r"(?<![\w-])" + text + r"(?![\w-])"


PR_MERGE = re.compile(GH + SEG + word("pr") + SEG + word("merge"))
PR_MERGE_QUOTED = re.compile(GH + r"\s+pr\s+merge(?![\w-])")
GH_API = re.compile(GH + SEG + word("api"))
CURL = re.compile(r"(?<![\w.-])(?:curl|wget|http|https|xh)(?![\w.-])")
# push must be git's own subcommand (after options such as -C FOLDER), so "git stash push" and
# "git config push.default" are not pushes.
GIT_PUSH = re.compile(
    r"(?<![\w.-])git(?:\.exe)?(?:[ \t]+(?:-[Cc][ \t]+\S+|--?[\w-]+(?:=\S+)?))*[ \t]+push(?![\w.-])"
)

# A repo value must end where the word ends: "owner/name$SUFFIX" is a variable, so it is unknown.
REPO = r"[\"']?(?:https?://)?(?:[\w.-]+/)?([\w.-]+)/([\w.-]+)(?=[\s\"';&|)]|$)"
# -R can ride at the end of a cluster of short flags (-sdR OWNER/REPO) or touch its value (-ROWNER/REPO).
REPO_FLAG = re.compile(r"(?<![\w-])-[A-Za-z]*R|(?<![\w-])--repo(?![\w-])")
REPO_FLAG_VALUE = re.compile(r"(?:-[A-Za-z]*R\s*=?\s*|--repo(?:\s+|=))" + REPO)
GH_REPO = re.compile(r"(?<!\w)GH_REPO=")
GH_REPO_VALUE = re.compile(r"GH_REPO=" + REPO)
REPO_ALONE = re.compile(REPO)
URL = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?=[/\s\"'#?]|$)")
MERGE_PATH = re.compile(r"(?:pulls/[^/\s\"']+/merge|(?<=/)merges)(?![\w/-])")
MERGE_PATH_REPO = re.compile(r"repos/([\w.-]+)/([\w.-]+)/(?:pulls/[^/\s\"']+/merge|merges)(?![\w/-])")
WRITE_METHOD = re.compile(r"(?:(?<![\w-])-X|--method|--request)[\s=]*[\"']?(?:PUT|POST|PATCH|DELETE)\b", re.I)
WRITE_FLAG = re.compile(
    r"(?<![\w-])(?:-f|-F|-d|--field|--raw-field|--input|--data|--data-raw|--data-binary|--json|--post-data|--body-data)(?![\w-])"
)
MUTATION = re.compile(r"mergePullRequest|enablePullRequestAutoMerge|mergeBranch|enqueuePullRequest")
WORD_SPLIT = re.compile(r"[\s\"';&|()<>=,`]+")
MAIN = ("main", "master")

NOBODY = "Nobody here merges in it, not a lane and not the hub. Ask for a review the way that repo's own team does."


class Unknown(Exception):
    """The repo a merge would act on cannot be read from the command."""


def read_patterns():
    try:
        with open(LIST_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def on_list(repos, patterns):
    """(repo, pattern) for the first repo that matches the list, else None."""
    for repo in sorted(repos):
        for pat in patterns:
            if fnmatch.fnmatchcase(repo.lower(), pat.lower()):
                return repo, pat
    return None


def segment(text, start):
    """The command part (between ; | & and line ends) that holds position start."""
    left = 0
    for m in SEPARATOR.finditer(text, 0, start):
        left = m.end()
    m = SEPARATOR.search(text, start)
    return left, (m.start() if m else len(text))


def named_repos(part):
    """Repos named in one command part by -R, --repo, GH_REPO= or a github.com address. Raises Unknown for a variable."""
    repos = set()
    for m in REPO_FLAG.finditer(part):
        v = REPO_FLAG_VALUE.match(part, m.start())
        if not v:
            raise Unknown()
        repos.add(v.group(1) + "/" + v.group(2))
    for m in GH_REPO.finditer(part):
        v = GH_REPO_VALUE.match(part, m.start())
        if not v:
            raise Unknown()
        repos.add(v.group(1) + "/" + v.group(2))
    for m in URL.finditer(part):
        repos.add(m.group(1) + "/" + m.group(2))
    return repos


def expand(text):
    if text == "~" or text.startswith("~/"):
        text = HOME + text[1:]
    return text.replace("${HOME}", HOME).replace("$HOME", HOME)


def folders(command, cwd):
    """(folders the command runs in, True when one of them is written as a variable)."""
    out = []
    unknown = False
    if isinstance(cwd, str) and os.path.isabs(cwd):
        out.append(os.path.normpath(cwd))
    words = [w for w in WORD_SPLIT.split(command) if w]
    for i, w in enumerate(words):
        if w not in ("cd", "pushd", "-C"):
            continue
        j = i + 1
        while w != "-C" and j < len(words) and words[j].startswith("-") and words[j] != "-":
            j += 1
        if j >= len(words):
            continue
        target = expand(words[j])
        if "$" in target or target == "-":
            unknown = True
            continue
        new = [os.path.normpath(target)] if os.path.isabs(target) else [os.path.normpath(os.path.join(b, target)) for b in out]
        if not new:
            unknown = True
        for folder in new:
            if folder not in out:
                out.append(folder)
    if len(out) > MAX_FOLDERS:
        return out[:MAX_FOLDERS], True
    return out, unknown


def git(folder, *args):
    # Claude Code stops a hook at its timeout and then lets the call through, so every git call
    # shares one budget well inside it. Out of budget reads as "no repo found", which refuses a merge.
    left = GIT_BUDGET - (time.monotonic() - STARTED)
    if left <= 0:
        return ""
    try:
        r = subprocess.run(["git", "-C", folder] + list(args), capture_output=True, text=True, timeout=min(2, left),
                           env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout if r.returncode == 0 else ""


def remote_repos(folder_list):
    repos = set()
    for folder in folder_list:
        for m in URL.finditer(git(folder, "remote", "-v")):
            repos.add(m.group(1) + "/" + m.group(2))
    return repos


def merge_verdict(part, command, cwd, patterns, extra=()):
    """A reason to refuse one merge command part, or None. Raises Unknown when the repo cannot be read."""
    repos = named_repos(part) | set(extra)
    if not repos:
        if GH_REPO.search(command):
            repos = named_repos(command)
        else:
            folder_list, unknown = folders(command, cwd)
            if unknown:
                raise Unknown()
            repos = remote_repos(folder_list)
            # gh reads GH_REPO from the session's own environment before it looks at the folder.
            inherited = os.environ.get("GH_REPO", "").strip()
            if inherited:
                m = REPO_ALONE.fullmatch(inherited)
                if not m:
                    raise Unknown()
                repos.add(m.group(1) + "/" + m.group(2))
    if not repos:
        raise Unknown()
    hit = on_list(repos, patterns)
    if hit:
        return "BLOCKED: %s keeps its own review process (own-review list: %s). %s" % (hit[0], hit[1], NOBODY)
    return None


def check_merges(command, cwd):
    """A reason to refuse, or None. Every command part that merges is checked; any doubt refuses."""
    plain = QUOTED.sub(lambda m: "Q" * len(m.group(0)), command)
    parts = []  # (kind, text of the command part)
    for m in PR_MERGE.finditer(plain):
        a, b = segment(plain, m.start())
        parts.append(("pr", command[a:b]))
    # Inside quotes (bash -c 'gh pr merge 5'), and with quote marks or backslashes around a word ("gh" pr merge).
    bare = re.sub(r"[\"'\\]", "", command)
    for text in (command, bare):
        for m in PR_MERGE_QUOTED.finditer(text):
            a, b = segment(text, m.start())
            parts.append(("pr", text[a:b]))
    for m in list(GH_API.finditer(plain)) + list(CURL.finditer(plain)):
        a, b = segment(plain, m.start())
        part = command[a:b]
        if re.search(word("graphql"), part) and MUTATION.search(part):
            parts.append(("graphql", part))
        elif MERGE_PATH.search(part) and (WRITE_METHOD.search(part) or WRITE_FLAG.search(part)):
            parts.append(("api", part))
    if not parts:
        return None
    try:
        patterns = read_patterns()
        if patterns is None:
            return ("BLOCKED: the own-review list is missing or unreadable, so no session merges until a person puts it "
                    "back (it lives in the private config folder; lane_merge.py names the path).")
        for kind, part in parts:
            if kind == "graphql":
                return ("BLOCKED: a merge sent as a GraphQL mutation does not show which repo it acts on, so it is refused "
                        "everywhere. Use gh pr merge N -R OWNER/REPO, which this guard can read.")
            extra = set()
            if kind == "api":
                found = MERGE_PATH_REPO.findall(part)
                if len(found) != len(MERGE_PATH.findall(part)):
                    raise Unknown()
                extra = {o + "/" + r for o, r in found}
            reason = merge_verdict(part, command, cwd, patterns, extra)
            if reason:
                return reason
    except Unknown:
        return ("BLOCKED: this merge does not show which repo it acts on (a variable, a folder change this guard cannot "
                "follow, or no repo at all). Name it in the command: gh pr merge N -R OWNER/REPO.")
    except Exception as exc:
        return "BLOCKED: own-review-merge-guard could not check this merge (%s); it refuses rather than guess." % type(exc).__name__
    return None


def check_push(command, cwd):
    """A reason to refuse a push to main or master of an own-review repo, or None. Refuses only what it can prove."""
    plain = QUOTED.sub(lambda m: "Q" * len(m.group(0)), command)
    for m in GIT_PUSH.finditer(plain):
        a, b = segment(plain, m.start())
        part = command[a:b]
        patterns = read_patterns()
        if not patterns:
            return None
        # git -C FOLDER push acts on that folder alone; otherwise every folder the command runs in counts.
        own_folders, _ = folders(command[a:m.end()], None)
        folder_list = own_folders or folders(command, cwd)[0]
        repos = {o + "/" + r for o, r in URL.findall(part)} | remote_repos(folder_list)
        hit = on_list(repos, patterns)
        if not hit:
            continue
        args = [w for w in WORD_SPLIT.split(command[m.end():b]) if w]
        positional = [w.lstrip("+") for w in args if not w.startswith("-")]
        to_main = any(w in ("--all", "--mirror") for w in args)
        if "--tags" in args and len(positional) < 2 and not to_main:
            continue
        current = False
        for w in positional[1:] or ["HEAD"]:
            dest = w.rsplit(":", 1)[-1]
            if dest.rsplit("/", 1)[-1] in MAIN:
                to_main = True
            elif dest == "HEAD" or "$" in w:
                current = True
        if len(positional) == 1 and URL.search(positional[0]) is None and positional[0] in MAIN:
            to_main = True
        if current and not to_main:
            for folder in folder_list:
                if git(folder, "symbolic-ref", "--short", "-q", "HEAD").strip() in MAIN:
                    to_main = True
        if to_main:
            return ("BLOCKED: %s keeps its own review process (own-review list: %s). Nobody here pushes to its main branch, "
                    "not a lane and not the hub. Push a branch and ask for a review the way that repo's own team does."
                    % hit)
    return None


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0
    cwd = payload.get("cwd")
    reason = check_merges(command, cwd)
    if not reason:
        try:
            reason = check_push(command, cwd)
        except Exception as exc:
            print("own-review-merge-guard internal error: %s" % type(exc).__name__, file=sys.stderr)
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
