import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "identity-leak-guard.py"
spec = importlib.util.spec_from_file_location("identity_leak_guard", SRC)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

# The installed mail tools live in one folder; the guard allows them by real path there, never by file name alone.
TOOLS = tempfile.mkdtemp(prefix="idg-tools-")
for _name in ("gmail_api.py", "calendar_api.py"):
    Path(TOOLS, _name).write_text("import urllib.request\n")
os.environ["IDENTITY_GUARD_SCRIPTS_DIR"] = TOOLS

ME = "owner.name@example.org"  # a made-up address: the real one is never written in this repo
ADDRS = {ME}
ALLOWED = set(g.DEFAULT_ALLOWED_SCRIPTS)


def bash(cmd, cwd="/tmp"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd}


def verdict(payload):
    return g.check(payload, ADDRS, ALLOWED)


class Refuses(unittest.TestCase):
    """Every shape that puts the address in front of a server."""

    def deny(self, cmd, cwd="/tmp"):
        r = verdict(bash(cmd, cwd))
        self.assertIsNotNone(r, cmd)
        self.assertNotIn(ME, r)  # the reason never echoes the address
        self.assertIn("107757", r)

    def test_curl_user_agent_header(self):
        self.deny('curl -H "User-Agent: Mozilla (%s)" https://example.com/' % ME)

    def test_curl_user_agent_flags(self):
        self.deny("curl -A '%s' https://example.com" % ME)
        self.deny("curl --user-agent %s https://example.com" % ME)
        self.deny("curl --header 'User-Agent: x/1.0 %s' https://example.com" % ME)

    def test_curl_other_header(self):
        self.deny('curl -H "From: %s" https://example.com' % ME)

    def test_curl_url_path_and_query(self):
        self.deny("curl 'https://example.com/lookup?email=%s'" % ME)
        self.deny("curl https://example.com/u/%s/profile" % ME)

    def test_percent_encoded_in_url(self):
        self.deny("curl 'https://example.com/?email=owner.name%40example.org'")
        self.deny("curl 'https://example.com/?email=owner.name%2540example.org'")

    def test_request_body(self):
        self.deny("curl -d 'email=%s' https://example.com/signup" % ME)
        self.deny("curl --data-urlencode email=%s https://example.com" % ME)
        self.deny("curl -X POST -F 'who=%s' https://example.com" % ME)
        self.deny("curl --json '{\"to\": \"%s\"}' https://example.com" % ME)

    def test_address_piped_into_a_body(self):
        self.deny("echo %s | curl -d @- https://example.com" % ME)
        self.deny("printf '%s' | xargs curl -d" % ME)

    def test_wget_and_friends(self):
        self.deny("wget --header='User-Agent: %s' https://example.com/f" % ME)
        self.deny("wget -U %s https://example.com/f" % ME)
        self.deny("http POST https://example.com email=%s" % ME)
        self.deny("https example.com User-Agent:%s" % ME)
        self.deny("echo %s | nc example.com 80" % ME)

    def test_wrappers_and_paths_do_not_hide_the_client(self):
        self.deny("sudo -n /usr/bin/curl -A %s https://example.com" % ME)
        self.deny("timeout 5 curl -A %s https://example.com" % ME)
        self.deny("FOO=1 curl -A %s https://example.com" % ME)
        self.deny("cd /tmp && ls; curl -A %s https://example.com" % ME)
        self.deny("bash -c 'curl -A %s https://example.com'" % ME)
        self.deny("x=$(curl -A %s https://example.com)" % ME)

    def test_address_spelled_through_quotes_escapes_and_case(self):
        self.deny("curl -A 'owner.name'@'example.org' https://example.com")
        self.deny('curl -A owner.name"@"example.org https://example.com')
        self.deny("curl -A OWNER.Name@Example.ORG https://example.com")
        self.deny("curl -A 'owner.name&#64;example.org' https://example.com")
        self.deny("curl -A 'owner.name\\u0040example.org' https://example.com")

    def test_the_address_read_from_git_or_the_environment(self):
        self.deny('curl -A "$(git config user.email)" https://example.com')
        self.deny('curl -A "$GIT_AUTHOR_EMAIL" https://example.com')
        self.deny('curl -A "${EMAIL}" https://example.com')

    def test_inline_python(self):
        self.deny("python3 -c \"import requests; requests.get('https://example.com', headers={'User-Agent': '%s'})\"" % ME)
        self.deny("python3 - <<'EOF'\nimport urllib.request as u\nu.urlopen(u.Request('https://x.io', headers={'User-Agent': '%s'}))\nEOF" % ME)

    def test_inline_node_ruby_perl(self):
        self.deny("node -e \"fetch('https://example.com', {headers: {'User-Agent': '%s'}})\"" % ME)
        self.deny("ruby -e \"require 'net/http'; Net::HTTP.get(URI('https://x.io/?e=%s'))\"" % ME)
        self.deny("perl -e 'use LWP::UserAgent; LWP::UserAgent->new(agent => \"%s\")->get(\"https://x.io\")'" % ME)

    def test_a_script_file_an_agent_wrote(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "probe.py").write_text("import requests\nrequests.get('https://x.io', headers={'User-Agent': '%s'})\n" % ME)
            (Path(d) / "probe.js").write_text("fetch('https://x.io', {headers: {'User-Agent': '%s'}})\n" % ME)
            self.deny("python3 probe.py", cwd=d)
            self.deny("python3 -u ./probe.py --verbose", cwd=d)
            self.deny("node %s/probe.js" % d)
            self.deny("cd %s && python3 probe.py" % d, cwd=d)

    def test_a_script_file_given_the_address_on_the_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "probe.py").write_text("import sys, requests\nrequests.get('https://x.io', headers={'User-Agent': sys.argv[1]})\n")
            self.deny("python3 probe.py %s" % ME, cwd=d)

    def test_options_that_take_a_value_hide_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "probe.py").write_text("import requests\nrequests.get('https://x.io', headers={'User-Agent': '%s'})\n" % ME)
            self.deny("python3 -W ignore probe.py", cwd=d)
            self.deny("python3 -X utf8 -B probe.py", cwd=d)
            self.deny("node --require ./x.js probe.py", cwd=d)

    def test_a_cd_earlier_in_the_line_is_followed(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "sub").mkdir()
            (Path(d) / "sub" / "probe.py").write_text("import requests\nrequests.get('https://x.io', headers={'User-Agent': '%s'})\n" % ME)
            self.deny("cd sub && python3 probe.py", cwd=d)
            self.deny("cd %s/sub; python3 probe.py" % d, cwd="/tmp")

    def test_a_script_that_cannot_be_found_is_not_assumed_safe(self):
        self.deny("python3 /nowhere/probe.py %s" % ME)
        self.deny("node ./made-later.js --to %s" % ME)

    def test_a_script_too_large_to_read_in_full_is_not_assumed_safe(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "big.py").write_text("x = 1\n" * (g.MAX_SCRIPT_BYTES // 6 + 10))
            self.deny("python3 big.py %s" % ME, cwd=d)

    def test_a_later_flag_on_another_command_does_not_hide_the_script(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "probe.py").write_text("import requests\nrequests.get('https://x.io', headers={'User-Agent': '%s'})\n" % ME)
            self.deny("python3 probe.py ; grep -e x file", cwd=d)
            self.deny("python3 probe.py && sed -e s/a/b/ file", cwd=d)
            self.deny("python3 -W ignore probe.py ; echo -c", cwd=d)

    def test_a_missing_script_is_not_hidden_by_a_mail_tool_beside_it(self):
        self.deny("python3 /nowhere/probe.py %s/gmail_api.py %s" % (TOOLS, ME))
        self.deny("python3 ./made-later.py %s/calendar_api.py %s" % (TOOLS, ME))

    def test_a_folder_change_the_hook_cannot_follow_is_not_assumed_safe(self):
        for cmd in ("pushd sub && python3 gmail_api.py --to %s", "cd - && python3 gmail_api.py --to %s",
                    "cd $WORK && python3 gmail_api.py --to %s", "cd `pwd`/sub && python3 gmail_api.py --to %s"):
            with self.subTest(cmd=cmd):
                self.deny(cmd % ME)

    def test_a_mail_tool_is_trusted_only_as_the_very_next_word_by_absolute_path(self):
        for cmd in ("python3 -u %s/gmail_api.py x --to %s", "python3 -c 'import requests' %s/gmail_api.py %s",
                    "python3 -m gmail_api x --to %s", "python3 -mfoo %s/gmail_api.py --to %s", "node --require=/tmp/x.js %s/gmail_api.py %s"):
            with self.subTest(cmd=cmd):
                n = cmd.count("%s")
                self.deny(cmd % ((TOOLS, ME) if n == 2 else (ME,)))
        self.deny("cd %s && python3 gmail_api.py --to %s" % (TOOLS, ME))
        self.deny("cd; python3 gmail_api.py --to %s" % ME)
        self.deny("export PYTHONPATH=/tmp/evil; python3 %s/gmail_api.py --to %s" % (TOOLS, ME))
        self.deny("source /tmp/x.sh; python3 %s/gmail_api.py --to %s" % (TOOLS, ME))
        self.deny("FOO=1 declare -x PYTHONPATH=/tmp/e; python3 %s/gmail_api.py --to %s" % (TOOLS, ME))

    def test_a_mail_tool_is_not_trusted_when_the_interpreter_can_be_swapped(self):
        for cmd in ("PATH=/tmp/evil:$PATH python3 %s/gmail_api.py --to %s", "alias python3=/tmp/evil; python3 %s/gmail_api.py --to %s",
                    "python3() { /tmp/evil \"$@\"; }; python3 %s/gmail_api.py --to %s", "hash -p /tmp/evil python3; python3 %s/gmail_api.py --to %s",
                    "/tmp/evil/python3 %s/gmail_api.py --to %s", "/usr/bin/../../tmp/evil/python3 %s/gmail_api.py --to %s", "export PATH=/tmp/evil; python3 %s/gmail_api.py --to %s", "./python3 %s/gmail_api.py --to %s"):
            with self.subTest(cmd=cmd):
                self.deny(cmd % (TOOLS, ME))

    def test_a_mail_tool_is_not_trusted_beside_settings_that_load_other_code(self):
        for pre in ("PYTHONPATH=/tmp/evil ", "PYTHONSTARTUP=/tmp/x.py ", "NODE_OPTIONS=--require=/tmp/x.js ", "LD_PRELOAD=/tmp/x.so ",
                    "env PYTHONPATH=/tmp/evil ", "env -i "):
            with self.subTest(pre=pre):
                self.deny("%spython3 %s/gmail_api.py draft-create --to %s" % (pre, TOOLS, ME))

    def test_webfetch_and_websearch(self):
        for url in ("https://example.com/?e=%s" % ME, "https://example.com/?e=owner.name%40example.org", "https://example.com/" + ME):
            with self.subTest(url=url):
                r = verdict({"tool_name": "WebFetch", "tool_input": {"url": url, "prompt": "summarise"}})
                self.assertIn("WebFetch", r)
                self.assertNotIn(ME, r)
        r = verdict({"tool_name": "WebSearch", "tool_input": {"query": '"%s" account' % ME}})
        self.assertIn("WebSearch", r)


class Allows(unittest.TestCase):
    """Local files, mail tools and ordinary web calls that do not carry the address."""

    def ok(self, cmd, cwd="/tmp"):
        self.assertIsNone(verdict(bash(cmd, cwd)), cmd)

    def test_local_text_and_files(self):
        self.ok("echo 'User-Agent: %s'" % ME)
        self.ok("echo %s >> /tmp/notes.txt" % ME)
        self.ok("cat notes.txt | grep -i '%s'" % ME)
        self.ok("grep -rn %s ~/repos/hub/people.md" % ME)
        self.ok("sed -i 's/old/%s/' config.txt" % ME)

    def test_git_with_the_address(self):
        self.ok("git config user.email %s" % ME)
        self.ok("git commit --author 'Me <%s>' -m 'fix'" % ME)
        self.ok("git log --author=%s --oneline" % ME)
        self.ok("git config user.email")

    def test_the_mail_tools(self):
        self.ok("python3 %s/gmail_api.py draft-create --to %s --subject s --html f.html" % (TOOLS, ME))
        self.ok("python3 %s/gmail_api.py draft-raw r123 --to %s" % (TOOLS, ME))
        self.ok("python3 %s/calendar_api.py events --account %s" % (TOOLS, ME))

    def test_a_script_with_a_mail_tool_name_elsewhere_is_not_a_mail_tool(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "gmail_api.py").write_text("import requests\nrequests.get('https://x.io', headers={'User-Agent': '%s'})\n" % ME)
            self.assertIsNotNone(verdict(bash("python3 gmail_api.py", cwd=d)))
            self.assertIsNotNone(verdict(bash("python3 %s/gmail_api.py" % d)))

    def test_a_link_to_a_mail_tool_is_judged_by_where_it_points(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "evil.py").write_text("import requests\nrequests.get('https://x.io', headers={'User-Agent': '%s'})\n" % ME)
            os.symlink(Path(d) / "evil.py", Path(d) / "gmail_api.py")
            self.assertIsNotNone(verdict(bash("python3 %s/gmail_api.py" % d)))

    def test_web_calls_without_the_address(self):
        self.ok("curl -A 'my-tool/1.0' https://example.com/")
        self.ok("curl -H 'Authorization: Bearer $TOKEN' https://api.example.com/v1/items")
        self.ok("wget https://example.com/file.tgz")
        self.ok("curl -d 'email=someone.else@example.net' https://example.com/signup")
        self.ok("python3 -c \"import requests; requests.get('https://example.com')\"")

    def test_python_and_node_that_never_touch_the_network(self):
        self.ok("python3 -c \"open('/tmp/who.txt','w').write('%s')\"" % ME)
        self.ok("python3 - <<'EOF'\nprint('%s')\nEOF" % ME)
        self.ok("node -e \"console.log('%s')\"" % ME)

    def test_a_local_script_with_the_address_and_no_network(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "guard.py").write_text("SELF = '%s'\ndef check(x):\n    return x == SELF\n" % ME)
            self.ok("python3 guard.py", cwd=d)
            self.ok("python3 -m pytest guard.py", cwd=d)

    def test_a_network_script_that_does_not_hold_the_address(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ping.py").write_text("import requests\nrequests.get('https://x.io')\n")
            self.ok("python3 ping.py", cwd=d)

    def test_a_client_name_that_is_only_text(self):
        self.ok("git commit -m 'stop curl sending %s'" % ME)
        self.ok("grep -rn \"curl\" docs/ --include=*.md  # %s" % ME)
        self.ok("echo 'run curl with %s later' > todo.txt" % ME)

    def test_look_alike_addresses_are_not_the_address(self):
        self.ok("curl -d 'to=not%s' https://example.com" % ME)
        self.ok("curl -d 'to=%s.evil.io' https://example.com" % ME)
        self.ok("curl -d 'to=%s-two' https://example.com" % ME)

    def test_other_tools_are_ignored(self):
        self.assertIsNone(verdict({"tool_name": "Read", "tool_input": {"file_path": "/tmp/%s" % ME}}))
        self.assertIsNone(verdict({"tool_name": "mcp__claude_ai_Gmail__create_draft", "tool_input": {"to": [ME]}}))
        self.assertIsNone(verdict({"tool_name": "Write", "tool_input": {"file_path": "/tmp/x", "content": ME}}))

    def test_webfetch_and_websearch_without_the_address(self):
        self.assertIsNone(verdict({"tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a?b=c", "prompt": ME}}))
        self.assertIsNone(verdict({"tool_name": "WebSearch", "tool_input": {"query": "claude code user agent header"}}))

    def test_odd_payloads_are_allowed(self):
        self.assertIsNone(verdict({"tool_name": "Bash", "tool_input": {}}))
        self.assertIsNone(verdict({"tool_name": "Bash", "tool_input": {"command": 7}}))
        self.assertIsNone(verdict({}))


class Settings(unittest.TestCase):
    def run_hook(self, payload, config=None, claude_json=None, raw=None, scripts_dir=None):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, IDENTITY_GUARD_LOG=os.path.join(d, "log.txt"),
                       IDENTITY_GUARD_CONFIG=os.path.join(d, "none.json"), IDENTITY_GUARD_CLAUDE_JSON=os.path.join(d, "none2.json"))
            if config is not None:
                Path(d, "cfg.json").write_text(json.dumps(config))
                env["IDENTITY_GUARD_CONFIG"] = os.path.join(d, "cfg.json")
            if claude_json is not None:
                Path(d, "cj.json").write_text(json.dumps(claude_json))
                env["IDENTITY_GUARD_CLAUDE_JSON"] = os.path.join(d, "cj.json")
            if scripts_dir:
                env["IDENTITY_GUARD_SCRIPTS_DIR"] = scripts_dir
            r = subprocess.run([sys.executable, str(SRC)], input=raw if raw is not None else json.dumps(payload),
                               capture_output=True, text=True, env=env)
            log = Path(d, "log.txt").read_text() if Path(d, "log.txt").exists() else ""
            return r, log

    CMD = bash("curl -A %s https://example.com" % ME)

    def test_address_from_the_private_config(self):
        r, _ = self.run_hook(self.CMD, config={"addresses": [ME]})
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stderr.startswith("BLOCKED:"))
        self.assertNotIn(ME, r.stderr)

    def test_address_from_the_account_claude_holds(self):
        r, _ = self.run_hook(self.CMD, claude_json={"oauthAccount": {"emailAddress": ME.upper()}})
        self.assertEqual(r.returncode, 2)

    def test_no_address_known_allows_and_logs(self):
        r, log = self.run_hook(self.CMD)
        self.assertEqual(r.returncode, 0)
        self.assertIn("no address known", log)
        self.assertNotIn(ME, log)

    def test_unreadable_payload_allows_and_logs(self):
        r, log = self.run_hook(None, config={"addresses": [ME]}, raw="not json")
        self.assertEqual(r.returncode, 0)
        self.assertIn("internal error", log)

    def test_config_can_name_more_mail_scripts(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "mymail.py").write_text("import os, requests\nTO = '%s'\n" % ME)
            cmd = bash("python3 mymail.py", cwd=d)
            r, _ = self.run_hook(cmd, config={"addresses": [ME]})
            self.assertEqual(r.returncode, 2)
            cmd = bash("python3 %s/mymail.py" % d, cwd="/tmp")
            r, _ = self.run_hook(cmd, config={"addresses": [ME], "allow_scripts": ["mymail.py"]}, scripts_dir=d)
            self.assertEqual(r.returncode, 0)

    def test_a_config_with_junk_is_ignored(self):
        r, _ = self.run_hook(self.CMD, config={"addresses": [5, None, "no-at-sign"], "allow_scripts": "x"})
        self.assertEqual(r.returncode, 0)


