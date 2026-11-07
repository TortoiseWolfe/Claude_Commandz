#!/usr/bin/env python3
"""lane_prompt.py: fill a lane prompt template and make the lane folder. No model, no network.

A lane prompt starts as a template with {{name}} slots (absorb, backlog, weekly review and so on). Filling one by hand means
reading the whole template, copying it, and hoping no slot is left. This does it in one command and refuses a typo.

  lane_prompt.py list                                    each template and the slots it has
  lane_prompt.py fill TEMPLATE --slug S [--var k=v ...]  makes <lanes-dir>/<date>-<slug>/prompt.md and prints the folder
  lane_prompt.py fill TEMPLATE --slug S --check          the same checks, nothing written

TEMPLATE is a name (absorb finds <lanes-dir>/absorb-template.md) or a path to a file.
{{lane_dir}} (the new folder) and {{date}} (YYYY-MM-DD) are filled by this command unless given. Any other slot needs
--var name=value; a long value comes from a file with --var name=@path. A --var the template has no slot for is refused (a typo
would otherwise vanish), a slot with no value is refused (a half-filled prompt would reach a lane), and a folder that already holds a
prompt.md is never overwritten. Values go in as plain text: a value that itself holds {{x}} is not filled again.

--lanes-dir defaults to $LANES_DIR, then ~/.claude/state/lanes. Exit 0 done, 2 refused.
"""
import argparse
import datetime
import os
import re
import sys
from pathlib import Path

SLOT = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
AUTO = ("lane_dir", "date")


def lanes_dir(arg):
    return Path(arg or os.environ.get("LANES_DIR") or Path.home() / ".claude/state/lanes")


def find_template(name, base):
    p = Path(name).expanduser()
    if ("/" in name or name.endswith(".md")) and p.is_file():
        return p
    q = base / ("%s-template.md" % name)
    return q if q.is_file() else None


def slots(text):
    return sorted(set(SLOT.findall(text)))


def read_value(raw):
    if raw.startswith("@"):
        return Path(raw[1:]).expanduser().read_text()
    return raw


def fill(text, values):
    return SLOT.sub(lambda m: values[m.group(1)], text)


def cmd_list(a):
    base = lanes_dir(a.lanes_dir)
    for t in sorted(base.glob("*-template.md")):
        names = [s for s in slots(t.read_text()) if s not in AUTO]
        print("%s: %s" % (t.name[: -len("-template.md")], ", ".join(names) if names else "(no slots)"))
    return 0


def cmd_fill(a):
    base = lanes_dir(a.lanes_dir)
    tpl = find_template(a.template, base)
    if tpl is None:
        print("lane_prompt: no template %r (looked for %s-template.md in %s)" % (a.template, a.template, base), file=sys.stderr)
        return 2
    if not SLUG.match(a.slug):
        print("lane_prompt: slug must be lower-case letters, digits and hyphens", file=sys.stderr)
        return 2
    try:
        day = datetime.date.fromisoformat(a.date) if a.date else datetime.date.today()
    except ValueError:
        print("lane_prompt: --date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    folder = base / ("%s-%s" % (day.isoformat(), a.slug))
    text = tpl.read_text()
    have = set(slots(text))
    given = {}
    for item in a.var:
        if "=" not in item:
            print("lane_prompt: --var needs name=value, got %r" % item.split("=")[0], file=sys.stderr)
            return 2
        k, v = item.split("=", 1)
        try:
            given[k] = read_value(v)
        except OSError as e:
            print("lane_prompt: cannot read the file for %s: %s" % (k, e.strerror), file=sys.stderr)
            return 2
    unknown = sorted(set(given) - have)
    if unknown:
        print("lane_prompt: the template has no slot for: %s (it has: %s)" % (", ".join(unknown), ", ".join(sorted(have)) or "none"), file=sys.stderr)
        return 2
    values = {"lane_dir": str(folder), "date": day.isoformat()}
    values.update(given)
    missing = sorted(have - set(values))
    if missing:
        print("lane_prompt: no value for: %s" % ", ".join(missing), file=sys.stderr)
        return 2
    target = folder / "prompt.md"
    if target.exists():
        print("lane_prompt: %s already exists; not overwriting" % target, file=sys.stderr)
        return 2
    if a.check:
        print("ok: would write %s" % target)
        return 0
    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(fill(text, values))
    print(folder)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--lanes-dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    f = sub.add_parser("fill")
    f.add_argument("template")
    f.add_argument("--slug", required=True)
    f.add_argument("--var", action="append", default=[])
    f.add_argument("--date")
    f.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    return cmd_list(a) if a.cmd == "list" else cmd_fill(a)


if __name__ == "__main__":
    sys.exit(main())
