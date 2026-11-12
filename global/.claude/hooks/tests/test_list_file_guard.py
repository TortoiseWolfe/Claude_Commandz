"""list-file-guard.py refuses a session's edits to the two merge list files.

Every case runs the hook as a subprocess with a temporary HOME, so no real file is touched.
"""
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "list-file-guard.py"
NAMES = ("merge-repos.txt", "own-review-repos.txt")
# The account the tests run as; a shell reads "~<that name>" as the home folder.
ME = pwd.getpwuid(os.getuid()).pw_name


@pytest.fixture()
def home(tmp_path):
    afa = tmp_path / ".config" / "afa"
    afa.mkdir(parents=True)
    for name in NAMES + ("machine",):
        (afa / name).write_text("example-owner/example-repo\n")
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "list-file-guard.py").write_text("# installed copy\n")
    (hooks / "secret-guard.py").write_text("# other hook\n")
    (tmp_path / ".config" / "other").mkdir()
    (tmp_path / "notes").mkdir()
    return tmp_path


def run(home, payload, raw=None):
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    text = raw if raw is not None else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=text, env=env,
                          capture_output=True, text=True, cwd=str(home))


def call(home, tool, **tool_input):
    return run(home, {"tool_name": tool, "tool_input": tool_input})


def bash(home, command, cwd=None):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return run(home, payload)


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
    own = bash(home, f"cp /tmp/{marker} ~/.claude/hooks/list-file-guard.py")
    assert_blocked(own)
    assert marker not in own.stderr
    assert "cp" not in own.stderr.split("BLOCKED:")[1].split()


# ---- the rewrite: session folder, folder changes, wildcards anywhere, own copy ----

@pytest.mark.parametrize("command", [
    "cat merge-*", "rm *", "cat m*", "rm -rf .", "ls own-review-r?pos.txt",
])
def test_cwd_inside_list_folder_blocked(home, command):
    assert_blocked(bash(home, command, cwd=home / ".config" / "afa"))


@pytest.mark.parametrize("command", [
    "cd ~/.config/afa && cat m*",
    "cd ~/.config && mv afa afa-old",
    "cd ~/.config && rm -r afa",
    "pushd ~/.config/afa; sed -i s/a/b/ [mo]*",
    "cd $HOME/.config/afa && cat *",
])
def test_folder_change_blocked(home, command):
    assert_blocked(bash(home, command, cwd=home))


@pytest.mark.parametrize("command", [
    "cat ~/.conf*/afa/merge*",
    "cat ~/.config/a*/*",
    "ls ~/.config/*/own-review-*",
])
def test_wildcard_in_folder_part_blocked(home, command):
    assert_blocked(bash(home, command))


def test_braces_blocked(home):
    assert_blocked(bash(home, "cat ~/.config/afa/{merge,own-review}-repos.{txt,bak}"))


@pytest.mark.parametrize("command", ["cat ~/afa-link/m*", "rm -r ~/afa-link"])
def test_symlink_to_folder_blocked(home, command):
    (home / "afa-link").symlink_to(home / ".config" / "afa")
    assert_blocked(bash(home, command))


@pytest.mark.parametrize("name", NAMES)
def test_symlink_to_list_file_blocked(home, name):
    (home / "innocent.txt").symlink_to(home / ".config" / "afa" / name)
    assert_blocked(bash(home, "cat ~/innocent.txt"))


def test_variable_holding_folder_blocked(home):
    assert_blocked(bash(home, "d=~/.config/afa; rm -r $d"))


@pytest.mark.parametrize("command", [
    "mv ~/.config ~/config-old",
    "rm -rf ~/.claude/hooks",
    "chmod -R 000 ~/.config",
    "cd ~ && mv .config x",
])
def test_parent_folders_blocked(home, command):
    assert_blocked(bash(home, command, cwd=home))


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_own_copy_edit_tools_blocked(home, tool):
    assert_blocked(call(home, tool, file_path="~/.claude/hooks/list-file-guard.py"))


