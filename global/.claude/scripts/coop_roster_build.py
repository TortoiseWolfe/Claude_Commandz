#!/usr/bin/env python3
"""Build the co-op scrubber's roster at run time, from the member records kept on this machine.

  coop_roster_build.py [--paths-file F] [--out F] [--source PATH ...]

The sources are the paths in ~/.config/afa/no-ai-paths.txt (one path or glob per line, `~/` allowed, `**` crosses
folders), or the --source paths when given. The roster is written to ~/.config/afa/coop-roster.json with mode 600 and is
never committed. This script is the only thing that opens those records, and it prints COUNTS ONLY: how many files of
each kind it read, how many people, names, emails and phone numbers it found. It never prints a name, an address, a
number or a file path, and a file it cannot read is only counted.

What it understands (anything else is searched for emails and phone numbers only, and Presidio covers the names):
  csv, tsv        a header row; columns named like first name, last name, full or display name, email, phone, mobile
  json, jsonl     any object with keys named like those
  vcf             FN, N, EMAIL and TEL lines
  sql             a database dump: CREATE TABLE column lists plus INSERT rows (with or without a column list)
  other text      emails and phone numbers only

Environment overrides (tests): NO_AI_PATHS_FILE, COOP_ROSTER_FILE.
"""
import argparse
import csv
import glob
import io
import json
import os
import re
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import coop_scrubber as cs  # noqa: E402

TEXT_EXT = {".csv", ".tsv", ".json", ".jsonl", ".vcf", ".sql", ".txt", ".md", ".ldif", ".log"}
MAX_BYTES = 200 * 1024 * 1024
NOT_A_NAME = {"null", "none", "n/a", "na", "unknown", "anonymous", "admin", "test", "-"}
SKIP_WORDS = ("org", "company", "file", "user", "host", "domain", "site", "group", "role", "label", "title", "type",
              "street", "city", "state", "country", "campaign", "event", "mailing", "list", "template", "subject")


def classify(key):
    """What a column or key holds: email, phone, first, last, full or None."""
    k = re.sub(r"[^a-z0-9]+", " ", str(key).lower()).strip()
    if not k:
        return None
    if "email" in k or "e mail" in k:
        return None if any(w in k for w in ("type", "opt", "hold", "bulk", "on hold", "signature", "reply", "greeting")) else "email"
    if re.search(r"\b(phone|mobile|cell|tel|telephone)\b", k):
        return None if any(w in k for w in ("type", "ext", "country", "code", "location")) else "phone"
    if any(w in k for w in SKIP_WORDS):
        return None
    if re.search(r"\b(first|given|forename)\b", k) or k in ("firstname", "fname"):
        return "first"
    if re.search(r"\b(last|surname|family)\b", k) or k in ("lastname", "lname"):
        return "last"
    if k in ("name", "full name", "fullname", "display name", "displayname", "sort name", "contact name",
             "member name", "person", "real name", "realname", "preferred name"):
        return "full"
    return None


class Collector:
    def __init__(self):
        self.people = []        # list of {"names": set, "emails": set, "phones": set}
        self.by_email = {}
        self.by_phone = {}
        self.files = {}
        self.skipped = 0

    def add(self, names=(), emails=(), phones=()):
        names = {n for n in (clean_name(x) for x in names) if n}
        emails = {e.strip().lower() for e in emails if isinstance(e, str) and cs.EMAIL_RE.fullmatch(e.strip())}
        phones = {p for p in (cs.normalize_phone(x) for x in phones if isinstance(x, str)) if p}
        if not (names or emails or phones):
            return
        person = None
        for e in emails:
            person = person or self.by_email.get(e)
        for p in phones:
            person = person or self.by_phone.get(p)
        if person is None:
            person = {"names": set(), "emails": set(), "phones": set()}
            self.people.append(person)
        person["names"] |= names
        person["emails"] |= emails
        person["phones"] |= phones
        for e in person["emails"]:
            self.by_email.setdefault(e, person)
        for p in person["phones"]:
            self.by_phone.setdefault(p, person)

    def add_record(self, record):
        """record: a dict of column -> value (strings). Returns True when it carried a recognised column."""
        parts = {"email": [], "phone": [], "first": [], "last": [], "full": []}
        for key, value in record.items():
            kind = classify(key)
            if kind and isinstance(value, str) and value.strip():
                parts[kind].append(value.strip())
        if not any(parts.values()):
            return False
        names = list(parts["full"])
        if parts["first"] and parts["last"]:
            names.append("%s %s" % (parts["first"][0], parts["last"][0]))
        self.add(names, parts["email"], parts["phone"])
        return True

    def to_roster(self, salt):
        people = []
        for p in self.people:
            people.append({"names": sorted(p["names"]), "emails": sorted(p["emails"]), "phones": sorted(p["phones"])})
        people.sort(key=lambda p: ((p["emails"] or p["names"] or p["phones"] or [""])[0]).lower())
        return {"salt": salt, "people": people}


