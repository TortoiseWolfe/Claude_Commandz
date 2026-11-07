#!/usr/bin/env python3
"""Write the deny rules an agy lane starts with (agy --settings <file>).

usage: agy_deny.py <checkout> <settings.json>

Why: until 6 Nov 2026 lane.sh started agy with no deny rules at all. The only guard was a sentence at the top of the prompt, so a .env inside the
checkout was readable, and the "no push to main" rule existed only for OpenCode lanes (opencode_lane.sh, #316). agy takes a per-session settings
file ("--settings": a JSON object of settings.json keys, never saved). Its permissions are `{"permissions": {"deny": ["action(target)", ...]}}`,
deny beats ask and allow, and a deny on read_file also blocks write_file on that path (documented at antigravity.google/docs/permissions).

What is written:
  read_file(<absolute path>)   for every env file, key file and certificate found in the checkout when the lane starts (this script runs at every
                               start and restart, so files added since are caught at the next one), plus the same names relative to the checkout
                               root, plus ~/.ssh, ~/.config, ~/.gnupg, ~/.aws and ~/.netrc.
  write_file(.git/hooks/), write_file(.git/config), write_file(~/.gitconfig)   git settings stay as they are.
  command(regex:...)           pushes to main or master (also HEAD:main, refs/heads/main), forced, mirrored and all-branch pushes, install_scripts.py,
                               env and printenv, anything naming an env file or key, git config and git -c. Commands match word by word, or by
                               regex anchored at both ends, so each pattern here starts and ends with .*.

Limits, stated plainly: agy's docs name `*` as the only wildcard, so env files are listed by exact path, not by pattern; a file created after the
lane starts is not covered until the next start. Headless agy cannot run commands at all (lane.sh runs it with --mode accept-edits --sandbox), so
the command rules are a second wall behind that one. As with the OpenCode lane, the firm fixes are GitHub branch protection on main (set 6 Nov
2026) and keeping keys out of the lane's environment.
"""
import json
import os
import re
import sys

# File names that hold secrets, matched against the base name.
SECRET_NAME = re.compile(
    r"^(\.env(\..*)?|.*\.env|.*\.pem|\.netrc|id_rsa.*|id_ed25519.*|.*api-key.*)$"
)
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
MAX_FILES = 400

# Relative names at the checkout root, in case the checkout has not got the file yet but a lane writes then reads it.
ROOT_NAMES = [".env", ".env.local", ".env.development", ".env.production", ".env.test", ".env.staging", ".netrc"]

COMMAND_DENY = [
    r".*git.*push.*(--force|--mirror|--all|--delete).*",
    r".*git.*push.* -f.*",
    r".*git.*push.* \+.*",
    r".*git.*push.* (main|master)( .*)?",
    r".*git.*push.*:(main|master)( .*)?",
    r".*git.*push.*refs/heads/(main|master).*",
    r".*install_scripts\.py.*",
    r"printenv.*",
    r"env( .*)?",
    r"export -p.*",
    r".*/proc/.*/environ.*",
    r".*API_KEY.*",
    r".*\.env.*",
    r".*api-key.*",
    r".*\.pem.*",
    r".*id_rsa.*",
    r".*id_ed25519.*",
    r".*git.*config.*",
    r".*git .*-c .*",
    r".*git.*--config-env.*",
    r".*GIT_CONFIG.*",
    r".*core\.hooksPath.*",
    r".*\.gitconfig.*",
    r".*\.git/(config|hooks).*",
]


def secret_files(checkout):
    found = []
    for root, dirs, files in os.walk(checkout):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if SECRET_NAME.match(f):
                found.append(os.path.join(root, f))
                if len(found) >= MAX_FILES:
                    return found
    return found


def build(checkout, home):
    checkout = os.path.realpath(checkout)
    deny = []
    for p in secret_files(checkout):
        deny.append(f"read_file({p})")
    for n in ROOT_NAMES:
        deny.append(f"read_file({n})")
    for d in (".ssh", ".config", ".gnupg", ".aws"):
        deny.append(f"read_file({home}/{d}/)")
    deny.append(f"read_file({home}/.netrc)")
    deny += ["write_file(.git/hooks/)", "write_file(.git/config)", f"write_file({home}/.gitconfig)"]
    deny += [f"command(regex:{r})" for r in COMMAND_DENY]
    seen, out = set(), []
    for d in deny:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return {"permissions": {"deny": out}}


def main(argv):
    if len(argv) != 3:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    checkout, dest = argv[1], argv[2]
    if not os.path.isdir(checkout):
        print(f"agy_deny: not a folder: {checkout}", file=sys.stderr)
        return 1
    settings = build(checkout, os.path.expanduser("~"))
    tmp = dest + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(settings, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, dest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