@pytest.mark.parametrize("command", [
    "cp /tmp/x ~/.claude/hooks/list-file-guard.py",
    "cd ~/.claude/hooks && rm list-file-g*",
    "sed -i s/a/b/ ~/.claude/hooks/list-file-guard.py",
])
def test_own_copy_bash_blocked(home, command):
    assert_blocked(bash(home, command, cwd=home))


def test_find_delete_in_list_folder_blocked(home):
    assert_blocked(bash(home, "find ~/.config/afa -type f -delete"))


def test_too_many_folder_changes_blocked(home):
    command = " && ".join(f"cd d{i}" for i in range(30))
    res = bash(home, command, cwd=home)
    assert_blocked(res)
    assert "d29" not in res.stderr


@pytest.mark.parametrize("command", [
    "cat ~/.config/afa/machine",
    "ls ~/.config/afa",
    "cd ~/.config/afa && cat machine",
    "cp notes.txt ~/.config/other/",
    "ls ~/.claude/hooks",
    "cat ~/.claude/hooks/secret-guard.py",
    "find ~/notes -name x",
    "mv notes.txt ~/.config/other/notes.txt",
    "ls *.txt",
    "rm *.txt",
])
def test_rewrite_allowed(home, command):
    assert_allowed(bash(home, command, cwd=home / "notes"))


# ---- the security review of the rewrite: five ways past it, each closed ----

@pytest.mark.parametrize("command", [
    "cd && rm -r .config/afa",
    "cd; rm -r .config",
    "cd\nmv .config x",
    "cd -- && cat .config/afa/m*",
    "(cd) ; rm -rf .claude/hooks",
    "pushd && rm -r .config/afa",
    "cd",
    "cd \"$SOMEWHERE\" && cat .config/afa/m*",
])
def test_cd_with_no_folder_means_home(home, command):
    """(1) cd or pushd with nothing after it goes home, so relative words are checked against the home folder."""
    res = bash(home, command, cwd=home / "notes")
    if command == "cd":
        assert_allowed(res)
    else:
        assert_blocked(res)


def test_no_session_folder_checks_home(home):
    """(1) With no folder in the payload, relative words are still checked against the home folder."""
    assert_blocked(bash(home, "rm -r .config/afa"))
    assert_blocked(bash(home, "cat .config/afa/m*"))


@pytest.mark.parametrize("command", [
    "git -C ~/.config/afa rm m*",
    "tar -C ~/.config -xf x.tar afa/m*",
    "make -C ~/.config/afa own-review-r?pos.txt",
])
def test_dash_c_folder_change_blocked(home, command):
    assert_blocked(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "echo a\x00b; rm -r ~/.config/afa",
    "rm -r ~/.config/afa\x00",
    "rm -r ~/.config/afa\x00junk",
    "rm -r ~/.con\x00fig/afa",
    "cat ~/.config/afa/m\x01* ; echo \x00",
    "cat ~/.config/afa/merge-re\x00pos.txt",
    "echo \x00\x01\x02\x1f\x7f; mv ~/.config ~/x",
])
def test_odd_characters_do_not_switch_the_checks_off(home, command):
    """(2) A word holding a NUL or another control character used to make the whole path check allow."""
    res = bash(home, command)
    assert_blocked(res)
    assert "internal error" not in res.stderr


