#!/usr/bin/env python3
"""Before asking the user to create ANY account, app, key or token: find what already exists.

Lists files under ~/repos and ~/.config that define variables whose NAME matches a pattern,
and prints file paths + variable names only — never values. Gitignored .env files included
(this shell's grep skips them silently).

  cred_names.py TWITCH            # TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, ...
  cred_names.py 'CLOUDFLARE|CF_'  # any Cloudflare credential
"""
import os, re, sys

if len(sys.argv) != 2:
    sys.exit(__doc__)
pat = re.compile(sys.argv[1], re.I)
ROOTS = [os.path.expanduser("~/repos"), os.path.expanduser("~/.config")]
SKIP = {"node_modules", ".git", ".next", "out", "dist", "build", ".pnpm-store", "coverage", ".wrangler", ".venv"}
VAR = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]", re.M)
home = os.path.expanduser("~")
n = 0
for root in ROOTS:
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP]
        for f in files:
            p = os.path.join(d, f)
            interesting = (f.startswith(".env") or f.endswith((".env", ".toml", ".yaml", ".yml", ".vars"))
                           or ".dev.vars" in f or pat.search(f))
            if not interesting:
                continue
            try:
                if os.path.getsize(p) > 200_000:
                    continue
                txt = open(p, errors="ignore").read()
            except OSError:
                continue
            names = sorted({v for v in VAR.findall(txt) if pat.search(v)})
            if names or (pat.search(f) and os.path.dirname(p).startswith(os.path.join(home, ".config"))):
                n += 1
                print(p.replace(home, "~"), "->", ", ".join(names) or "(file name matches; contents not shown)")
print(f"{n} file(s)")
