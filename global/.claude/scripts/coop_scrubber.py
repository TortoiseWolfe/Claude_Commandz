#!/usr/bin/env python3
"""Co-op scrubber: swap people's names, email addresses and phone numbers for placeholders before a vendor model sees
co-op operations text.

Three finders run over the same text and their spans are merged (the roster wins an overlap, then the longer span):

  roster     every name, email and phone number we hold, built at run time by coop_roster_build.py into the private file
             ~/.config/afa/coop-roster.json (mode 600, never committed).
  patterns   email addresses, and phone numbers written with separators or a leading +.
  Presidio   Microsoft's open source personal-data finder, run in a private venv by coop_scrubber_presidio.py. Optional:
             without the venv the other two still run, and the hook says so once.

Private config, never in a repo (a missing file means that part is off):

  ~/.config/afa/coop-scrubber.json
      {"folders": ["~/repos/Some-Folder*"], "allow": ["Name that must stay readable"], "ner": true}
      folders  patterns (`*` inside one segment, `**` across segments, `~/` is home). A session whose working folder
               sits under a match is a co-op session. No file or no folders: the scrubber is off everywhere.
      allow    words that must never become a placeholder (the operator's own name, for example).
      ner      false turns Presidio off.
  ~/.config/afa/coop-roster.json   {"salt": "...", "people": [{"names": [...], "emails": [...], "phones": [...]}]}

Placeholders carry no member detail. A roster person is [PERSON_<n>], [EMAIL_<n>], [PHONE_<n>] with n the person's
position in the roster, so the same person gets the same placeholder in every call. Anything found only by a pattern or
Presidio is [PERSON_x<4 hex>] and so on, the hex taken from a salted hash of the text.

Environment overrides (tests): COOP_SCRUBBER_CONFIG, COOP_ROSTER_FILE, COOP_SCRUBBER_PRESIDIO_PYTHON,
COOP_SCRUBBER_PRESIDIO_WORKER, COOP_SCRUBBER_LOG.
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

MAX_NER_CHARS = 200_000     # a longer string skips Presidio (roster and patterns still run)
MAX_NER_TOTAL = 1_000_000   # characters sent to Presidio in one call
NER_TIMEOUT = 25
BUILTIN_ALLOW = {"claude", "anthropic"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
# a phone number must show separators or a leading + (a bare run of digits is an id, a timestamp or a size)
PHONE_RE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[ .\-]?)?(?:\(\d{3}\)[ .\-]?|\d{3}[ .\-])\d{3}[ .\-]\d{4}(?![\w])"
    r"|(?<![\w.])\+\d{1,3}(?:[ .\-]?\d{2,4}){2,5}(?![\w])"
)
DIGITISH_RE = re.compile(r"(?<![\w.])\+?\(?\d[\d ().\-]{5,18}\d(?![\w])")


def home():
    return os.environ.get("HOME") or os.path.expanduser("~")


def config_file():
    return os.environ.get("COOP_SCRUBBER_CONFIG") or os.path.join(home(), ".config", "afa", "coop-scrubber.json")


def roster_file():
    return os.environ.get("COOP_ROSTER_FILE") or os.path.join(home(), ".config", "afa", "coop-roster.json")


def log_file():
    return os.environ.get("COOP_SCRUBBER_LOG") or os.path.join(home(), ".claude", "state", "coop-scrubber.log")


def presidio_python():
    return os.environ.get("COOP_SCRUBBER_PRESIDIO_PYTHON") or os.path.join(
        home(), ".local", "share", "coop-scrubber", "venv", "bin", "python")


def load_json(path):
    """The parsed file, or None when it is missing. A file that exists but cannot be parsed raises ValueError."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError("%s cannot be read (%s)" % (path, type(exc).__name__))


def load_config():
    cfg = load_json(config_file())
    if cfg is None:
        return {"folders": [], "allow": [], "ner": True}
    if not isinstance(cfg, dict):
        raise ValueError("config is not an object")
    return {
        "folders": [str(x) for x in cfg.get("folders", []) if isinstance(x, str)],
        "allow": [str(x) for x in cfg.get("allow", []) if isinstance(x, str)],
        "ner": cfg.get("ner", True) is not False,
    }


# ---------------------------------------------------------------- co-op folder test

def _glob_to_re(pattern):
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _expand(pattern):
    if pattern == "~" or pattern.startswith("~/"):
        pattern = home() + pattern[1:]
    return pattern.rstrip("/") or "/"