def test_one_unreadable_word_is_skipped_and_the_rest_checked(home, tmp_path):
    """(2) A fault while checking one word skips that word only; the next word is still checked."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('guard', {str(HOOK)!r})\n"
        "guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)\n"
        "real = guard.check_word\n"
        "def flaky(word, *rest):\n"
        "    if word == 'boom':\n"
        "        raise ValueError('made-up fault')\n"
        "    return real(word, *rest)\n"
        "guard.check_word = flaky\n"
        "print(guard.check_bash(sys.argv[1], sys.argv[2]))\n"
    )
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}

    def probe_run(command):
        return subprocess.run([sys.executable, str(probe), command, str(home)], env=env,
                              capture_output=True, text=True)

    hit = probe_run("echo boom; rm -r ~/.config/afa")
    assert hit.stdout.strip() == ".config/afa", hit.stderr
    assert hit.stderr.count("internal error: ValueError") == 1
    miss = probe_run("echo boom boom; ls ~/notes")
    assert miss.stdout.strip() == "None", miss.stderr
    assert miss.stderr.count("internal error") == 1


@pytest.mark.parametrize("command", [
    "/bin/rm -rf ~/.config/afa",
    "/usr/bin/mv ~/.config/afa ~/afa-old",
    "./rm -r ~/.config/afa",
    "\\rm -rf ~/.config/afa",
    "\"rm\" -rf ~/.config/afa",
    "r''m -rf ~/.config/afa",
    "command rm -rf ~/.config",
    "/usr/bin/find ~/.config/afa -type f -delete",
    "/bin/chmod -R 000 ~/.claude/hooks",
    "sudo /bin/ln -sfn /tmp/x ~/.config/afa",
])
def test_verb_written_with_a_path_blocked(home, command):
    """(3) /bin/rm is still rm."""
    assert_blocked(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "docker run --rm example ls ~/.config/afa",
    "ls ~/.config/afa ~/notes/x.rm",
    "cat ~/notes/rm.txt ~/.config/afa/machine",
    "ls ~/.config/afa # confirm before the next step",
])
def test_a_verb_inside_another_word_is_not_a_verb(home, command):
    assert_allowed(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "rm -r ~/.config/\"afa\"",
    "rm -r ~/.config/'afa'",
    "rm -r ~/.con\"fig\"/a'f'a",
    "rm -r ~/.config/a\\fa",
    "cat ~/.config/\"afa\"/m*",
    "cat ~/.config/'afa'/[mo]*",
    "cat ~/.config/afa/merge-re\"pos\".txt",
    "cat ~/.config/afa/own-review-re\\pos.txt",
    "cat \"$HOME\"/.config/afa/*",
    "cat \"$HOME/.config\"/'afa'/m*",
    "sed -i s/a/b/ ~/.claude/\"hooks\"/list-file-guard.py",
    "cd ~/.config/\"afa\" && cat m*",
])
def test_path_glued_from_quoted_pieces_blocked(home, command):
    """(4) A shell takes the quotes out, so ~/.config/"afa" is ~/.config/afa."""
    assert_blocked(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "cat ~/.config/{afa/merge-rep?s,other/x}.txt",
    "cat ~/.config/afa/{merge,own-review}-repos.{txt,bak}",
    "cat ~/.config/afa/{x,merge}-re{pos,x}.txt",
    "cat ~/{.config/afa,a,b,c,d,e,f,g}/{m,n,o,p,q,r,s,t,u}*",
    "cat ~/{.config/afa,a,b,c,d,e,f,g}/x/../{m,n,o,p,q,r,s,t,u}*",
    "{rm,-rf} ~/.config/afa",
    "cat ~/{.config/afa,notes}/m*",
    "cat {~/.config/afa,/tmp}/m*",
    "cat ~/.config/{afa/m,other/x}*",
    "cat ~/{.config/{afa,other},notes}/m*",
    "rm -r ~/{.config/afa,notes}",
    "rm -r ~/.{config/afa,cache/x}",
    "mv ~/{.config,notes/x} /tmp/",
    "cat ~/.config/{afa/x/..,other}/m*",
    "cat ~/.config/afa/{m,o}*",
    "cat ~/.config/afa/{a..z}*",
    "cat ~/.config/afa/{merge,own-review}-rep?s.{txt,bak}",
])
def test_brace_group_holding_a_slash_blocked(home, command):
    """(5) A brace group with a "/" in it changes how many folders deep the word goes."""
    assert_blocked(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "ls {a,b}/*.txt",
    "ls {a/x,b/y}/*.txt",
    "cat ~/notes/{a,b}.txt",
    "cat ~/.config/other/{a,b}/*",
    "find ~/notes -name x -exec cat {} \\;",
    "echo ${HOME}/notes/*",
    "rm -rf *",
    "rm -rf ~/notes/*",
    "mv * ~/notes/",
    "chmod -R 644 ./*",
])
def test_brace_and_wildcard_everyday_use_allowed(home, command):
    """Everyday wildcards stay allowed, in the notes folder and in the home folder itself (a "*" skips dot folders)."""
    assert_allowed(bash(home, command, cwd=home / "notes"))
    assert_allowed(bash(home, "cd && " + command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "rm -rf ~/.*", "rm -rf ~/.c*", "cd && rm -rf .??*", "chmod 000 ~/.config/a?a", "rm -rf ~/.[!.]*",
    "shopt -s dotglob; rm -rf ~/*",
    "shopt -s dotglob && cd && mv * /tmp/",
    "GLOBIGNORE=x; rm -rf ~/?config",
    "rm -r ${HOME}/.config/afa",
    "rm -rf ~/.config/afa/.",
    "rm -rf ~/.config//afa/../afa/",
])
def test_wildcard_that_names_a_dot_folder_blocked(home, command):
    assert_blocked(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "python3 -m pytest global/.claude/hooks/tests -q",
    "git add global/.claude/hooks/list-file-guard.py",
])
def test_repo_copy_is_not_installed_copy(home, command):
    repo_hook = home / "repo" / "global" / ".claude" / "hooks" / "list-file-guard.py"
    repo_hook.parent.mkdir(parents=True)
    repo_hook.write_text("# repo copy\n")
    assert_allowed(bash(home, command, cwd=home / "repo"))


# ---- the second review: unnamed current folder, shell spellings, false refusals ----

@pytest.mark.parametrize("command", [
    "tar xf x.tar -C ~/.config/afa",
    "tar xf x.tar -C $HOME/.config/afa/",
    "tar xf x.tar -C .config/afa",
    "cd ~/.config/afa && tar xf /tmp/x.tar",
    "pushd .config/afa; unzip /tmp/x.zip",
    "cd ~/.config/afa && find -type f -delete",
    "cd ~/afa-link && tar xf /tmp/x.tar",
])
def test_verb_that_acts_on_the_current_folder_blocked_in_list_folder(home, command):
    """A verb that writes into or walks the folder it runs in never names that folder as a word."""
    (home / "afa-link").symlink_to(home / ".config" / "afa")
    assert_blocked(bash(home, command, cwd=home))


def test_session_folder_is_the_list_folder(home):
    afa = home / ".config" / "afa"
    assert_blocked(bash(home, "tar xf /tmp/x.tar", cwd=afa))
    assert_allowed(bash(home, "ls -la", cwd=afa))
    assert_allowed(bash(home, "cd ~/.config/afa && ls -la", cwd=home))
    assert_allowed(bash(home, "tar xf /tmp/x.tar -C ~/notes", cwd=home))
    assert_allowed(bash(home, "cd ~/notes && tar xf /tmp/x.tar && find . -name x", cwd=home))


@pytest.mark.parametrize("command", [
    "tee ~/.config/afa/m$''erge-repos.txt",
    'tee ~/.config/afa/m$""erge-repos.txt',
    "tee ~/.config/afa/merge\\\n-repos.txt",
    "tee ~/.config/afa/own-review-\\\nrepos.txt",
])
def test_name_split_by_an_empty_quote_or_a_joined_line_blocked(home, command):
    assert_blocked(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "tee ~/.config/afa/@(m|o)*",
    "tee ~/.config/afa/+(m)erge-repos.txt",
    "tee ~/.config/afa/!(x)",
    "cd ~/.config/afa && tee !(x)",
    "tee ~/.config/afa/own-review-r?(e)pos.txt",
    "tee ${HOME:-x}/.config/afa/m*",
    "tee ${HOME#}/.config/afa/m*",
    "tee ${HOME%/}/.config/afa/m*",
    "rm -r ${HOME:?}/.config/afa",
    "tee $HOME/.config/afa/$(echo m)*",
    "tee ~/.config/afa/`echo m`*",
    "tee ~/.config/afa/$(echo $(echo m))erge-rep?s.txt",
])
def test_group_patterns_and_home_forms_blocked(home, command):
    """Extended wildcard groups, a command's output glued into a name, and every ${HOME...} form."""
    assert_blocked(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "rm $(find . -name x)",
    "mv `ls -t | head -1` /tmp/",
    "ls !(x)",
    "echo ${HOMEDIR:-x}/notes/*",
    "cp x ~/notes/$(date +%F).txt",
])
def test_group_patterns_everyday_use_allowed(home, command):
    assert_allowed(bash(home, command, cwd=home))


