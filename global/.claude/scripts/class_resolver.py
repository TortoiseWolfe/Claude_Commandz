#!/usr/bin/env python3
"""Class resolver (spec 002, FR-020 to FR-023): the L0 step that runs before any model sees an item.

  class_resolver.py [--json] < item.json      one item in, {"class", "rules", "fail_closed"} out

An item is a JSON object; every field is optional:
  text         the raw text (read here, never printed or logged)
  sender       the From line or address
  repos        repo names the item is explicitly about
  paths        paths the item is explicitly about
  prior_class  the class the thread or lane already had (it carries forward)
  model_class  a class a model proposed (it can raise the result, never lower it)

Classes are public, own and client; the strictest matching rule wins and every rule that matched is returned.
The five lists it reads are the ones `lane.sh` and the review panel's gate already read:
  clients     ~/repos/hub/client-senders.tsv   (private; column 2 repo_dir, column 4 gmail_from)
  repos       ~/repos/hub/client-repos.txt     (private; one path per line)
  cd          ~/repos/CD                       (a folder of client repos)
  classes     ~/.config/panel/classes.json     ({"repos": {"<name>": "public|own|client"}})
  terms       ~/.config/panel/pii-terms.txt    (private terms; one per line)
A list that is missing, unreadable or malformed makes the item client (fail closed): the rule
"list-unreadable:<name>" says which. The resolver never prints or returns text from an item or from a list
(a rule detail names a list entry's position, not its content, except repo names that the item itself carried).
Python 3 stdlib only."""

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field

STRICTNESS = {"public": 0, "own": 1, "client": 2}
HOME = os.path.expanduser("~")


def stricter(a, b):
    """The stricter of two classes. An unknown class counts as client."""
    ra, rb = STRICTNESS.get(a, 2), STRICTNESS.get(b, 2)
    return a if ra >= rb else b


@dataclass
class Config:
    clients: str = os.path.join(HOME, "repos/hub/client-senders.tsv")
    repos: str = os.path.join(HOME, "repos/hub/client-repos.txt")
    cd: str = os.path.join(HOME, "repos/CD")
    classes: str = os.path.join(HOME, ".config/panel/classes.json")
    terms: str = os.path.join(HOME, ".config/panel/pii-terms.txt")
    home: str = HOME


@dataclass
class Lists:
    """The five lists, loaded once. `bad` names every list that could not be read."""
    sender_patterns: list = field(default_factory=list)   # lowercase gmail_from substrings
    client_names: dict = field(default_factory=dict)      # lowercase repo name -> where it came from
    class_map: dict = field(default_factory=dict)         # lowercase repo name -> class
    terms: list = field(default_factory=list)
    cd_root: str = ""
    client_paths: list = field(default_factory=list)      # absolute paths from client-repos.txt and the senders file
    bad: list = field(default_factory=list)


def _expand(p, cfg):
    p = p.strip()
    if p.startswith("~/"):
        p = os.path.join(cfg.home, p[2:])
    return os.path.normpath(p)


def _read_regular(path):
    """Text of a readable regular file; raises OSError otherwise (a symlink to a file is fine)."""
    if not stat.S_ISREG(os.stat(path).st_mode):
        raise OSError("not a regular file")
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_lists(cfg=None):
    cfg = cfg or Config()
    L = Lists()

    try:   # senders: column 2 is a client repo dir, column 4 a sender pattern
        rows = [r.split("\t") for r in _read_regular(cfg.clients).splitlines()[1:] if r.strip() and not r.startswith("#")]
        for r in rows:
            if len(r) >= 4 and r[3].strip():
                L.sender_patterns.append(r[3].strip().lower())
            if len(r) >= 2 and r[1].strip():
                L.client_paths.append(_expand(r[1], cfg))
    except (OSError, UnicodeError):
        L.bad.append("clients")

    try:
        for line in _read_regular(cfg.repos).splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                L.client_paths.append(_expand(line, cfg))
    except (OSError, UnicodeError):
        L.bad.append("repos")

    try:
        L.cd_root = os.path.normpath(cfg.cd)
        for name in os.listdir(cfg.cd):
            L.client_names[name.lower()] = "cd"
    except OSError:
        L.bad.append("cd")

    try:
        data = json.loads(_read_regular(cfg.classes))["repos"]
        if not isinstance(data, dict):
            raise ValueError("repos")
        for k, v in data.items():
            L.class_map[str(k).lower()] = v if v in STRICTNESS else "client"
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        L.bad.append("classes")

    try:
        L.terms = [t.strip() for t in _read_regular(cfg.terms).splitlines() if t.strip() and not t.strip().startswith("#")]
    except (OSError, UnicodeError):
        L.bad.append("terms")

    for p in L.client_paths:
        L.client_names.setdefault(os.path.basename(p).lower(), "client-list")
    return L


# --- the rules -------------------------------------------------------------------------------------------
# Each rule is rule(item, lists, cfg) -> list of (class, detail). The name is the key in RULES, and the test
# file proves every one of them can go red.

def _word(text, s, e):
    """A hit counts when no letter touches it, so acme_corp and getAcmeUser() match but macmeal does not."""
    left = s == 0 or not text[s - 1].isalpha() or (text[s - 1].islower() and text[s].isupper())
    right = e == len(text) or not text[e].isalpha() or (text[e].isupper() and text[e - 1].islower())
    return left and right