def is_coop_folder(cwd, folders):
    """True when cwd, or any folder above it, matches a pattern. The real path and the written path both count."""
    if not cwd or not folders:
        return False
    forms = {os.path.normpath(cwd)}
    try:
        forms.add(os.path.realpath(cwd))
    except OSError:
        pass
    regexes = [_glob_to_re(_expand(p)) for p in folders if p.strip()]
    for form in forms:
        parts = form.split("/")
        for i in range(2, len(parts) + 1):
            ancestor = "/".join(parts[:i]) or "/"
            if any(r.match(ancestor) for r in regexes):
                return True
    return False


# ---------------------------------------------------------------- roster

def normalize_phone(text):
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 7 else ""


def name_variants(name):
    """The written forms of one name that should match: 'First Last', 'Last, First', 'Last First'."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    if len(name) < 3 or (" " not in name and "," not in name):  # a one-word "name" is a label as often as a person: Presidio's job
        return []
    out = [name]
    if "," in name:
        last, _, first = [p.strip() for p in name.partition(",")]
        if first and last:
            out += ["%s %s" % (first, last), "%s %s" % (last, first)]
    else:
        parts = name.split(" ")
        if len(parts) >= 2:
            out += ["%s, %s" % (parts[-1], " ".join(parts[:-1])), "%s %s" % (parts[-1], " ".join(parts[:-1]))]
    return out


class Roster:
    def __init__(self, data=None):
        data = data or {}
        self.salt = str(data.get("salt") or "")
        self.names = {}    # lowercase written form -> person index
        self.emails = {}   # lowercase address -> person index
        self.phones = {}   # normalized digits -> person index
        for idx, person in enumerate(data.get("people", []), start=1):
            if not isinstance(person, dict):
                continue
            for n in person.get("names", []):
                for v in name_variants(n):
                    self.names.setdefault(v.lower(), idx)
            for e in person.get("emails", []):
                if isinstance(e, str) and "@" in e:
                    self.emails.setdefault(e.strip().lower(), idx)
            for p in person.get("phones", []):
                d = normalize_phone(p)
                if d:
                    self.phones.setdefault(d, idx)
        # longest first, so "Ann Lee-Smith" is taken before "Ann Lee"
        ordered = sorted(self.names, key=len, reverse=True)
        self._name_re = None
        if ordered:
            self._name_re = re.compile(r"(?<![\w])(?:%s)(?![\w])" % "|".join(re.escape(n) for n in ordered), re.I)

    @classmethod
    def load(cls):
        return cls(load_json(roster_file()))

    def empty(self):
        return not (self.names or self.emails or self.phones)

    def spans(self, text):
        out = []
        if self._name_re is not None:
            for m in self._name_re.finditer(text):
                out.append((m.start(), m.end(), "PERSON", self.names[m.group(0).lower()]))
        if self.emails:
            for m in EMAIL_RE.finditer(text):
                idx = self.emails.get(m.group(0).lower())
                if idx:
                    out.append((m.start(), m.end(), "EMAIL", idx))
        if self.phones:
            for m in DIGITISH_RE.finditer(text):
                idx = self.phones.get(normalize_phone(m.group(0)))
                if idx:
                    out.append((m.start(), m.end(), "PHONE", idx))
        return out


# ---------------------------------------------------------------- the three finders

def pattern_spans(text):
    out = [(m.start(), m.end(), "EMAIL", None) for m in EMAIL_RE.finditer(text)]
    out += [(m.start(), m.end(), "PHONE", None) for m in PHONE_RE.finditer(text)]
    return out


NOTE_ONCE = set()


def note_once(key, message, stream=None):
    if key in NOTE_ONCE:
        return
    NOTE_ONCE.add(key)
    (stream or sys.stderr).write("coop scrubber: %s\n" % message)


def presidio_spans(texts):
    """One list of (start, end, kind, None) per text, or None when Presidio could not run."""
    py = presidio_python()
    worker = os.environ.get("COOP_SCRUBBER_PRESIDIO_WORKER") or os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "coop_scrubber_presidio.py")
    if not os.path.exists(py):
        return None
    try:
        r = subprocess.run([py, worker], input=json.dumps({"texts": texts}), capture_output=True, text=True,
                           timeout=NER_TIMEOUT)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        return [[(int(a), int(b), k, None) for a, b, k in per] for per in data["spans"]]
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------- the scrubber

class Scrubber:
    def __init__(self, roster=None, allow=(), use_ner=True):
        self.roster = roster or Roster()
        self.allow = {a.strip().lower() for a in allow if a.strip()} | BUILTIN_ALLOW
        self.use_ner = use_ner
        self.counts = {"PERSON": 0, "EMAIL": 0, "PHONE": 0}
        self.ner_state = "off" if not use_ner else "unused"

    def placeholder(self, kind, idx, written):
        if idx:
            return "[%s_%d]" % (kind, idx)
        h = hashlib.sha256((self.roster.salt + "|" + written.strip().lower()).encode("utf-8")).hexdigest()[:4]
        return "[%s_x%s]" % (kind, h)

    def _merge(self, text, spans):
        # roster spans (index set) first, then longer, then earlier
        spans = [s for s in spans if s[1] > s[0] and text[s[0]:s[1]].strip().lower() not in self.allow]
        spans.sort(key=lambda s: (s[3] is None, -(s[1] - s[0]), s[0]))
        kept = []
        for s in spans:
            if all(s[1] <= k[0] or s[0] >= k[1] for k in kept):
                kept.append(s)
        kept.sort(key=lambda s: s[0])
        return kept

    def scrub_strings(self, texts):
        """Scrub a list of strings; returns the list, in the same order."""
        ner = [None] * len(texts)  # type: list
        if self.use_ner:
            budget, idxs = MAX_NER_TOTAL, []
            for i, t in enumerate(texts):
                if t and len(t) <= MAX_NER_CHARS and len(t) <= budget:
                    idxs.append(i)
                    budget -= len(t)
            if idxs:
                res = presidio_spans([texts[i] for i in idxs])
                if res is None:
                    self.ner_state = "unavailable"
                else:
                    self.ner_state = "on"
                    for i, per in zip(idxs, res):
                        ner[i] = per
        out = []
        for i, text in enumerate(texts):
            if not text:
                out.append(text)
                continue
            spans = self.roster.spans(text) + pattern_spans(text) + (ner[i] or [])
            kept = self._merge(text, spans)
            if not kept:
                out.append(text)
                continue
            pieces, pos = [], 0
            for a, b, kind, idx in kept:
                pieces.append(text[pos:a])
                pieces.append(self.placeholder(kind, idx, text[a:b]))
                self.counts[kind] = self.counts.get(kind, 0) + 1
                pos = b
            pieces.append(text[pos:])
            out.append("".join(pieces))
        return out

    def scrub(self, obj):
        """Scrub every string value inside obj, keeping its shape. Returns a new object."""
        strings = []

        def collect(o):
            if isinstance(o, str):
                strings.append(o)
            elif isinstance(o, dict):
                for v in o.values():
                    collect(v)
            elif isinstance(o, (list, tuple)):
                for v in o:
                    collect(v)

        collect(obj)
        done = iter(self.scrub_strings(strings))

        def rebuild(o):
            if isinstance(o, str):
                return next(done)
            if isinstance(o, dict):
                return {k: rebuild(v) for k, v in o.items()}
            if isinstance(o, list):
                return [rebuild(v) for v in o]
            if isinstance(o, tuple):
                return tuple(rebuild(v) for v in o)
            return o

        return rebuild(obj)


def log_counts(tool, scrubber):
    """One line per scrubbed call: time, tool, counts. Never any text."""
    try:
        path = log_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\tperson=%d\temail=%d\tphone=%d\tner=%s\n" % (
                datetime.datetime.now().isoformat(timespec="seconds"), tool, scrubber.counts["PERSON"],
                scrubber.counts["EMAIL"], scrubber.counts["PHONE"], scrubber.ner_state))
    except OSError:
        pass


def main(argv):
    """Command line: coop_scrubber.py scrub < text > scrubbed text; coop_scrubber.py status (counts only)."""
    cmd = argv[1] if len(argv) > 1 else "status"
    cfg = load_config()
    roster = Roster.load()
    if cmd == "status":
        print("folders=%d roster_names=%d roster_emails=%d roster_phones=%d ner=%s presidio_venv=%s" % (
            len(cfg["folders"]), len(roster.names), len(roster.emails), len(roster.phones), cfg["ner"],
            "yes" if os.path.exists(presidio_python()) else "no"))
        return 0
    if cmd == "scrub":
        s = Scrubber(roster, cfg["allow"], cfg["ner"])
        sys.stdout.write(s.scrub_strings([sys.stdin.read()])[0])
        return 0
    sys.stderr.write("usage: coop_scrubber.py status | scrub\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