def clean_name(value):
    if not isinstance(value, str):
        return ""
    v = re.sub(r"\s+", " ", value.strip().strip("\"'"))
    if len(v) < 3 or len(v) > 80 or v.lower() in NOT_A_NAME or "@" in v:
        return ""
    if not re.search(r"[A-Za-z]{2}", v) or sum(c.isdigit() for c in v) > 2:
        return ""
    return v


# ---------------------------------------------------------------- readers

def read_csv(text, collector, delimiter):
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next(reader, None)
    if not header:
        return
    for row in reader:
        collector.add_record({h: (row[i] if i < len(row) else "") for i, h in enumerate(header)})


def walk_json(obj, collector):
    if isinstance(obj, dict):
        flat = {k: v for k, v in obj.items() if isinstance(v, str)}
        collector.add_record(flat)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                walk_json(v, collector)
    elif isinstance(obj, list):
        for v in obj:
            walk_json(v, collector)


def read_json(text, collector, lines):
    if lines:
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    walk_json(json.loads(line), collector)
                except ValueError:
                    pass
        return
    walk_json(json.loads(text), collector)


def read_vcf(text, collector):
    card = None
    for raw in text.replace("\r\n", "\n").replace("\n ", "").split("\n"):
        line = raw.strip()
        up = line.upper()
        if up == "BEGIN:VCARD":
            card = {"names": [], "emails": [], "phones": []}
        elif up == "END:VCARD" and card is not None:
            collector.add(card["names"], card["emails"], card["phones"])
            card = None
        elif card is not None and ":" in line:
            prop, _, value = line.partition(":")
            prop = prop.split(";")[0].upper()
            if prop == "FN":
                card["names"].append(value)
            elif prop == "N":
                bits = value.split(";")
                if len(bits) >= 2 and bits[0].strip() and bits[1].strip():
                    card["names"].append("%s %s" % (bits[1].strip(), bits[0].strip()))
            elif prop == "EMAIL":
                card["emails"].append(value)
            elif prop == "TEL":
                card["phones"].append(value)


CREATE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?([\w$]+)[`\"]?\s*\(", re.I)
INSERT_RE = re.compile(r"INSERT\s+(?:IGNORE\s+)?INTO\s+[`\"]?([\w$]+)[`\"]?\s*(\([^)]*\))?\s*VALUES\s*", re.I)


def split_tuples(text, pos):
    """From pos (just after VALUES), yield each parenthesised row as a list of strings or None, until the statement's ;"""
    n = len(text)
    while pos < n:
        while pos < n and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= n or text[pos] == ";" or text[pos] != "(":
            return
        pos += 1
        row, cur, in_q, quote = [], [], False, ""
        was_quoted = False
        while pos < n:
            c = text[pos]
            if in_q:
                if c == "\\" and pos + 1 < n:
                    nxt = text[pos + 1]
                    cur.append({"n": "\n", "t": "\t", "r": "\r", "0": ""}.get(nxt, nxt))
                    pos += 2
                    continue
                if c == quote:
                    if pos + 1 < n and text[pos + 1] == quote:
                        cur.append(quote)
                        pos += 2
                        continue
                    in_q = False
                else:
                    cur.append(c)
            elif c in "'\"":
                in_q, quote, was_quoted = True, c, True
            elif c == ",":
                row.append(finish(cur, was_quoted))
                cur, was_quoted = [], False
            elif c == ")":
                row.append(finish(cur, was_quoted))
                pos += 1
                break
            else:
                cur.append(c)
            pos += 1
        yield row


