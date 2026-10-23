#!/usr/bin/env python3
"""Check the read-only production proof pins against the real files on this machine.

The plugin tests cannot read ~/repos (the test engine has no real filesystem), so this runs beside them:

    python3 mods/afa-mods/scripts/check-pins.py

It fails loudly (exit 1) when a pinned repo's scripts/portainer-deploy.sh no longer hashes to a DEPLOY_PINS entry, or when the repo's
dotenv file holds a key that DEPLOY_ENV_KEYS does not list. Either one means read-only production proofs are silently held again; a reviewed PR
updates prodread.ts. Only key NAMES of the dotenv file are read into the output; values are dropped at once and never printed.
"""
import hashlib
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "prodread.ts"
REPOS = Path.home() / "repos"


def main() -> int:
    text = SRC.read_text()
    pins_block = re.search(r"export const DEPLOY_PINS: DeployPins = \{(.*?)\n\}", text, re.S)
    keys_block = re.search(r"export const DEPLOY_ENV_KEYS: ReadonlySet<string> = new Set\(\[(.*?)\]\)", text, re.S)
    if not pins_block or not keys_block:
        print("check-pins: cannot find DEPLOY_PINS or DEPLOY_ENV_KEYS in prodread.ts")
        return 1
    pins: dict[str, list[str]] = {}
    for repo, body in re.findall(r"^\s*'?([\w-]+)'?:\s*\[(.*?)\]", pins_block.group(1), re.S | re.M):
        pins[repo] = re.findall(r"'([0-9a-f]{64})'", body)
    allowed = set(re.findall(r"'([A-Z0-9_]+)'", keys_block.group(1)))
    bad = 0
    for repo, hashes in pins.items():
        script = REPOS / repo / "scripts" / "portainer-deploy.sh"
        if not script.is_file():
            print(f"FAIL {repo}: {script} not found")
            bad += 1
            continue
        h = hashlib.sha256(script.read_bytes()).hexdigest()
        if h in hashes:
            print(f"ok   {repo}: portainer-deploy.sh {h[:12]} is pinned")
        else:
            print(f"FAIL {repo}: portainer-deploy.sh is {h[:12]}, pinned: {', '.join(x[:12] for x in hashes)}")
            bad += 1
        dotenv = REPOS / repo / (".env")
        if dotenv.is_file():
            names = set()
            for line in dotenv.read_text().split("\n"):
                if line and not line.startswith("#"):
                    names.add(line.split("=", 1)[0])
            extra = sorted(n for n in names if n not in allowed)
            if extra:
                print(f"FAIL {repo}: dotenv keys not on DEPLOY_ENV_KEYS: {', '.join(extra)}")
                bad += 1
            else:
                print(f"ok   {repo}: all {len(names)} dotenv keys are on the allowlist")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