@pytest.mark.parametrize("command", [
    "touch {a..z}{a..z}",
    "touch {a,b}{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}",
    "echo {a,b}{c,d}{e,f}{g,h}{i,j}{k,l}{m,n}",
    "touch {1..9}{1..9}.txt",
])
def test_many_brace_groups_in_the_home_folder_allowed(home, command):
    """Brace groups side by side stay inside one name; they are not a "**"."""
    assert_allowed(bash(home, command, cwd=home))


@pytest.mark.parametrize("command", [
    "cat ~/.config/afa/{a,b,m}{a,b,e}{a,b,r}{a,b,g}{a,b,e}-rep?s.{txt,bak}",
    "cat ~/.config/afa/{a..z}{a..z}*",
    "rm -r ~/.config/{a..z}{a..z}{a..z}",
])
def test_many_brace_groups_that_reach_the_lists_blocked(home, command):
    assert_blocked(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("count", [5, 6])
def test_a_handful_of_relative_folder_changes_allowed(home, count):
    command = "; ".join(f"cd d{i}" for i in range(count)) + "; ls"
    assert_allowed(bash(home, command, cwd=home / "notes"))
    grouped = "; ".join(f"(cd d{i} && ls)" for i in range(count))
    assert_allowed(bash(home, grouped, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "git commit -m 'document cd ~/.config; mv afa x'",
    "gh pr create --title 'the rm rule' --body 'run rm -rf ~/.config/afa never'",
    'git commit -m "install: cp x ~/.claude/hooks/ then rm the old one"',
    "git commit -am 'chmod ~/.config by hand' && git push",
    "git tag -m 'mv ~/.claude/hooks aside first' v1",
])
def test_message_text_is_read_like_any_other_words(home, command):
    """Message text is not skipped: every way of skipping it also hid a live command (the cases below)."""
    res = bash(home, command, cwd=home / "notes")
    assert_blocked(res)
    assert "put the message in a file" in res.stderr


@pytest.mark.parametrize("command", [
    "git commit -F /tmp/message.txt",
    "gh pr create --title 'the rm rule' --body-file body.md",
    "git commit -m 'Fix rm handling in the cleanup step'",
    'git commit -m "say where the lists live: ~/.config/afa"',
    "git commit -am 'tidy' && git push",
])
def test_everyday_messages_and_message_files_allowed(home, command):
    assert_allowed(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "echo ' git x -m '; rm -rf ~/.config/afa; echo ' '",
    'echo " git x -m "; rm -rf ~/.config/afa; echo " "',
    "git # -m '\nrm -rf ~/.config/afa #'",
    "cat <<EOF\n'\nEOF\necho ' ; git x -m '; rm -rf ~/.config/afa; echo ' '",
    "eval : git y -m '; rm -rf ~/.config/afa'",
    "git y -m 'rm -rf ~/.config/afa'; eval \"$_\"",
    "git bisect run sh -c -m 'rm -rf ~/.config/afa'",
    "export GIT_EDITOR=sh; git commit -e -m 'rm -rf ~/.config/afa'",
    "declare a=(git z -m '; rm -rf ~/.config/afa'); eval : ${a[*]}",
])
def test_a_quote_after_a_message_flag_does_not_hide_a_command(home, command):
    """Each of these runs rm in a real shell: the quote is not where a message starts, or the text is run later."""
    assert_blocked(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    'git commit -m "$(rm -rf ~/.config/afa)"',
    'git commit -m "`rm -rf ~/.config/afa`"',
    "bash -c 'rm -rf ~/.config/afa'",
    "git commit -m 'tidy'; rm -rf ~/.config/afa",
    "git commit -m 'tidy' && cd ~/.config && mv afa x",
    "git commit -m 'about merge-repos.txt'",
    "echo --body 'x'; sh -c -m 'rm -rf ~/.config/afa'",
    "git commit -m 'a' -m 'b' -- ~/.config/afa/m*",
])
def test_message_flag_does_not_hide_a_command(home, command):
    assert_blocked(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "unzip /tmp/x.zip -d ~/.config/afa",
    "unzip /tmp/x.zip -d$HOME/.config/afa",
    "unzip /tmp/x.zip -d.config/afa",
    "cd ~/.config && unzip /tmp/x.zip -d afa",
    "bsdtar -xf /tmp/x.tar --cd ~/.config/afa",
    "cpio -i -D ~/.config/afa",
    "7z x /tmp/x.7z -o$HOME/.config/afa",
    "patch -d ~/.config/afa -i /tmp/p.diff",
    "cd ~/.config/afa && patch -i /tmp/p.diff",
    "cd ~/.config/afa && 7z x /tmp/x.7z",
    "tar xf /tmp/x.tar -C$HOME/.config/afa",
    "tar xf /tmp/x.tar -C.config/afa",
    "cd ~/.config && tar xf /tmp/x.tar -Cafa",
])
def test_unpacking_into_the_list_folder_blocked(home, command):
    """An unpacking tool pointed at the list folder writes the lists without a word that names them."""
    assert_blocked(bash(home, command, cwd=home))


