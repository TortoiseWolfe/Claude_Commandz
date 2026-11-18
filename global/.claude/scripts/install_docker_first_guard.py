#!/usr/bin/env python3
"""Install the Docker-first guard hook on this machine: one file copied, one settings entry added. Nothing else.
Use when: a person turns the Docker-first guard on or off on a machine (never a repo-wide hook or script sync).

Jonathan's rule (18 Nov 2026): "always Docker first! for all our projects". This puts the hook that enforces it in place.

A person runs it in a terminal, from a checkout of this repo that has the merged hook:

  python3 global/.claude/scripts/install_docker_first_guard.py              install (safe to run again: it adds nothing twice)
  python3 global/.claude/scripts/install_docker_first_guard.py --uninstall  the off switch: remove the entry, then the file
  python3 global/.claude/scripts/install_docker_first_guard.py --dry-run    say what would change, change nothing

It does exactly two things, in this order, and is on purpose NOT part of any repo-wide hook or script sync (install_global.py,
install_scripts.py): the public repo was scrubbed, and a whole-folder sync could arm other live guards with placeholder text.
  1. copies hooks/docker-first-guard.py (the file beside this script's own folder) to ~/.claude/hooks/ FIRST, because a settings
     entry that points at a missing file makes every Bash call fail;
  2. merges one PreToolUse entry (matcher "Bash") into ~/.claude/settings.json: the JSON is parsed, the entry added only if its
     command is not already there, and the file written back with its own indent. A settings file that is not valid JSON is
     left alone and the run stops.
Then it proves the result by running the registered command the way Claude Code does (a Bash payload on stdin, an empty HOME so no
exemption file can matter): "npm --version" must pass and "npm ci" must be refused with the quoted rule. The last line is
"PASS ..." or "FAIL <exact reason>". On a failure after a change it puts both files back from the backup it took first.
Backups and an undo.sh go to ~/.claude/state/docker-first-guard-backups/<stamp>/.

No exemption file is created or read here; the hook reads ~/.config/afa/docker-first-exempt.txt itself, and only Jonathan's word adds one.
Exit 0 = PASS, 1 = FAIL (after undoing itself), 2 = could not start (nothing changed).
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile

HOOK_NAME = "docker-first-guard.py"
COMMAND = "python3 ~/.claude/hooks/docker-first-guard.py"
ENTRY = {"matcher": "Bash", "hooks": [{"type": "command", "command": COMMAND, "timeout": 5}]}
RULE = "always Docker first! for all our projects"
PLACEHOLDER_RE = re.compile(rb"@example\.(?:com|org|net)|REDACTED|<redacted|PLACEHOLDER", re.I)


class Stop(Exception):
    """A reason to stop; exit_code 2 = nothing changed yet, 1 = something changed and was put back."""

    def __init__(self, msg, code=2):
        super().__init__(msg)
        self.code = code


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def write_atomic(path, data, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".dfg-new"
    with open(tmp, "wb") as f:
        f.write(data)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def registered(settings):
    """Every (group, hook) in hooks.PreToolUse whose command is ours."""
    found = []
    for group in settings.get("hooks", {}).get("PreToolUse", []):
        for hook in group.get("hooks", []):
            if hook.get("command") == COMMAND:
                found.append((group, hook))
    return found


def parse_settings(raw, path):
    try:
        settings = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise Stop(f"{path} is not valid JSON ({e}); left alone")
    if not isinstance(settings, dict):
        raise Stop(f"{path} is not a JSON object; left alone")
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict) or not isinstance(hooks.get("PreToolUse", []), list):
        raise Stop(f"{path}: hooks or hooks.PreToolUse has an unexpected shape; left alone")
    for group in hooks.get("PreToolUse", []):
        if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
            raise Stop(f"{path}: a PreToolUse group has an unexpected shape; left alone")
        if any(not isinstance(h, dict) for h in group.get("hooks", [])):
            raise Stop(f"{path}: a PreToolUse hook has an unexpected shape; left alone")
    return settings


def indent_of(raw):
    """The indent the file already uses (so the merge does not reformat the whole file)."""
    m = re.search(rb"\n([ \t]+)\S", raw)
    if not m:
        return 2
    ws = m.group(1)
    return "\t" if ws.startswith(b"\t") else len(ws)


def dump(settings, raw):
    text = json.dumps(settings, indent=indent_of(raw), ensure_ascii=False)
    if raw.endswith(b"\n"):
        text += "\n"
    return text.encode("utf-8")


def add_entry(settings):
    """True when the entry had to be added. A second run finds ours and adds nothing."""
    if registered(settings):
        return False
    settings.setdefault("hooks", {}).setdefault("PreToolUse", []).append(json.loads(json.dumps(ENTRY)))
    return True


def remove_entry(settings):
    """True when something was removed. A group left with no hooks is dropped; every other group stays."""
    removed = False
    groups = settings.get("hooks", {}).get("PreToolUse", [])
    for group in list(groups):
        before = len(group.get("hooks", []))
        group["hooks"] = [h for h in group.get("hooks", []) if h.get("command") != COMMAND]
        if len(group["hooks"]) != before:
            removed = True
            if not group["hooks"]:
                groups.remove(group)
    return removed


def smoke(settings_home, settings):
    """None when the guard works, else the exact failure."""
    entries = registered(settings)
    if not entries:
        return "settings.json has no PreToolUse entry for the guard"
    command_text = entries[0][1]["command"]
    with tempfile.TemporaryDirectory(prefix="dfg-smoke-") as scratch:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": scratch}
        cmd = command_text.replace("~/.claude", settings_home)
        for text, want in (("npm --version", 0), ("npm ci", 2)):
            payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": text}, "cwd": scratch})
            try:
                r = subprocess.run(["sh", "-c", cmd], input=payload, capture_output=True, text=True, env=env, timeout=20)
            except subprocess.TimeoutExpired:
                return f'smoke "{text}": the hook did not answer in 20 seconds'
            if r.returncode != want:
                return f'smoke "{text}": exit {r.returncode}, wanted {want}; stderr: {r.stderr.strip()[:200]}'
            if want == 2 and RULE not in r.stderr:
                return f'smoke "{text}": refused, but the message does not quote the rule; stderr: {r.stderr.strip()[:200]}'
    return None


def plan_state(home):
    hook = os.path.join(home, "hooks", HOOK_NAME)
    return hook, os.path.join(home, "settings.json"), read_bytes(hook), read_bytes(os.path.join(home, "settings.json"))


def backup(home, stamp, hook_before, settings_before):
    folder = os.path.join(home, "state", "docker-first-guard-backups", stamp)
    os.makedirs(folder, exist_ok=True)
    lines = ["#!/bin/sh", "# puts back the two files this run touched", "set -e"]
    hook, settings = os.path.join(home, "hooks", HOOK_NAME), os.path.join(home, "settings.json")
    if hook_before is not None:
        write_atomic(os.path.join(folder, HOOK_NAME), hook_before)
        lines.append(f'cp -p "{os.path.join(folder, HOOK_NAME)}" "{hook}"')
    else:
        lines.append(f'rm -f "{hook}"')
    if settings_before is not None:
        write_atomic(os.path.join(folder, "settings.json"), settings_before)
        lines.append(f'cp -p "{os.path.join(folder, "settings.json")}" "{settings}"')
    write_atomic(os.path.join(folder, "undo.sh"), ("\n".join(lines) + "\n").encode(), 0o755)
    return folder


def restore(hook, settings_path, hook_before, settings_before):
    if hook_before is None:
        try:
            os.remove(hook)
        except FileNotFoundError:
            pass
    else:
        write_atomic(hook, hook_before, 0o755)
    if settings_before is not None:
        write_atomic(settings_path, settings_before)


def install(home, source, dry):
    src = read_bytes(source)
    if src is None:
        raise Stop(f"the hook file is missing: {source}")
    if PLACEHOLDER_RE.search(src):
        raise Stop(f"{source} carries placeholder text from the scrub; not installing it")
    hook, settings_path, hook_before, settings_before = plan_state(home)
    if settings_before is None:
        raise Stop(f"{settings_path} is missing; this is not a set-up ~/.claude (use --home to point at one)")
    settings = parse_settings(settings_before, settings_path)
    new_settings = json.loads(json.dumps(settings))
    added = add_entry(new_settings)
    copy_needed = hook_before != src
    print(f"hook file:      {'copy to ' + hook if copy_needed else 'already current at ' + hook}")
    print(f"settings entry: {'add to ' + settings_path if added else 'already registered in ' + settings_path}")
    if dry:
        return "DRY-RUN nothing changed"
    folder = None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        if copy_needed or added:
            folder = backup(home, stamp, hook_before, settings_before)
        if copy_needed:
            write_atomic(hook, src, 0o755)  # the file first: an entry that points at nothing breaks every Bash call
        elif hook_before is not None:
            os.chmod(hook, 0o755)
        if added:
            write_atomic(settings_path, dump(new_settings, settings_before))
        failure = smoke(home, parse_settings(read_bytes(settings_path), settings_path))
    except Stop:
        raise
    except Exception as e:
        failure = f"{type(e).__name__}: {e}"
    if failure:
        restore(hook, settings_path, hook_before, settings_before)
        raise Stop(f"{failure}; both files put back" + (f" (backup {folder})" if folder else ""), 1)
    return f"PASS the guard is installed and refuses host npm ci" + (f" (backup {folder})" if folder else " (nothing needed changing)")


def uninstall(home, dry):
    hook, settings_path, hook_before, settings_before = plan_state(home)
    if settings_before is None:
        raise Stop(f"{settings_path} is missing; nothing to remove from")
    settings = parse_settings(settings_before, settings_path)
    new_settings = json.loads(json.dumps(settings))
    removed = remove_entry(new_settings)
    print(f"settings entry: {'remove from ' + settings_path if removed else 'not registered in ' + settings_path}")
    print(f"hook file:      {'remove ' + hook if hook_before is not None else 'not present at ' + hook}")
    if dry:
        return "DRY-RUN nothing changed"
    folder = None
    if removed or hook_before is not None:
        folder = backup(home, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), hook_before, settings_before)
    try:
        if removed:  # the entry first: a missing file with the entry still in place breaks every Bash call
            write_atomic(settings_path, dump(new_settings, settings_before))
        if hook_before is not None:
            os.remove(hook)
        left = parse_settings(read_bytes(settings_path), settings_path)
        if registered(left) or os.path.exists(hook):
            raise Stop("the entry or the file is still there after removal")
    except Exception as e:
        restore(hook, settings_path, hook_before, settings_before)
        raise Stop(f"{e}; both files put back" + (f" (backup {folder})" if folder else ""), 1)
    return "PASS the guard is switched off: entry and file removed" + (f" (backup {folder})" if folder else "")


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--home", default=os.path.expanduser("~/.claude"), help="the folder to install into (default ~/.claude)")
    ap.add_argument("--source", default=os.path.join(here, "..", "hooks", HOOK_NAME), help="the hook file to copy")
    ap.add_argument("--uninstall", action="store_true", help="the off switch: remove the entry, then the file")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    home = os.path.abspath(os.path.expanduser(a.home))
    try:
        line = uninstall(home, a.dry_run) if a.uninstall else install(home, os.path.abspath(a.source), a.dry_run)
    except Stop as e:
        print(f"FAIL {e}")
        return e.code
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
