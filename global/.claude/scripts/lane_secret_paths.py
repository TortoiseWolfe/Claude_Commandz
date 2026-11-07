#!/usr/bin/env python3
"""The one list of settings files and folders a lane must never see, whatever engine it runs on (issue 332).

usage:
  lane_secret_paths.py hide <root>...     print what lane_hide_secrets.sh hides: "F<path>" for a file, "D<path>" for a folder, each ended by a NUL byte
(agy_deny.py imports is_secret_file and is_sealed_dir; tests/test_lane_secret_guard.py checks opencode_lane.sh against OPENCODE_READ_GLOBS.)

Why: on 6 and 7 Nov 2026 a free-engine lane opened an env file and a password went to an outside AI provider. PRs 316, 319 and 320 each patched one
engine with its own deny list. This file is the shared list; lane.sh hides these paths from the engine's process with a mount namespace
(lane_hide_secrets.sh), and the per-engine denies are a second layer built from the same names.

What is a settings file:
  - env files, by the name rule known_secrets_scan.py uses (.env, .env.*, *.env, .envrc), except placeholder names (example, sample, template, defaults, .dist);
  - key files: *.pem, *.p12, *.pfx, .netrc, id_rsa*, id_ed25519*, *api-key*;
  - folders named like sealed key stores (sealed, .sealed, sealed-keys, ...);
  - the fixed folders under the home folder: ~/.config/afa/secrets and ~/.config/afa/rotated-test-users.
Never read, print or copy the contents of any of them; this module only lists paths.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import known_secrets_scan as ks  # noqa: E402

# Key files, matched against the base name. Env files are matched by ks.ENV_NAME so there is one definition of an env file.
KEY_NAME = re.compile(r"^(.*\.pem|.*\.p12|.*\.pfx|\.netrc|id_rsa.*|id_ed25519.*|.*api-key.*)$")
# Folders that hold sealed keys, matched against the base name.
SEALED_DIR = re.compile(r"^\.?sealed([-_.].*)?$", re.I)
# Fixed folders, relative to the home folder.
HOME_DIRS = [".config/afa/secrets", ".config/afa/rotated-test-users"]
# Never walked into.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
# More paths than this means a runaway walk or a mount tree gone wrong; the caller fails closed.
MAX_PATHS = 5000

# The read globs opencode_lane.sh denies (OpenCode globs match the whole path, so "*" prefixes them).
OPENCODE_READ_GLOBS = ["*.env", "*.env.*", "*api-key*", "*.pem", "*.netrc", "*id_rsa*", "*id_ed25519*"]


def is_env_file(name):
    return bool(ks.ENV_NAME.match(name)) and not ks.ENV_SKIP.search(name)


def is_key_file(name):
    return bool(KEY_NAME.match(name))


def is_secret_file(name):
    return is_env_file(name) or is_key_file(name)


def is_sealed_dir(name):
    return bool(SEALED_DIR.match(name))


def hide_list(roots, home):
    """Return ([files], [dirs]) under the roots plus the fixed home folders. Raises OverflowError past MAX_PATHS."""
    files, dirs, seen = [], [], set()
    for d in HOME_DIRS:
        p = os.path.join(home, d)
        if os.path.isdir(p) and not os.path.islink(p):
            dirs.append(p)
            seen.add(p)
    for root in roots:
        root = os.path.realpath(root)
        if not os.path.isdir(root):
            continue
        for cur, subdirs, names in os.walk(root):
            keep = []
            for s in sorted(subdirs):
                if s in SKIP_DIRS:
                    continue
                full = os.path.join(cur, s)
                if is_sealed_dir(s):
                    # A link is hidden by what it points at: a bind mount follows the link.
                    target = os.path.realpath(full) if os.path.islink(full) else full
                    if os.path.isdir(target) and target not in seen:
                        seen.add(target)
                        dirs.append(target)
                    continue  # hidden whole, so no need to walk it
                keep.append(s)
            subdirs[:] = keep
            for n in sorted(names):
                full = os.path.join(cur, n)
                if is_secret_file(n):
                    target = os.path.realpath(full) if os.path.islink(full) else full
                    if os.path.isfile(target) and target not in seen:
                        seen.add(target)
                        files.append(target)
            if len(files) + len(dirs) > MAX_PATHS:
                raise OverflowError("more than %d settings paths under %s" % (MAX_PATHS, root))
    return files, dirs


def main(argv):
    if len(argv) >= 2 and argv[1] == "hide":
        roots = argv[2:]
        if not roots:
            print("lane_secret_paths: hide needs at least one root", file=sys.stderr)
            return 2
        try:
            files, dirs = hide_list(roots, os.path.expanduser("~"))
        except OverflowError as e:
            print("lane_secret_paths: %s" % e, file=sys.stderr)
            return 1
        out = sys.stdout.buffer
        for tag, group in ((b"D", dirs), (b"F", files)):
            for p in group:
                out.write(tag + os.fsencode(p) + b"\0")
        return 0
    print(__doc__.split("\n\n")[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
