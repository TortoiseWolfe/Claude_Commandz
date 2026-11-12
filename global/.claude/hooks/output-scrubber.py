#!/usr/bin/env python3
"""Claude Code PostToolUse hook: masks secret values in what a tool printed, then runs the co-op scrub, in one process.

Use when: registering the one PostToolUse output rewriter (secret masking first, co-op name/email/phone scrub second).

stdin:  {"tool_name": ..., "tool_response": ..., "cwd": ...}
stdout: {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": <same shape, strings scrubbed>}}
        printed only when something was masked; otherwise nothing at all (exit 0), so ordinary output passes untouched.
Tier 1 (known values, token shapes, key blocks, URL passwords, auth headers) runs for every tool. Tier 2 (secret-named
fields) runs for Bash, Grep, WebFetch, WebSearch and mcp__* tools, not for Read/Edit/Write/NotebookEdit.
Then coop-scrubber.py's own main() runs on the result (it decides by its own test whether the folder is a co-op folder).
Two PostToolUse rewriters in parallel would race, so register this hook and leave coop-scrubber.py out of settings.
Fails closed: any exception or an 8 second alarm replaces every string of the result with a withheld notice.
Libraries: scripts/secret_scrubber.py and coop-scrubber.py, found beside this file and in ~/.claude.
PostToolUseFailure (updatedToolOutput is ignored there): every string except tool_input is scanned; a hit writes a count-only
line "failed-call" to ~/.claude/state/secret-scrub-alerts.log (SECRET_SCRUB_ALERTS overrides) and returns additionalContext.
PostToolUse for Bash with CLAUDE_CODE_SHELL_PREFIX set and no shell-scrub-off file: a hook-side mask logs "wrapper-missed"; a
heartbeat (SHELL_SCRUB_HEARTBEAT, ~/.claude/state/shell-scrub-heartbeat) older than 900 s on a foreground call logs
"heartbeat-stale" and adds a warning line to the model at most once per 30 minutes (stamp file beside the alerts log).
With the shell-scrub-off file present (SHELL_SCRUB_OFF_FILE) the heartbeat check is skipped, but the off switch is loud: a
"filter-off" alert line and a security notice to the model at most once per 30 minutes (its own stamp file), and a call the
hook had to mask logs "filter-off-masked" every time.
One line per masked call goes to ~/.claude/state/output-scrubber.log (OUTPUT_SCRUBBER_LOG overrides): time, tool, count.
"""
import contextlib
import importlib.util
import io
import json
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = [os.path.join(HERE, "..", "scripts"), os.path.join(os.path.expanduser("~"), ".claude", "scripts")]
for _p in reversed(SCRIPTS):
    sys.path.insert(0, _p)

TIER1_ONLY = {"Read", "Edit", "Write", "NotebookEdit"}
ALARM_SECONDS = 8


class Timeout(BaseException):   # not an Exception: coop-scrubber's own handlers must not swallow it
    pass


def _alarm(signum, frame):
    raise Timeout()


def withhold(obj):
    notice = "[withheld: the output scrubber failed on this text]"
    if isinstance(obj, str):
        return notice if obj else obj
    if isinstance(obj, dict):
        return {k: withhold(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [withhold(v) for v in obj]
    return obj


def emit(new, context=None):
    out = {"hookEventName": "PostToolUse", "updatedToolOutput": new}
    if context:
        out["additionalContext"] = context
    json.dump({"hookSpecificOutput": out}, sys.stdout)


def log(tool, count, note=""):
    path = os.environ.get("OUTPUT_SCRUBBER_LOG") or os.path.join(os.path.expanduser("~"), ".claude", "state",
                                                                 "output-scrubber.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write("%s\t%s\tmasked=%d\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), tool, count, note))
    except OSError:
        pass


def coop_step(payload, response):
    """Run coop-scrubber.py's main() on the already secret-scrubbed response. Returns the new response or None."""
    path = os.path.join(HERE, "coop-scrubber.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("coop_scrubber_hook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake_in = io.StringIO(json.dumps({"tool_name": payload.get("tool_name"), "cwd": payload.get("cwd"),
                                      "tool_response": response}))
    out = io.StringIO()
    old_in = sys.stdin
    sys.stdin = fake_in
    try:
        with contextlib.redirect_stdout(out):
            mod.main()
    finally:
        sys.stdin = old_in
    if not out.getvalue().strip():
        return None
    return json.loads(out.getvalue())["hookSpecificOutput"]["updatedToolOutput"]


def state_path(env_name, name):
    return os.environ.get(env_name) or os.path.join(os.path.expanduser("~"), ".claude", "state", name)


def alert(payload, tool, count, word):
    """One line in the alerts log: time, session id, tool, count, a word. Never any text."""
    path = state_path("SECRET_SCRUB_ALERTS", "secret-scrub-alerts.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write("%s\t%s\t%s\tcount=%d\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), payload.get("session_id") or "?",
                                                    tool, count, word))
    except OSError:
        pass