@pytest.mark.parametrize("command", [
    "unzip /tmp/x.zip -d ~/notes",
    "tar xf /tmp/x.tar -C/tmp",
    "tar xf /tmp/x.tar -Cnotes",
    "7z x /tmp/x.7z -o/tmp/out",
    "patch -p1 -i /tmp/p.diff",
    "ls -la ~/.config/afa",
    "grep -rn x ~/.config/afa/machine",
])
def test_unpacking_elsewhere_allowed(home, command):
    assert_allowed(bash(home, command, cwd=home))


@pytest.mark.parametrize("command", [
    f"rm -rf ~{ME}/.config/afa",
    f"tee ~{ME}/.config/afa/m*",
    f"mv ~{ME}/.claude/hooks /tmp/x",
    f"cd ~{ME}/.config && mv afa x",
    "cd ~/.config && rm -rf ~+/afa",
    "cd ~/.config/afa && tee ~+/m*",
    "cd ~/.config/afa && cd .. && rm -rf ~-",
])
def test_other_spellings_of_a_home_or_current_folder_blocked(home, command):
    """A shell reads ~name as that account's home folder, ~+ as the current folder and ~- as the one before."""
    assert_blocked(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    f"ls ~{ME}/notes",
    f"rm -rf ~{ME}/notes/old",
    "ls ~root",
    "ls ~+",
    "cd ~-",
    "rm -rf ~no-such-account-here/.config/afa",
])
def test_other_spellings_everyday_use_allowed(home, command):
    assert_allowed(bash(home, command, cwd=home / "notes"))