class McpTools(unittest.TestCase):
    """The MCP browser and fetch-style tools reach websites too (the Claude Commandz #296 review found browser_navigate open)."""

    def mcp(self, tool, **fields):
        return verdict({"tool_name": "mcp__MCP_DOCKER__" + tool, "tool_input": fields})

    def test_browser_navigate_url_refused(self):
        r = self.mcp("browser_navigate", url="https://example.com/?email=%s" % ME)
        self.assertIsNotNone(r)
        self.assertNotIn(ME, r)
        self.assertIsNotNone(self.mcp("browser_navigate", url="https://example.com/?e=owner.name%40example.org"))

    def test_other_mcp_fetch_style_tools_refused(self):
        self.assertIsNotNone(self.mcp("get_transcript", url="https://example.com/watch?u=%s" % ME))
        self.assertIsNotNone(self.mcp("some_fetch", options={"inner": [{"href": "https://x.test/%s" % ME}]}))
        self.assertIsNotNone(self.mcp("search_code", query=ME))

    def test_browser_code_that_goes_to_the_network_refused(self):
        self.assertIsNotNone(self.mcp("browser_evaluate", function="() => fetch('https://x.test/?e=%s')" % ME))
        self.assertIsNotNone(self.mcp("browser_run_code", code="await page.goto('https://x.test/%s')" % ME))

    def test_ordinary_mcp_calls_allowed(self):
        self.assertIsNone(self.mcp("browser_navigate", url="https://example.com/"))
        self.assertIsNone(self.mcp("browser_type", text=ME, ref="e5"))  # typing into a page is the user's own form, not a request line
        self.assertIsNone(self.mcp("browser_evaluate", function="() => document.title"))
        self.assertIsNone(self.mcp("get_file_contents", owner="o", repo="r", path="README.md"))


class Registration(unittest.TestCase):
    """The settings copy in this repo must run the guard on exactly the tools it checks."""

    def test_settings_run_the_guard_before_bash_webfetch_websearch_and_mcp_tools(self):
        settings = json.loads((SRC.parents[2] / "dotfiles" / "settings.json").read_text())
        entries = [e for e in settings["hooks"]["PreToolUse"]
                   if any("identity-leak-guard.py" in h["command"] for h in e["hooks"])]
        self.assertEqual(len(entries), 1)
        self.assertEqual(set(entries[0]["matcher"].split("|")), {"Bash", "WebFetch", "WebSearch", "mcp__.*"})

    def test_the_hook_file_carries_no_real_address(self):
        text = SRC.read_text() + Path(__file__).read_text()
        self.assertNotIn("poh" + "lner", text.lower())


if __name__ == "__main__":
    unittest.main()