def _find_word(text, needle):
    low, n, start = text.lower(), needle.lower(), 0
    while n and (i := low.find(n, start)) != -1:
        if _word(text, i, i + len(n)):
            return True
        start = i + 1
    return False


_PATH_RE = re.compile(r"(?<![\w-])(?:~|/home/[^/\s]+|\.)?/?repos/((?:\.worktrees/)?[A-Za-z0-9_.\-]+)(/[^\s'\"`]*)?")


def _paths_in(item):
    out = [p for p in (item.get("paths") or []) if isinstance(p, str)]
    out += [m.group(0) for m in _PATH_RE.finditer(item.get("text") or "")]
    return out


def _repo_of_path(p):
    """(repo name, in_cd) for a path under repos/, or None."""
    m = _PATH_RE.search(p)
    if not m:
        return None
    first = m.group(1)
    rest = m.group(2) or ""
    if first == "CD":
        return ("CD", True)
    if first.startswith(".worktrees/"):
        return (first[len(".worktrees/"):].split("--")[0], False)
    return (first, False)


def rule_sender(item, L, cfg):
    s = (item.get("sender") or "").lower()
    return [("client", "sender matches client-senders entry")] if s and any(p in s for p in L.sender_patterns) else []


def rule_client_repo(item, L, cfg):
    hits = []
    named = [str(r) for r in (item.get("repos") or [])]
    for p in _paths_in(item):
        got = _repo_of_path(p)
        if got:
            named.append(got[0])
    for r in named:
        base = r.rstrip("/").rsplit("/", 1)[-1].lower()
        if base == "cd" or L.client_names.get(base):
            hits.append(("client", f"repo {base} is a client repo"))
        else:
            ap = _expand(r, cfg) if "/" in r else ""
            if ap and (ap == L.cd_root or ap.startswith(L.cd_root + "/") or any(ap == c or ap.startswith(c + "/") for c in L.client_paths)):
                hits.append(("client", f"path under a client repo ({base})"))
    text = item.get("text") or ""
    for name, src in L.client_names.items():
        if len(name) >= 3 and _find_word(text, name):
            hits.append(("client", f"text names client repo {name}"))
    return hits


def rule_class_map(item, L, cfg):
    """A repo the item names that the class map lists client, or does not list at all."""
    hits = []
    named = [str(r) for r in (item.get("repos") or [])]
    for p in _paths_in(item):
        got = _repo_of_path(p)
        if got and got[0] != "CD":
            named.append(got[0])
    for r in named:
        base = r.rstrip("/").rsplit("/", 1)[-1].lower()
        if base == "cd":
            continue
        if base not in L.class_map:
            hits.append(("client", f"repo {base} is not in the class map"))
        else:
            hits.append((L.class_map[base], f"repo {base} is {L.class_map[base]} in the class map"))
    text = item.get("text") or ""
    for name, cls in L.class_map.items():
        if len(name) >= 3 and _find_word(text, name):
            hits.append((cls, f"text names {cls} repo {name}"))
    return hits


def rule_terms(item, L, cfg):
    text = item.get("text") or ""
    return [("client", "a private term appears") for t in L.terms if _find_word(text, t)][:1]


def rule_prior(item, L, cfg):
    pc = item.get("prior_class")
    return [(pc if isinstance(pc, str) and pc in STRICTNESS else "client", "class carries forward")] if pc else []


RULES = {
    "sender": rule_sender,
    "client-repo": rule_client_repo,
    "class-map": rule_class_map,
    "terms": rule_terms,
    "prior-class": rule_prior,
}


def resolve(item, cfg=None, lists=None):
    """The strictest class and every rule that matched. Fails closed on any unreadable list, and a model's
    proposed class can raise the result but never lower it."""
    cfg = cfg or Config()
    L = lists or load_lists(cfg)
    if not isinstance(item, dict):
        return {"class": "client", "rules": [{"rule": "bad-item", "class": "client", "detail": "item is not an object"}], "fail_closed": True}
    matched, cls = [], "public"
    for name in L.bad:
        matched.append({"rule": f"list-unreadable:{name}", "class": "client", "detail": "fail closed"})
    for name, fn in RULES.items():
        try:
            hits = fn(item, L, cfg)
        except Exception:   # a rule that breaks is a rule that matched client
            hits = [("client", "rule raised")]
        for c, detail in hits:
            matched.append({"rule": name, "class": c, "detail": detail})
    for m in matched:
        cls = stricter(cls, m["class"])
    if not any(m["rule"] in ("class-map", "client-repo", "prior-class") for m in matched) and not L.bad and cls == "public":
        cls = "own"   # nothing names a repo, so nothing says public
        matched.append({"rule": "no-repo-default", "class": "own", "detail": "no repo named; nothing says public"})
    mc = item.get("model_class")
    if mc is not None:
        raised = stricter(cls, mc if isinstance(mc, str) and mc in STRICTNESS else "client")
        if raised != cls:
            matched.append({"rule": "model-raised", "class": raised, "detail": "a model raised the class"})
        cls = raised
    return {"class": cls, "rules": matched, "fail_closed": bool(L.bad)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="print JSON (default)")
    ap.parse_args(argv)
    try:
        item = json.load(sys.stdin)
    except ValueError:
        item = None   # unparseable input is client
    try:
        result = resolve(item)
    except Exception:   # nothing a bad item or list does may end without a class
        result = {"class": "client", "rules": [{"rule": "resolver-error", "class": "client", "detail": "fail closed"}], "fail_closed": True}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