@pytest.mark.parametrize("command", [
    "mv ~/.claude/hooks/[a-k]* /tmp",
    "rm ~/.claude/hooks/[0-9]*",
    "rm ~/.config/[x-z]*",
    "rm -rf ~/.[a-b]*",
    "rm -rf ~/.config/af[b-z]/*",
    "rm ~/.claude/hooks/[!l-s]*",
])
def test_bracket_set_that_cannot_match_allowed(home, command):
    assert_allowed(bash(home, command, cwd=home))


@pytest.mark.parametrize("command", [
    "rm ~/.claude/hooks/[k-m]*",
    "rm ~/.config/afa/[!x]*",
    "rm ~/.config/afa/[^x]*",
    "cat ~/.config/afa/[m]erge-rep[o]s.txt",
    "rm -rf ~/.config/[A-C]fa",
    "rm ~/.config/afa/[]m]*",
    "rm ~/.config/afa/[[:alpha:]]*",
    "rm ~/.config/afa/[[:alpha:]][[:alpha:]]rge-rep?s.txt",
    "rm ~/.config/afa/[z-a]*",
    "rm ~/.config/afa/[a--z]erge-rep?s.txt",
    "rm -rf ~/.[!.]*",
])
def test_bracket_set_that_can_match_blocked(home, command):
    assert_blocked(bash(home, command, cwd=home))


