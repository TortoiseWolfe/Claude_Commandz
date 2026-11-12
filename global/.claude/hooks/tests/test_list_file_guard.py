"""list-file-guard.py refuses a session's edits to the two merge list files.

Every case runs the hook as a subprocess with a temporary HOME, so no real file is touched.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "list-file-guard.py"
NAMES = ("merge-repos.txt", "own-review-repos.txt")


@pytest.fixture()
def home(tmp_path):
    afa = tmp_path / ".config" / "afa"
    afa.mkdir(parents=True)
    for name in NAMES + ("machine",):
        (afa / name).write_text("example-owner/example-repo\n")
    return tmp_path


def run(home, payload, raw=None):
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    text = raw if raw is not None else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=text, env=env,
                          capture_output=True, text=True, cwd=str(home))


def call(home, tool, **tool_input):
    return run(home, {"tool_name": tool, "tool_input": tool_input})


def bash(home, command):
    return call(home, "Bash", command=command)


def assert_blocked(res):
    assert res.returncode == 2, res.stderr
    assert res.stderr.startswith("BLOCKED:")


def assert_allowed(res):
    assert res.returncode == 0, res.stderr


def path_input(tool, path):
    return {"notebook_path": path} if tool == "NotebookEdit" else {"file_path": path}


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
@pytest.mark.parametrize("name", NAMES)
class TestEditTools:
    def test_plain_path(self, home, tool, name):
        assert_blocked(call(home, tool, **path_input(tool, str(home / ".config/afa" / name))))

    def test_tilde_path(self, home, tool, name):
        assert_blocked(call(home, tool, **path_input(tool, f"~/.config/afa/{name}")))

    def test_dotdot_path(self, home, tool, name):
        assert_blocked(call(home, tool, **path_input(tool, f"{home}/notes/../.config/afa/{name}")))

    def test_symlink_to_file(self, home, tool, name):
        link = home / "innocent.txt"
        link.symlink_to(home / ".config/afa" / name)
        assert_blocked(call(home, tool, **path_input(tool, str(link))))

    def test_symlinked_folder(self, home, tool, name):
        link = home / "afa-link"
        link.symlink_to(home / ".config/afa")
        assert_blocked(call(home, tool, **path_input(tool, str(link / name))))


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_neighbours_allowed(home, tool):
    other = home / "elsewhere"
    other.mkdir()
    for path in ("~/.config/afa/machine", "~/.config/afa/merge-repos.proposed.txt",
                 str(other / "merge-repos.txt")):
        assert_allowed(call(home, tool, file_path=path))


@pytest.mark.parametrize("tool_input", [{}, {"file_path": None}, {"file_path": 5}, {"file_path": ""}])
def test_missing_or_bad_path_allowed(home, tool_input):
    assert_allowed(call(home, "Write", **tool_input))


@pytest.mark.parametrize("command", [
    "echo example-owner/x >> ~/.config/afa/merge-repos.txt",
    "sed -i 's/a/b/' ~/.config/afa/own-review-repos.txt",
    "cp /tmp/new ~/.config/afa/merge-repos.txt",
    "echo hi | tee -a $HOME/.config/afa/own-review-repos.txt",
    "python3 -c \"open('/x/.config/afa/merge-repos.txt','w')\"",
    "cat ~/.config/afa/merge-repos.txt",
    "ls ~/.config/afa/merge-re*.txt",
    "cat $HOME/.config/afa/*",
    "cat ~/.config/afa/own-review-r?pos.txt",
    "cat ~/.config/afa/[mo]*",
    "mv ~/.config/afa ~/afa-old",
    "mv ~/.config/afa/ ~/afa-old",
    "rm -rf ~/.config/afa",
    "rm -rf \"$HOME/.config/afa\"",
    "ln -s ~/elsewhere ~/.config/afa",
    "cd /x && rm -r ~/.config/afa; ls",
])
def test_bash_blocked(home, command):
    assert_blocked(bash(home, command))


@pytest.mark.parametrize("command", [
    "cat ~/.config/afa/machine",
    "ls ~/.config/afa",
    "ls ~/.config/afa/",
    "ls ~/notes/*.txt",
    "ls $HOME/.config/other/*",
    "python3 ~/.claude/scripts/lane_merge.py 12 -R example-owner/example-repo",
    "gh pr merge 12 --squash",
    "git log --oneline",
])
def test_bash_allowed(home, command):
    assert_allowed(bash(home, command))


def test_read_tool_allowed(home):
    assert_allowed(call(home, "Read", file_path=str(home / ".config/afa/merge-repos.txt")))


@pytest.mark.parametrize("tool", ["Grep", "Glob", "WebFetch", "Task", "mcp__example__tool"])
def test_other_tools_allowed(home, tool):
    assert_allowed(call(home, tool, file_path="~/.config/afa/merge-repos.txt",
                        command="cat ~/.config/afa/merge-repos.txt"))


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", "null", '{"tool_name": "Bash"}',
                                 '{"tool_name": "Bash", "tool_input": "x"}',
                                 '{"tool_name": "Bash", "tool_input": {"command": 5}}'])
def test_malformed_stdin_allowed(home, raw):
    assert_allowed(run(home, None, raw=raw))


def test_reason_never_echoes_the_command(home):
    marker = "zq-unique-marker-77"
    res = bash(home, f"echo {marker} >> ~/.config/afa/merge-repos.txt")
    assert_blocked(res)
    assert marker not in res.stderr
    assert "echo" not in res.stderr
    assert "merge-repos.txt" in res.stderr
    folder = bash(home, f"mv ~/.config/afa /tmp/{marker}")
    assert_blocked(folder)
    assert marker not in folder.stderr