def finish(cur, was_quoted):
    value = "".join(cur)
    if not was_quoted:
        value = value.strip()
        if value.upper() == "NULL":
            return None
    return value


def read_sql(text, collector):
    columns = {}
    for m in CREATE_RE.finditer(text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        names = []
        for line in text[m.end():i - 1].split("\n"):
            lm = re.match(r"\s*[`\"]?([A-Za-z_][\w$]*)[`\"]?\s+\w", line)
            if lm and lm.group(1).upper() not in ("PRIMARY", "KEY", "UNIQUE", "CONSTRAINT", "INDEX", "FOREIGN", "FULLTEXT"):
                names.append(lm.group(1))
        columns[m.group(1)] = names
    for m in INSERT_RE.finditer(text):
        if m.group(2):
            cols = [c.strip().strip("`\"") for c in m.group(2).strip("()").split(",")]
        else:
            cols = columns.get(m.group(1), [])
        if not cols or not any(classify(c) for c in cols):
            continue
        for row in split_tuples(text, m.end()):
            collector.add_record({c: v for c, v in zip(cols, row) if isinstance(v, str)})


def read_text_fallback(text, collector):
    for m in cs.EMAIL_RE.finditer(text):
        collector.add((), [m.group(0)], ())
    for m in cs.PHONE_RE.finditer(text):
        collector.add((), (), [m.group(0)])


def read_file(path, collector):
    ext = os.path.splitext(path)[1].lower()
    if ext not in TEXT_EXT or os.path.getsize(path) > MAX_BYTES:
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    try:
        if ext in (".csv", ".tsv"):
            read_csv(text, collector, "\t" if ext == ".tsv" else ",")
        elif ext in (".json", ".jsonl"):
            read_json(text, collector, ext == ".jsonl")
        elif ext == ".vcf":
            read_vcf(text, collector)
        elif ext == ".sql":
            read_sql(text, collector)
    except (ValueError, csv.Error):
        collector.skipped += 1
    read_text_fallback(text, collector)
    return ext.lstrip(".")


# ---------------------------------------------------------------- sources and output

def source_paths(paths_file):
    out = []
    with open(paths_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def expand_sources(entries):
    files = []
    for entry in entries:
        entry = os.path.expanduser(entry) if entry.startswith("~") else entry
        for hit in glob.glob(entry, recursive=True) or []:
            if os.path.isdir(hit):
                for root, _unused, names in os.walk(hit):
                    files += [os.path.join(root, n) for n in names]
            elif os.path.isfile(hit):
                files.append(hit)
    return sorted(set(files))


def write_roster(path, roster):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(roster, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the co-op scrubber roster (counts only on stdout).")
    ap.add_argument("--paths-file", default=os.environ.get("NO_AI_PATHS_FILE") or os.path.join(cs.home(), ".config", "afa", "no-ai-paths.txt"))
    ap.add_argument("--out", default=cs.roster_file())
    ap.add_argument("--source", action="append", default=[])
    args = ap.parse_args(argv)
    try:
        entries = args.source or source_paths(args.paths_file)
    except OSError:
        print("coop roster: the source list cannot be read; nothing written", file=sys.stderr)
        return 2
    files = expand_sources(entries)
    collector = Collector()
    for path in files:
        try:
            kind = read_file(path, collector)
        except (OSError, ValueError):
            collector.skipped += 1
            continue
        if kind is None:
            collector.skipped += 1
        else:
            collector.files[kind] = collector.files.get(kind, 0) + 1
    try:
        salt = (cs.load_json(args.out) or {}).get("salt") or secrets.token_hex(8)
    except ValueError:
        salt = secrets.token_hex(8)
    roster = collector.to_roster(salt)
    if not roster["people"]:
        print("coop roster: found no one in %d files; the existing roster is left alone" % len(files), file=sys.stderr)
        return 3
    write_roster(args.out, roster)
    print("coop roster: %d people, %d names, %d emails, %d phones; files read by kind: %s; skipped %d" % (
        len(roster["people"]), sum(len(p["names"]) for p in roster["people"]), sum(len(p["emails"]) for p in roster["people"]),
        sum(len(p["phones"]) for p in roster["people"]),
        ", ".join("%s %d" % kv for kv in sorted(collector.files.items())) or "none", collector.skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