# ---- long input: the settings file stops the hook after a few seconds, and a stopped or crashed hook lets the
# ---- command through, so nothing may run it past its own limit and nothing may crash it ----

SETTINGS = Path(__file__).resolve().parents[3] / "dotfiles" / "settings.json"
LOADER = (
    "import importlib.util, sys, time\n"
    f"spec = importlib.util.spec_from_file_location('guard', {str(HOOK)!r})\n"
    "guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)\n"
)
PADS = {
    "brace groups side by side": "{a,b}" * 5000,
    "brace groups inside each other": "{" * 3000 + "a,b" + "}" * 3000,
    "open brackets": "[" * 20000,
    "open brackets after a letter": "ls a" + "[" * 20000,
    "class openers that never close": "[[:" * 10000 + "]",
    "command output and wildcard groups": "$(a)@(b)" * 5000,
    "the word git": "git " * 25000,
}


def settings_timeout():
    groups = json.loads(SETTINGS.read_text())["hooks"]["PreToolUse"]
    found = [h["timeout"] for g in groups for h in g["hooks"] if h["command"].endswith("/list-file-guard.py")]
    assert len(found) == 1, found
    return found[0]


def bash_in_settings_time(home, command, where="notes"):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(home / where)})
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run([sys.executable, str(HOOK)], input=payload, env=env, capture_output=True, text=True,
                          cwd=str(home), timeout=settings_timeout())