def once_per_30_min(name=""):
    """True (and stamps) when no warning of this kind (name) went to the model in the last 30 minutes.
    Each kind has its own stamp file, so the off-switch notice cannot hide the heartbeat warning."""
    stamp = state_path("SECRET_SCRUB_ALERTS", "secret-scrub-alerts.log") + (".%s.stamp" % name if name else ".stamp")
    try:
        if time.time() - os.path.getmtime(stamp) < 1800:
            return False
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w"):
            pass
    except OSError:
        pass
    return True


def emit_context(event, text):
    json.dump({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}, sys.stdout)


def failed_call(payload, tool):
    """PostToolUseFailure: updatedToolOutput is ignored there, so a secret in a failed call's output cannot be masked.
    Scan every string except tool_input; on a hit write a count-only alert line and tell the model (additionalContext)."""
    try:
        import secret_scrubber as ss
        rest = {k: v for k, v in payload.items() if k != "tool_input"}
        _, count = ss.scrub_obj(rest, 1 if tool in TIER1_ONLY else 2)
    except (Exception, Timeout):
        return 0
    if not count:
        return 0
    alert(payload, tool, count, "failed-call")
    when = time.strftime("%Y-%m-%dT%H:%M:%S")
    emit_context("PostToolUseFailure",
                 "Security notice: a secret-like value showed in the output of this failed %s command and was NOT masked. "
                 "Do not repeat it, quote it or write it anywhere (not in files, commits, reports or messages). "
                 "Write the line `SECRET-EXPOSED %s %s` in your report.md so the hub can rotate it." % (tool, tool, when))
    return 0


def prefix_checks(payload, tool, count):
    """PostToolUse for Bash when CLAUDE_CODE_SHELL_PREFIX is set: did the shell wrapper do its job? Returns context or None."""
    if tool != "Bash" or not os.environ.get("CLAUDE_CODE_SHELL_PREFIX"):
        return None
    if os.path.exists(state_path("SHELL_SCRUB_OFF_FILE", "shell-scrub-off")):
        if count:     # printed while the filter was off and only this hook caught it
            alert(payload, tool, count, "filter-off-masked")
        if once_per_30_min("off"):
            alert(payload, tool, 0, "filter-off")
            return ("Security notice: the Bash output filter is switched off (the file shell-scrub-off exists in "
                    "~/.claude/state), so the output of a failed command is not masked. Unless the hub told you it "
                    "switched the filter off, write this in your report.md for the hub.")
        return None
    if count:
        alert(payload, tool, count, "wrapper-missed")
    ti = payload.get("tool_input")
    if isinstance(ti, dict) and ti.get("run_in_background"):
        return None
    try:
        stale = time.time() - os.path.getmtime(state_path("SHELL_SCRUB_HEARTBEAT", "shell-scrub-heartbeat")) > 900
    except OSError:
        stale = True
    if not stale:
        return None
    alert(payload, tool, 0, "heartbeat-stale")
    if once_per_30_min():
        return ("Security notice: the Bash output filter is not running (its heartbeat is missing or older than 15 minutes), "
                "so command output is not being masked. Tell the hub in your report.md.")
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        tool = str(payload.get("tool_name") or "?")
        if payload.get("hook_event_name") == "PostToolUseFailure":
            return failed_call(payload, tool)
        if "tool_response" not in payload:
            return 0
        response = payload["tool_response"]
    except Exception:
        return 0
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(ALARM_SECONDS)
    try:
        import secret_scrubber as ss
        tier = 1 if tool in TIER1_ONLY else 2
        new, count = ss.scrub_obj(response, tier)
        after_coop = coop_step(payload, new)
        signal.alarm(0)
        final = after_coop if after_coop is not None else new
        context = prefix_checks(payload, tool, count)
        if count or after_coop is not None:
            if count:
                log(tool, count)
            emit(final, context)
        elif context:
            emit_context("PostToolUse", context)
    except (Exception, Timeout) as exc:   # stay closed: nothing raw reaches the model
        signal.alarm(0)
        sys.stderr.write("output scrubber: failed (%s), so this result is withheld\n" % type(exc).__name__)
        log(tool, 0, "failed " + type(exc).__name__)
        emit(withhold(response))
    return 0


if __name__ == "__main__":
    sys.exit(main())
