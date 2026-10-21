#!/usr/bin/env python3
"""user_model.py: read, set or clear the USER default model in ~/.claude/settings.json, printing only that one key.

Why: Claude Code's /model also saves the choice as the user default, so `lane.sh --send <lane> "/model sonnet"` moved the hub's own
default to Sonnet (21 Oct). lane.sh --model reads the default with `get`, switches the lane, then puts it back with `set` or `unset`.
secret-guard blocks anything that would print settings.json, so this script is the one way lane.sh touches it: it prints the model
value and nothing else, and it writes only when the file round-trips through json byte for byte (so no other key or its layout moves).

  user_model.py get             the model value, or "-" when the key is absent
  user_model.py set <model>     set the key
  user_model.py unset           remove the key
  user_model.py check           "ok" when the file round-trips, else "differs" (exit 1)
The file is $USER_MODEL_SETTINGS, else $HOME/.claude/settings.json.
"""
import json
import os
import re
import sys
import tempfile


def path():
    return os.environ.get("USER_MODEL_SETTINGS") or os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def load(p):
    with open(p, encoding="utf-8") as f:
        text = f.read()
    data = json.loads(text)
    m = re.search(r"\n( +)\S", text)
    indent = len(m.group(1)) if m else 2
    return text, data, indent


def dump(data, indent, text):
    out = json.dumps(data, indent=indent, ensure_ascii=False)
    return out + ("\n" if text.endswith("\n") else "")


def main(argv):
    if not argv or argv[0] not in ("get", "set", "unset", "check"):
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2
    cmd, p = argv[0], path()
    try:
        text, data, indent = load(p)
    except (OSError, ValueError) as e:
        print("user_model: cannot read the settings file (%s)" % type(e).__name__, file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("user_model: the settings file is not a JSON object", file=sys.stderr)
        return 1
    same = dump(data, indent, text) == text
    if cmd == "check":
        print("ok" if same else "differs")
        return 0 if same else 1
    if cmd == "get":
        v = data.get("model")
        print(v if isinstance(v, str) and v else "-")
        return 0
    if not same:
        print("user_model: the settings file does not round-trip through json; not editing it", file=sys.stderr)
        return 1
    if cmd == "set":
        if len(argv) != 2 or not re.fullmatch(r"[A-Za-z0-9._\[\]:/-]+", argv[1]):
            print("user_model: set needs one model name", file=sys.stderr)
            return 2
        data["model"] = argv[1]
    else:
        data.pop("model", None)
    new = dump(data, indent, text)
    if new != text:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".user_model.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(new)
            os.chmod(tmp, os.stat(p).st_mode & 0o7777)
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