def probe(home, tmp_path, body, *args):
    """Run a few lines against the hook loaded as a module, in its own process with the temporary HOME."""
    script = tmp_path / "probe.py"
    script.write_text(LOADER + body)
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run([sys.executable, str(script), *args], env=env, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("pad", sorted(PADS))
def test_long_padding_does_not_hide_a_command(home, pad):
    res = bash_in_settings_time(home, PADS[pad] + " ; rm -rf ~/.config/afa")
    assert_blocked(res)
    assert ".config/afa folder" in res.stderr, res.stderr


@pytest.mark.parametrize("pad", sorted(PADS))
def test_long_padding_alone_is_read_in_time_and_allowed(home, pad):
    res = bash_in_settings_time(home, PADS[pad] + " ; ls")
    assert_allowed(res)
    assert res.stderr == ""


STAND_IN_RUNS = {
    "thirty brace pairs": "touch " + "{a,b}" * 30,
    "three hundred brace pairs": "touch " + "{a,b}" * 300,
    "five thousand brace pairs": "touch " + "{a,b}" * 5000,
    "three hundred number runs": "touch " + "{1..9}" * 300,
    "pairs between stars, dot names on": "shopt -s dotglob ; ls " + "*{a,b}" * 300,
    "pairs that hold a slash": "ls notes/" + "{a/b,c/d}" * 300 + "/x",
}


@pytest.mark.parametrize("pad", sorted(STAND_IN_RUNS))
@pytest.mark.parametrize("where", ["notes", ".", ".config"])
def test_a_long_run_of_brace_groups_is_read_in_time_from_any_folder(home, pad, where):
    """A run of groups is one "any characters", so it costs the same from the home folder as from anywhere else."""
    res = bash_in_settings_time(home, STAND_IN_RUNS[pad], where)
    assert_allowed(res)
    assert res.stderr == ""


@pytest.mark.parametrize("where", [".", ".config"])
def test_a_long_run_of_brace_groups_does_not_hide_a_command(home, where):
    """Refused for a folder it names, not for running out of time. From the home folder the run itself could
    name the .config folder, so that is the folder the refusal gives there."""
    res = bash_in_settings_time(home, "touch " + "{a,b}" * 300 + " ; rm -rf ~/.config/afa", where)
    assert_blocked(res)
    assert "folder holds the merge lists" in res.stderr, res.stderr
    assert "too long" not in res.stderr


def test_a_check_that_runs_too_long_is_refused(home, tmp_path):
    """Whatever makes the check slow, the hook answers "refused" itself before the settings file stops it."""
    res = probe(home, tmp_path, (
        "guard.TIME_LIMIT = 0.3\n"
        "guard.check_bash = lambda command, cwd: time.sleep(30)\n"
        "start = time.monotonic()\n"
        "code = guard.decide_in_time({'tool_name': 'Bash', 'tool_input': {'command': 'ls'}})\n"
        "print(code, time.monotonic() - start < 5)\n"
    ))
    assert res.stdout.split() == ["2", "True"], res.stderr
    assert res.stderr.startswith("BLOCKED:")
    assert "too long" in res.stderr


def test_a_fault_in_the_whole_check_is_one_line_and_does_not_refuse(home, tmp_path):
    res = probe(home, tmp_path, (
        "def broken(command, cwd):\n"
        "    raise RuntimeError('made-up fault')\n"
        "guard.check_bash = broken\n"
        "print(guard.decide_in_time({'tool_name': 'Bash', 'tool_input': {'command': 'ls'}}))\n"
    ))
    assert res.stdout.split() == ["1"], res.stderr
    assert res.stderr.strip() == "list-file-guard internal error: RuntimeError"


def test_an_answer_in_time_is_passed_on_unchanged(home, tmp_path):
    res = probe(home, tmp_path, (
        "for command in ('ls ~/notes', 'rm -rf ~/.config/afa'):\n"
        "    print(guard.decide_in_time({'tool_name': 'Bash', 'tool_input': {'command': command}, 'cwd': sys.argv[1]}))\n"
    ), str(home))
    assert res.stdout.split() == ["0", "2"], res.stderr
    assert res.stderr.count("BLOCKED:") == 1


def test_own_time_limit_is_inside_the_settings_timeout(home, tmp_path):
    res = probe(home, tmp_path, "print(guard.TIME_LIMIT)\n")
    assert float(res.stdout) + 1 <= settings_timeout(), "the hook must answer before the settings file stops it"


def test_brace_marking_matches_the_plain_reference(home, tmp_path):
    """The one-pass brace code gives the same answer as the simple repeat-until-done version, on 4000 made-up words."""
    res = probe(home, tmp_path, (
        "import random, re\n"
        "L, R, C = guard.LEFT, guard.RIGHT, guard.COMMA\n"
        "braces = re.compile(r'''\\{([^{}\\s\"'`]*)\\}''')\n"
        "group = re.compile('%s([^%s%s]*)%s' % (L, L, R, R))\n"
        "def mark(text):\n"
        "    while braces.search(text):\n"
        "        text = braces.sub(lambda m: L + m.group(1).replace(',', C) + R, text)\n"
        "    return text\n"
        "def collapse(word):\n"
        "    while group.search(word):\n"
        "        word = group.sub(lambda m: guard.ANY if ('/' in m.group(1) or guard.ANY in m.group(1)) else guard.ONE, word)\n"
        "    return word\n"
        "rng = random.Random(7)\n"
        "letters = '{{{}}},,,a/ \\'\"`\\t\\n' + L + R + C + guard.ANY\n"
        "bad = 0\n"
        "for _ in range(4000):\n"
        "    text = ''.join(rng.choice(letters) for _ in range(rng.randint(0, 30)))\n"
        "    marked = mark(text)\n"
        "    if guard.mark_braces(text) != marked or guard.collapse_braces(marked) != collapse(marked):\n"
        "        bad += 1\n"
        "        print('differs:', ascii(text))\n"
        "print('checked 4000, differ', bad)\n"
    ))
    assert res.stdout.strip() == "checked 4000, differ 0", res.stdout[-600:] + res.stderr[-600:]
