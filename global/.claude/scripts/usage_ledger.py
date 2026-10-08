#!/usr/bin/env python3
"""Per-client token and time ledger for Claude Code usage on this machine.

  usage_ledger.py ingest [--quiet] [--full] [--fold REGEX ...]
  usage_ledger.py report --since YYYY-MM-DD [--until YYYY-MM-DD] [--by client|repo|day|model] [--csv]
                         [--with-subagents] [--literal-prompts]
  usage_ledger.py tag SESSION --client NAME      (session-level client override)
  usage_ledger.py untag SESSION
  usage_ledger.py clients                        (repo -> client mapping actually seen, with token totals)

Reads ~/.claude/projects/**/*.jsonl incrementally into ~/.local/share/ledger/ledger.db (dir mode 700).
Stores METADATA ONLY (ids, timestamps, token counts, repo names): never message text, never secrets.
Client attribution is resolved at REPORT time (repo -> ~/.config/ledger/clients.json globs, then session
tags), so editing the map or tagging a session never needs a re-ingest. Python 3 stdlib only."""

import argparse
import csv
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

# ---- price table -------------------------------------------------------------------------------
# API-EQUIVALENT list prices, USD per million tokens. The subscription is flat, so these are a yardstick
# for "what would this have cost on the API", not money spent. VERIFY before quoting anyone: figures are
# the claude-api skill's model table cached 2026-09-25 (https://platform.claude.com/docs/en/about-claude/pricing).
# Keyed by model family; PRICE_OVERRIDES wins for an exact model id (Opus 5.5 is cheaper than the rest of opus).
# Cache writes bill as a multiple of the input price: 5-minute TTL 1.25x, 1-hour TTL 2x (also verify).
PRICES = {
    "opus": {"in": 5.0, "out": 25.0, "read": 0.50},
    "sonnet": {"in": 2.0, "out": 10.0, "read": 0.20},
    "haiku": {"in": 1.0, "out": 5.0, "read": 0.10},
    "fable": {"in": 10.0, "out": 50.0, "read": 0.25},
    "other": {"in": 5.0, "out": 25.0, "read": 0.50},   # unknown family: priced like opus rather than hidden
}
PRICE_OVERRIDES = {"claude-opus-5-5": {"in": 4.0, "out": 20.0, "read": 0.20}}
CACHE_WRITE_MULT = {"5m": 1.25, "1h": 2.0}
# opus-equivalent weights: (input+output+cache_write) * weight, cache reads at CACHE_READ_WEIGHT of that.
# fable has no stated weight; 8 = 2x opus, tracking its 2x price. Unknown families weigh like opus.
WEIGHTS = {"opus": 4, "sonnet": 2, "haiku": 1, "fable": 8, "other": 4}
CACHE_READ_WEIGHT = 0.1
FAMILIES = ("opus", "sonnet", "haiku", "fable", "other")

def default_author_emails():
    """Fallback commit identity when clients.json has no "author_emails": `git config --global user.email`.
    Kept out of this file so it can live in a public repo."""
    r = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True)
    return [r.stdout.strip()] if r.stdout.strip() else []
HUMAN_GAP, HUMAN_FLOOR, AGENT_GAP = 15 * 60, 5 * 60, 10 * 60   # seconds
HUB, HOME_LABEL = "repos (hub)", "~ (home)"
# Folders under ~/repos that hold repos rather than being one: worktrees, and the group folders.
CONTAINERS = (".worktrees", "CD", "_vendor", "_loose")
# Suffixes stripped when git cannot answer (directory deleted). Extend via clients.json "fold" or --fold.
DEFAULT_FOLD = [r"-wf-.*", r"-director", r"-integrate", r"-graphify", r"-seo", r"-tmux-.*", r"-pin-.*",
                r"-sitecheck", r"-blog-.*", r"-owned-parts", r"-ai-kitchen"]
# Transcript shapes: <slug>/<sid>.jsonl, <slug>/<sid>/subagents/agent-*.jsonl, .../subagents/workflows/wf_*/agent-*.jsonl
PATH_RE = re.compile(r"^(?P<slug>[^/]+)/(?:(?P<sid>[^/]+)\.jsonl|(?P<sid2>[^/]+)/subagents/(?:workflows/wf_[^/]+/)?agent-[^/]+\.jsonl)$")


class Env:
    """Every filesystem location in one place so tests can point the whole tool at a temp dir."""

    def __init__(self, home=None):
        self.home = os.path.abspath(home or os.path.expanduser("~"))
        self.repos_root = os.path.join(self.home, "repos")
        self.projects_dir = os.path.join(self.home, ".claude", "projects")
        self.ledger_dir = os.path.join(self.home, ".local", "share", "ledger")
        self.db_path = os.path.join(self.ledger_dir, "ledger.db")
        self.panel_log = os.path.join(self.ledger_dir, "panel.jsonl")
        self.clients_path = os.path.join(self.home, ".config", "ledger", "clients.json")


def slugify(path):
    """Claude Code's project-dir slug: every non-alphanumeric character becomes '-' (so it is lossy)."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def model_family(model):
    m = (model or "").lower()
    for fam in FAMILIES[:4]:
        if fam in m:
            return fam
    return "other"


class Resolver:
    """cwd (or project-dir slug) -> (parent repo name, parent repo path or None). Caches every git call."""

    def __init__(self, env, extra_fold=None, use_git=True):
        self.env, self.use_git = env, use_git
        self.fold_re = re.compile("(?:" + "|".join(DEFAULT_FOLD + list(extra_fold or [])) + ")$")
        self._git, self._top, self._slugs = {}, {}, None

    def fold_name(self, name):
        prev = None
        while prev != name:
            prev, name = name, self.fold_re.sub("", name) or name
        return name

    def git_parent(self, d):
        """Parent repo working-dir for directory d via --git-common-dir (worktrees fold to their parent)."""
        if d in self._git:
            return self._git[d]
        res = None
        if self.use_git and os.path.isdir(d):
            try:
                r = subprocess.run(["git", "-C", d, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                   capture_output=True, text=True, timeout=15,
                                   env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
                common = r.stdout.strip().rstrip("/") if r.returncode == 0 else ""
                if common and os.path.basename(common) == ".git":   # bare repos and submodules: don't guess
                    parent = os.path.dirname(common)
                    # a plain folder inside the hub (or $HOME) would "resolve" to the enclosing repo: reject
                    bad = {os.path.realpath(self.env.repos_root), os.path.realpath(self.env.home), "/"}
                    if os.path.realpath(parent) not in bad:
                        res = parent
            except (OSError, subprocess.SubprocessError):
                res = None
        self._git[d] = res
        return res

    def resolve_top(self, top):
        """top = a directory name directly under ~/repos."""
        if top in self._top:
            return self._top[top]
        d = os.path.join(self.env.repos_root, top)
        parent = self.git_parent(d)
        if parent:
            out = (os.path.basename(parent), parent)
        else:
            base = top.split(os.sep)[-1]
            if top.startswith(".worktrees" + os.sep):
                base = base.split("--")[0]        # <repo>--<topic> naming
            name = self.fold_name(base)
            p = os.path.join(self.env.repos_root, name)
            out = (name, p if os.path.isdir(os.path.join(p, ".git")) else None)
        self._top[top] = out
        return out

    def for_cwd(self, cwd):
        cwd = os.path.normpath(cwd)
        root = os.path.normpath(self.env.repos_root)
        if cwd == root:
            return HUB, root
        if cwd.startswith(root + os.sep):
            parts = cwd[len(root) + 1:].split(os.sep)
            top = parts[0]
            if top in CONTAINERS and len(parts) > 1:
                top = os.path.join(top, parts[1])
            return self.resolve_top(top)
        if cwd == os.path.normpath(self.env.home):
            return HOME_LABEL, None
        parent = self.git_parent(cwd)
        if parent:
            return os.path.basename(parent), parent
        return "other:" + (os.path.basename(cwd) or cwd), None

    def for_slug(self, slug):
        """Fallback for lines with no cwd: decode the lossy project-dir slug against the real ~/repos listing."""
        root = slugify(self.env.repos_root)
        if slug == root:
            return HUB, self.env.repos_root
        if slug == slugify(self.env.home):
            return HOME_LABEL, None
        if not slug.startswith(root + "-"):
            return "other:" + slug, None
        rest = slug[len(root) + 1:]
        if rest.startswith(slugify(".worktrees") + "-"):            # ~/repos/.worktrees/<name>
            return self.resolve_top(os.path.join(".worktrees", rest[len(slugify(".worktrees")) + 1:]))
        if self._slugs is None:
            try:
                names = os.listdir(self.env.repos_root)
            except OSError:
                names = []
            self._slugs = sorted(((slugify(n), n) for n in names), key=lambda t: -len(t[0]))
        for s, n in self._slugs:
            if rest == s or rest.startswith(s + "-"):
                if n in CONTAINERS and rest != s:                       # CD-cd-hubzilla -> CD/cd-hubzilla
                    sub = rest[len(s) + 1:]
                    try:
                        kids = os.listdir(os.path.join(self.env.repos_root, n))
                    except OSError:
                        kids = []
                    for ks, k in sorted(((slugify(k), k) for k in kids), key=lambda t: -len(t[0])):
                        if sub == ks or sub.startswith(ks + "-"):
                            return self.resolve_top(os.path.join(n, k))
                return self.resolve_top(n)
        return self.fold_name(rest.split("--")[0]), None   # "--" is "/." (hidden dir, e.g. .claude/worktrees)


def load_clients(path):
    """-> {"rules": [(client, [globs])], "notes": {client: note}, "fold": [regex]}. Missing file = no rules."""
    cfg = {"rules": [], "notes": {}, "fold": [], "author_emails": []}
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return cfg
    for c in raw.get("clients", []):
        cfg["rules"].append((c["client"], list(c.get("repos", []))))
        if c.get("note"):
            cfg["notes"][c["client"]] = c["note"]
    cfg["fold"] = list(raw.get("fold", []))
    cfg["author_emails"] = list(raw.get("author_emails", []))
    return cfg


def client_for(cfg, repo, session=None, tags=None):
    """Session tag override, else first matching glob on the parent repo name, else own:<repo>."""
    if tags and session in tags:
        return tags[session]
    for client, globs in cfg["rules"]:
        if any(fnmatch.fnmatchcase(repo, g) for g in globs):
            return client
    return "own:" + repo


# ---- database ----------------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, offset INTEGER);
CREATE TABLE IF NOT EXISTS tokens(message_id TEXT PRIMARY KEY, ts INTEGER, session TEXT, agent_id TEXT,
  repo TEXT, model TEXT, sidechain INTEGER, input INTEGER, output INTEGER, cache_5m INTEGER, cache_1h INTEGER,
  cache_read INTEGER) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS tokens_ts ON tokens(ts);
CREATE TABLE IF NOT EXISTS activity(uid TEXT PRIMARY KEY, session TEXT, ts INTEGER, repo TEXT, kind TEXT,
  sidechain INTEGER) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS activity_ts ON activity(ts);
CREATE TABLE IF NOT EXISTS panel(hash TEXT PRIMARY KEY, ts INTEGER, repo TEXT, expert TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER, ms INTEGER, class TEXT, gate TEXT) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS tags(session TEXT PRIMARY KEY, client TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS repos(name TEXT PRIMARY KEY, path TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def connect(env):
    os.makedirs(env.ledger_dir, mode=0o700, exist_ok=True)
    os.chmod(env.ledger_dir, 0o700)
    conn = sqlite3.connect(env.db_path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    try:
        os.chmod(env.db_path, 0o600)
    except OSError:
        pass
    return conn


def parse_ts(s):
    """ISO-8601 (UTC 'Z' or offset) or epoch number -> integer epoch seconds, else None."""
    if isinstance(s, (int, float)):
        return int(s)
    if not isinstance(s, str) or not s:
        return None
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


# ---- transcript scanning (pure: no db, no git; safe to run in worker processes) -----------------
_PATTERNS = (b'"type":"assistant"', b'"type":"user"', b'"type": "assistant"', b'"type": "user"')
SYSTEM_TAGS = ("<local-command-caveat", "<local-command-stdout", "<local-command-stderr", "<bash-stdout",
               "<bash-stderr", "<task-notification", "<system-reminder")


def classify_user(o):
    """'tool' (tool_result), 'human' (a person typed it) or 'machine' (injected: sdk/meta/notification/compaction).

    Only envelope metadata and the first few characters of the content are inspected, in memory;
    nothing about the text is stored."""
    content = (o.get("message") or {}).get("content")
    items = content if isinstance(content, list) else []
    if any(isinstance(i, dict) and i.get("type") == "tool_result" for i in items):
        return "tool"
    if o.get("isSidechain") or o.get("isMeta") or o.get("isCompactSummary"):
        return "machine"
    origin = o.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return "machine"
    if o.get("promptSource") in ("sdk", "system") or o.get("turnOrigin") in ("sdk", "task_notification", "auto_continuation"):
        return "machine"
    if str(o.get("entrypoint") or "").startswith("sdk"):
        return "machine"
    head = content if isinstance(content, str) else next(
        (i.get("text") for i in items if isinstance(i, dict) and isinstance(i.get("text"), str)), "")
    if isinstance(head, str) and head.startswith(SYSTEM_TAGS):
        return "machine"
    if not isinstance(content, (str, list)):
        return "machine"
    return "human"


def usage_row(o, msg, ts, session, cwd, side):
    u = msg.get("usage") or {}
    mid, model = msg.get("id"), msg.get("model")
    if not mid or not model or model == "<synthetic>" or not isinstance(u, dict) or not u:
        return None
    total_w = int(u.get("cache_creation_input_tokens") or 0)
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    w1h = int(cc.get("ephemeral_1h_input_tokens") or 0)
    w5m = int(cc.get("ephemeral_5m_input_tokens") or 0)
    if w5m + w1h != total_w:        # no (or inconsistent) breakdown: bill the remainder as 5-minute writes
        w5m = max(total_w - w1h, 0)
    return (mid, ts, session, o.get("agentId"), cwd, model, side, int(u.get("input_tokens") or 0),
            int(u.get("output_tokens") or 0), w5m, w1h, int(u.get("cache_read_input_tokens") or 0))


def scan_file(path, rel, start=0):
    """Stream one transcript from byte `start`. Returns (token_rows, activity_rows, end_offset).
    Rows carry the raw cwd (None when the line has none); repo resolution happens in the caller."""
    m = PATH_RE.match(rel)
    path_session = (m.group("sid") or m.group("sid2")) if m else None
    forced_side = 1 if "/subagents/" in rel else 0
    tok, act, pos = [], [], start
    with open(path, "rb") as fh:
        fh.seek(start)
        for line in fh:
            complete = line.endswith(b"\n")
            o, line_start = None, pos
            if any(p in line for p in _PATTERNS):
                try:
                    o = json.loads(line)
                except ValueError:
                    if not complete:
                        break               # half-written tail: leave it for the next run
            elif not complete:
                try:
                    json.loads(line)
                except ValueError:
                    break
            pos += len(line)
            if not isinstance(o, dict) or o.get("type") not in ("assistant", "user"):
                continue
            ts = parse_ts(o.get("timestamp"))
            if ts is None:
                continue
            side = 1 if (o.get("isSidechain") or forced_side) else 0
            session = o.get("sessionId") or path_session
            cwd = o.get("cwd") or None
            uid = o.get("uuid") or "%s:%d" % (rel, line_start)
            msg = o.get("message") if isinstance(o.get("message"), dict) else {}
            if o["type"] == "assistant":
                kind = "assistant"
                row = usage_row(o, msg, ts, session, cwd, side)
                if row:
                    tok.append(row)
            else:
                kind = classify_user(o)
            act.append((uid, session, ts, cwd, kind, side))
    return tok, act, pos


def _scan_job(job):
    path, rel, start = job[0], job[1], job[2]
    try:
        return job, scan_file(path, rel, start), None
    except OSError as e:
        return job, ([], [], start), str(e)


def discover(env):
    """Yield (abs_path, rel_path, size, mtime_ns) for every transcript file."""
    for dp, _dirs, names in os.walk(env.projects_dir):
        for n in names:
            if not n.endswith(".jsonl"):
                continue
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, env.projects_dir).replace(os.sep, "/")
            if not PATH_RE.match(rel):      # skips workflow journal.jsonl and anything else that is not a transcript
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            yield p, rel, st.st_size, st.st_mtime_ns


# ---- ingest ------------------------------------------------------------------------------------
def ingest_panel(conn, env):
    """Free-panel log: one JSON object per line, deduped by a hash of the line. Returns rows added."""
    try:
        st = os.stat(env.panel_log)
    except OSError:
        return 0
    row = conn.execute("SELECT size, offset FROM files WHERE path=?", (env.panel_log,)).fetchone()
    start = 0 if (row is None or st.st_size < row[0]) else row[1]
    if row is not None and st.st_size == row[0]:
        return 0
    before, pos = conn.execute("SELECT count(*) FROM panel").fetchone()[0], start
    with open(env.panel_log, "rb") as fh:
        fh.seek(start)
        for line in fh:
            if not line.endswith(b"\n"):
                try:
                    json.loads(line)
                except ValueError:
                    break
            pos += len(line)
            text = line.strip()
            if not text:
                continue
            try:
                o = json.loads(text)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            conn.execute("INSERT OR IGNORE INTO panel VALUES(?,?,?,?,?,?,?,?,?,?)", (
                hashlib.sha256(text).hexdigest(), parse_ts(o.get("ts")), str(o.get("repo") or ""),
                o.get("expert"), o.get("model"), int(o.get("input_tokens") or 0),
                int(o.get("output_tokens") or 0), int(o.get("ms") or 0), o.get("class"), o.get("gate")))
    conn.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?)", (env.panel_log, max(st.st_size, pos), st.st_mtime_ns, pos))
    return conn.execute("SELECT count(*) FROM panel").fetchone()[0] - before


def ingest(env, full=False, extra_fold=(), workers=None):
    """Incremental, idempotent ingest. Returns a stats dict."""
    t0 = time.time()
    conn = connect(env)
    lock = os.fdopen(os.open(os.path.join(env.ledger_dir, ".lock"), os.O_WRONLY | os.O_CREAT, 0o600), "w")
    fcntl.flock(lock, fcntl.LOCK_EX)          # two overlapping ingests (hooks) must not interleave
    try:
        cfg = load_clients(env.clients_path)
        res = Resolver(env, list(cfg["fold"]) + list(extra_fold))
        known = {p: (s, o) for p, s, o in conn.execute("SELECT path, size, offset FROM files")}
        jobs, seen_files = [], 0
        for p, rel, size, mtime in discover(env):
            seen_files += 1
            prev = known.get(p)
            if full or prev is None or size < prev[0]:
                start = 0
            elif size == prev[0]:
                continue
            else:
                start = prev[1]
            jobs.append((p, rel, start, size, mtime))
        stats = {"files_total": seen_files, "files_read": len(jobs), "token_rows": 0, "activity_rows": 0,
                 "panel_rows": 0, "errors": []}
        nworkers = workers if workers is not None else min(os.cpu_count() or 1, 12)
        if len(jobs) > 16 and nworkers > 1:
            import multiprocessing
            pool = multiprocessing.get_context("fork").Pool(nworkers)
            results = pool.imap(_scan_job, jobs, chunksize=4)       # ordered: first-seen copy wins, deterministically
        else:
            pool, results = None, map(_scan_job, jobs)
        repo_paths, cwd_cache, since_commit = {}, {}, 0
        # --full re-reads every file; existing rows are kept (transcripts may since have been deleted) but
        # their repo is refreshed, so changed fold rules take effect without losing history.
        ins_t, ins_a = "INSERT INTO tokens VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", "INSERT INTO activity VALUES(?,?,?,?,?,?)"
        tail = " ON CONFLICT(%s) DO UPDATE SET repo=excluded.repo" if full else " ON CONFLICT(%s) DO NOTHING"
        sql_tok, sql_act = ins_t + tail % "message_id", ins_a + tail % "uid"
        n_tok0 = conn.execute("SELECT count(*) FROM tokens").fetchone()[0]
        n_act0 = conn.execute("SELECT count(*) FROM activity").fetchone()[0]
        try:
            for (p, rel, start, size, mtime), (tok, act, pos), err in results:
                if err:
                    stats["errors"].append("%s: %s" % (rel, err))
                    continue
                slug = rel.split("/", 1)[0]

                def repo_of(cwd):
                    key = cwd or ("slug:" + slug)
                    hit = cwd_cache.get(key)
                    if hit is None:
                        hit = cwd_cache[key] = res.for_cwd(cwd) if cwd else res.for_slug(slug)
                        if hit[1] or hit[0] not in repo_paths:
                            repo_paths[hit[0]] = hit[1] or repo_paths.get(hit[0])
                    return hit[0]

                conn.executemany(sql_tok, [r[:4] + (repo_of(r[4]),) + r[5:] for r in tok])
                conn.executemany(sql_act, [(r[0], r[1], r[2], repo_of(r[3]), r[4], r[5]) for r in act])
                conn.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?)", (p, max(size, pos), mtime, pos))
                since_commit += 1
                if since_commit >= 200:
                    conn.commit()
                    since_commit = 0
        finally:
            if pool:
                pool.close()
                pool.join()
        for name, path in repo_paths.items():
            if path:
                conn.execute("INSERT OR REPLACE INTO repos VALUES(?,?)", (name, path))
            else:
                conn.execute("INSERT OR IGNORE INTO repos VALUES(?,NULL)", (name,))
        stats["token_rows"] = conn.execute("SELECT count(*) FROM tokens").fetchone()[0] - n_tok0
        stats["activity_rows"] = conn.execute("SELECT count(*) FROM activity").fetchone()[0] - n_act0
        stats["panel_rows"] = ingest_panel(conn, env)
        conn.execute("INSERT OR REPLACE INTO meta VALUES('last_ingest', ?)", (str(int(time.time())),))
        conn.commit()
        stats["seconds"] = time.time() - t0
        stats["db_bytes"] = os.path.getsize(env.db_path)
        return stats
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
        conn.close()


# ---- time math ---------------------------------------------------------------------------------
def merge_intervals(iv):
    """Union of (start, end) pairs -> sorted disjoint list. Touching intervals join."""
    out = []
    for s, e in sorted(iv):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1][1] = e
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def stream_intervals(ts, gap, floor):
    """One session's sorted timestamps -> intervals. Neighbours <= gap apart are bridged; each point
    gets at least `floor` seconds (so a lone prompt is worth `floor`; a cluster is last-first+floor)."""
    iv = []
    for i, t in enumerate(ts):
        iv.append((t, t + floor))
        if i + 1 < len(ts) and ts[i + 1] - t <= gap:
            iv.append((t, ts[i + 1]))
    return merge_intervals(iv)


def total_seconds(iv):
    return sum(e - s for s, e in iv)


def clip(iv, lo, hi):
    return [(max(s, lo), min(e, hi)) for s, e in iv if min(e, hi) > max(s, lo)]


def local_day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def next_midnight(ts):
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday + 1, 0, 0, 0, 0, 0, -1)))


def split_by_day(iv):
    """Disjoint intervals -> {local 'YYYY-MM-DD': seconds}, cutting at local midnights."""
    out = defaultdict(int)
    for s, e in iv:
        while s < e:
            cut = min(e, next_midnight(s))
            out[local_day(s)] += cut - s
            s = cut
    return out


def day_bounds(since, until=None):
    """'YYYY-MM-DD' (local) -> [lo, hi) epoch seconds; `until` is inclusive of that whole day."""
    def midnight(d, plus=0):
        y, m, dd = (int(x) for x in d.split("-"))
        return int(time.mktime((y, m, dd + plus, 0, 0, 0, 0, 0, -1)))
    lo = midnight(since)
    hi = midnight(until, 1) if until else int(time.time()) + 1
    return lo, hi


def hours_by_group(rows, group_of, kinds, gap, floor, with_sub):
    """rows = (session, repo, kind, sidechain, ts). Per group: merge each session's stream, then union the
    sessions, so parallel sessions never double count. -> {group: disjoint interval list}."""
    streams = defaultdict(list)
    for session, repo, kind, side, ts in rows:
        if kind in kinds and (with_sub or not side):
            streams[(group_of(session, repo), session)].append(ts)
    per_group = defaultdict(list)
    for (g, _s), ts in streams.items():
        ts.sort()
        per_group[g].extend(stream_intervals(ts, gap, floor))
    return {g: merge_intervals(iv) for g, iv in per_group.items()}


# ---- git commits -------------------------------------------------------------------------------
def commits_by_repo(conn, lo, hi, emails=None, all_branches=False, runner=subprocess.run):
    """{repo: [(sha, 'YYYY-MM-DD')]} for non-merge commits authored by `emails` in [lo, hi), one `git log` per
    parent repo (HEAD of its main checkout; all local+remote branches with all_branches), mailmap applied.
    A commit reachable from two clones is credited once (first repo by name)."""
    emails = emails or default_author_emails()
    fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
    seen, out = set(), {}
    for name, path in conn.execute("SELECT name, path FROM repos WHERE path IS NOT NULL ORDER BY name"):
        if not os.path.isdir(path):
            continue
        cmd = ["git", "-C", path, "log"] + (["--branches", "--remotes"] if all_branches else []) + [
               "--no-merges", "--use-mailmap", "-F", "-i",
               "--since=" + fmt(lo), "--until=" + fmt(hi - 1), "--format=%H%x09%ad", "--date=format-local:%Y-%m-%d"]
        cmd += ["--author=" + e for e in emails]
        try:
            r = runner(cmd, capture_output=True, text=True, timeout=120, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            sha, _, day = line.partition("\t")
            if sha and sha not in seen:
                seen.add(sha)
                out.setdefault(name, []).append((sha, day))
    return out


# ---- report ------------------------------------------------------------------------------------
def price_of(model):
    return PRICE_OVERRIDES.get(model) or PRICES[model_family(model)]


def usd_of(model, i, o, w5, w1, r):
    p = price_of(model)
    return (i * p["in"] + o * p["out"] + w5 * p["in"] * CACHE_WRITE_MULT["5m"]
            + w1 * p["in"] * CACHE_WRITE_MULT["1h"] + r * p["read"]) / 1e6


def opus_equiv(model, i, o, w5, w1, r):
    w = WEIGHTS[model_family(model)]
    return w * (i + o + w5 + w1) + CACHE_READ_WEIGHT * w * r


def load_tags(conn):
    return dict(conn.execute("SELECT session, client FROM tags"))


def build_report(env, since, until=None, by="client", with_sub=False, literal=False, emails=None, all_branches=False):
    """-> dict(rows=[...], all_your_h, all_agent_h, window=(lo, hi), notes={client: note})."""
    conn = connect(env)
    try:
        cfg, tags = load_clients(env.clients_path), load_tags(conn)
        res = Resolver(env, cfg["fold"], use_git=False)
        lo, hi = day_bounds(since, until)
        ccache = {}

        def client_of(session, repo):
            k = (session, repo)
            if k not in ccache:
                ccache[k] = client_for(cfg, repo, session, tags)
            return ccache[k]

        group_of = {"client": client_of, "repo": lambda s, r: r}.get(by, lambda s, r: "*")
        act = conn.execute("SELECT session, repo, kind, sidechain, ts FROM activity WHERE ts>=? AND ts<?",
                           (lo, hi)).fetchall()
        hk = {"human", "machine"} if literal else {"human"}
        ak = {"human", "machine", "assistant", "tool"}
        your = hours_by_group(act, group_of, hk, HUMAN_GAP, HUMAN_FLOOR, False)
        agent = hours_by_group(act, group_of, ak, AGENT_GAP, 0, with_sub)
        allk = lambda s, r: "*"
        all_your = merge_intervals(sum(hours_by_group(act, allk, hk, HUMAN_GAP, HUMAN_FLOOR, False).values(), []))
        all_agent = merge_intervals(sum(hours_by_group(act, allk, ak, AGENT_GAP, 0, with_sub).values(), []))
        rows = defaultdict(lambda: {"your_s": 0, "agent_s": 0, "commits": 0, "tokens": defaultdict(lambda: [0] * 5),
                                    "panel_in": 0, "panel_out": 0, "panel_n": 0})
        if by == "day":
            for g, sec in split_by_day(clip(all_your, lo, hi)).items():
                rows[g]["your_s"] = sec
            for g, sec in split_by_day(clip(all_agent, lo, hi)).items():
                rows[g]["agent_s"] = sec
        elif by != "model":
            for g, iv in your.items():
                rows[g]["your_s"] = total_seconds(clip(iv, lo, hi))
            for g, iv in agent.items():
                rows[g]["agent_s"] = total_seconds(clip(iv, lo, hi))
        for session, repo, model, day, i, o, w5, w1, r in conn.execute(
                "SELECT session, repo, model, strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime'), sum(input), sum(output),"
                " sum(cache_5m), sum(cache_1h), sum(cache_read) FROM tokens WHERE ts>=? AND ts<?"
                " GROUP BY session, repo, model, 4", (lo, hi)):
            g = {"client": client_of(session, repo), "repo": repo, "day": day, "model": model}[by]
            t = rows[g]["tokens"][model]
            for n, v in enumerate((i, o, w5, w1, r)):
                t[n] += v
        for repo, day, model, i, o, n in conn.execute(
                "SELECT repo, strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime'), model, sum(input_tokens),"
                " sum(output_tokens), count(*) FROM panel WHERE ts>=? AND ts<? GROUP BY 1, 2, 3", (lo, hi)):
            name = res.fold_name(os.path.basename((repo or "").rstrip("/")) or "unknown")
            g = {"client": client_for(cfg, name), "repo": name, "day": day, "model": model or "unknown"}[by]
            rows[g]["panel_in"] += i or 0
            rows[g]["panel_out"] += o or 0
            rows[g]["panel_n"] += n
        emails = emails or cfg["author_emails"] or default_author_emails()
        for repo, items in commits_by_repo(conn, lo, hi, emails, all_branches).items():
            for _sha, day in items:
                g = {"client": client_for(cfg, repo), "repo": repo, "day": day, "model": None}[by]
                if g is not None:
                    rows[g]["commits"] += 1
        out = []
        for g, r in rows.items():
            toks = {m: tuple(v) for m, v in r["tokens"].items()}
            r.update(group=g, your_hours=r.pop("your_s") / 3600, agent_hours=r.pop("agent_s") / 3600, tokens=toks,
                     opus_equiv_tokens=sum(opus_equiv(m, v[0], v[1], v[2], v[3], v[4]) for m, v in toks.items()),
                     api_equiv_usd=sum(usd_of(m, *v) for m, v in toks.items()))
            out.append(r)
        out.sort(key=(lambda r: r["group"]) if by == "day" else (lambda r: -r["api_equiv_usd"]))
        return {"rows": out, "by": by, "window": (lo, hi), "notes": cfg["notes"],
                "all_your_h": total_seconds(clip(all_your, lo, hi)) / 3600,
                "all_agent_h": total_seconds(clip(all_agent, lo, hi)) / 3600}
    finally:
        conn.close()


# ---- output ------------------------------------------------------------------------------------
def fmt_tok(n):
    n = float(n)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return "%.2f%s" % (n / div, suf)
    return "%d" % n


def render_table(headers, rows, right_from=1):
    cells = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    line = lambda r: "  ".join(c.ljust(w) if i < right_from else c.rjust(w) for i, (c, w) in enumerate(zip(r, widths)))
    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(r) for r in cells])


def fam_sums(tokens):
    """{model: (in, out, w5, w1, r)} -> {family: [input, output, cache_write, cache_read]}."""
    out = {f: [0, 0, 0, 0] for f in FAMILIES}
    for m, (i, o, w5, w1, r) in tokens.items():
        f = out[model_family(m)]
        f[0] += i
        f[1] += o
        f[2] += w5 + w1
        f[3] += r
    return out


def render_report_text(rep, since, until):
    by, rows = rep["by"], rep["rows"]
    head = "Usage ledger  %s .. %s  by %s" % (since, until or "now", by)
    lines = [head, "api_equiv_usd is the API-EQUIVALENT list price (the subscription is flat; this is not money spent).",
             "opus_equiv = (input+output+cache_write) weighted opus 4 / sonnet 2 / haiku 1 (fable 8), cache reads at 0.1 of that.", ""]
    hrs = (lambda v: "-") if by == "model" else (lambda v: "%.1f" % v)
    body = [(r["group"], hrs(r["your_hours"]), hrs(r["agent_hours"]), r["commits"] or "-", fmt_tok(r["opus_equiv_tokens"]),
             "$%.0f" % r["api_equiv_usd"], fmt_tok(r["panel_in"]) if r["panel_n"] else "-",
             fmt_tok(r["panel_out"]) if r["panel_n"] else "-") for r in rows]
    s = lambda k: sum(r[k] for r in rows)
    body.append(("TOTAL (sum of rows)", hrs(s("your_hours")), hrs(s("agent_hours")), s("commits") or "-",
                 fmt_tok(s("opus_equiv_tokens")), "$%.0f" % s("api_equiv_usd"), fmt_tok(s("panel_in")), fmt_tok(s("panel_out"))))
    if by != "model":
        body.append(("ALL, merged union", "%.1f" % rep["all_your_h"], "%.1f" % rep["all_agent_h"], "", "", "", "", ""))
    lines.append(render_table(["group", "your_h", "agent_h", "commits", "opus_equiv", "api_equiv_usd", "panel_in", "panel_out"], body))
    if by != "model":
        lines.append("overlap (sum of rows - union): your %.1f h, agent %.1f h"
                     % (s("your_hours") - rep["all_your_h"], s("agent_hours") - rep["all_agent_h"]))
    lines += ["", "Tokens by model", render_table(
        ["group", "model", "input", "output", "cache_write", "cache_read"],
        [(r["group"], m, fmt_tok(v[0]), fmt_tok(v[1]), fmt_tok(v[2] + v[3]), fmt_tok(v[4]))
         for r in rows for m, v in sorted(r["tokens"].items())], right_from=2)]
    notes = [(g, rep["notes"][g]) for g in (r["group"] for r in rows) if g in rep["notes"]]
    if notes:
        lines += [""] + ["note, %s: %s" % n for n in notes]
    return "\n".join(lines)


_FORMULA = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")


def csv_cell(v):
    """Spreadsheets run a cell that starts with = + - @ as a formula: prefix a single quote. Plain numbers
    (a leading minus is legitimate there) pass through untouched."""
    if isinstance(v, str) and v.startswith(_FORMULA) and not _NUMBER.fullmatch(v):
        return "'" + v
    return v


class _SafeWriter:
    def __init__(self, fh):
        self._w = csv.writer(fh)

    def writerow(self, row):
        self._w.writerow([csv_cell(c) for c in row])


def write_report_csv(rep, fh):
    w = _SafeWriter(fh)
    cols = ["group", "your_hours", "agent_hours", "commits", "opus_equiv_tokens", "api_equiv_usd",
            "panel_input_tokens", "panel_output_tokens"]
    cols += ["%s_%s" % (f, k) for f in FAMILIES for k in ("input", "output", "cache_write", "cache_read")] + ["note"]
    w.writerow(cols)
    for r in rep["rows"]:
        fs = fam_sums(r["tokens"])
        w.writerow([r["group"], "%.3f" % r["your_hours"], "%.3f" % r["agent_hours"], r["commits"],
                    "%.0f" % r["opus_equiv_tokens"], "%.2f" % r["api_equiv_usd"], r["panel_in"], r["panel_out"]]
                   + [v for f in FAMILIES for v in fs[f]] + [rep["notes"].get(r["group"], "")])
    if rep["by"] != "model":
        w.writerow(["ALL (merged union)", "%.3f" % rep["all_your_h"], "%.3f" % rep["all_agent_h"]]
                   + [""] * (len(cols) - 3))


# ---- tag / clients -----------------------------------------------------------------------------
def cmd_tag(env, session, client):
    conn = connect(env)
    try:
        hits = [r[0] for r in conn.execute("SELECT DISTINCT session FROM activity WHERE session LIKE ? LIMIT 3", (session + "%",))]
        if len(hits) > 1 and session not in hits:
            return "ambiguous session prefix %s (%s ...)" % (session, ", ".join(hits)), 2
        full = session if session in hits or not hits else hits[0]
        conn.execute("INSERT OR REPLACE INTO tags VALUES(?,?,?)", (full, client, int(time.time())))
        conn.commit()
        warn = "" if hits else "  (warning: session not seen in the data yet; tag stored anyway)"
        return "tagged %s -> %s%s" % (full, client, warn), 0
    finally:
        conn.close()


def cmd_untag(env, session, all_tags=False):
    """Remove a tag by exact id or by an 8+ character prefix. LIKE wildcards in SESSION are literal, except
    that a pattern made only of % and _ means "every tag" and is refused unless --all is given."""
    wild_only = not session.strip("%_")
    if wild_only and not all_tags:
        return "refusing to untag every tag with %r; pass --all if that is really what you want" % session, 2
    conn = connect(env)
    try:
        if wild_only:
            n = conn.execute("DELETE FROM tags").rowcount
        else:
            esc = session.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            n = conn.execute("DELETE FROM tags WHERE session=? OR (length(?)>=8 AND session LIKE ? ESCAPE '\\')",
                             (session, session, esc + "%")).rowcount
        conn.commit()
        return ("untagged %s" % session, 0) if n else ("no tag matched %s" % session, 1)
    finally:
        conn.close()


def cmd_clients(env):
    conn = connect(env)
    try:
        cfg = load_clients(env.clients_path)
        rules = [(c, g) for c, globs in cfg["rules"] for g in globs]
        rows = []
        for repo, n, i, o, w5, w1, r in conn.execute(
                "SELECT repo, count(*), sum(input), sum(output), sum(cache_5m), sum(cache_1h), sum(cache_read)"
                " FROM tokens GROUP BY repo"):
            rule = next(((c, g) for c, g in rules if fnmatch.fnmatchcase(repo, g)), None)
            rows.append((rule[0] if rule else "own:" + repo, repo, rule[1] if rule else "(no rule)", n,
                         i + o + w5 + w1, r, sum(opus_equiv(m, *v) for m, v in _models_of(conn, repo)),
                         sum(usd_of(m, *v) for m, v in _models_of(conn, repo))))
        rows.sort(key=lambda t: (t[0].startswith("own:"), t[0], -t[6]))
        out = [render_table(["client", "repo", "matched", "messages", "in+out+cache_w", "cache_read", "opus_equiv", "api_usd"],
                            [(c, r, g, n, fmt_tok(a), fmt_tok(b), fmt_tok(e), "$%.0f" % u) for c, r, g, n, a, b, e, u in rows], right_from=3)]
        tags = conn.execute("SELECT t.session, t.client, (SELECT count(*) FROM tokens k WHERE k.session=t.session)"
                            " FROM tags t ORDER BY t.client").fetchall()
        if tags:
            out += ["", "Session tags (override the repo map)", render_table(["session", "client", "messages"], tags, right_from=2)]
        return "\n".join(out)
    finally:
        conn.close()


def _models_of(conn, repo):
    for m, i, o, w5, w1, r in conn.execute(
            "SELECT model, sum(input), sum(output), sum(cache_5m), sum(cache_1h), sum(cache_read) FROM tokens WHERE repo=? GROUP BY model",
            (repo,)):
        yield m, (i, o, w5, w1, r)


# ---- cli ---------------------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(prog="usage_ledger.py", description="Per-client token and time ledger for Claude Code.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="read new transcript bytes into the ledger (incremental, idempotent)")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--full", action="store_true", help="re-read every file from byte 0 (keeps existing rows, refreshes repo)")
    p.add_argument("--fold", action="append", default=[], metavar="REGEX",
                   help="extra repo-name suffix to strip when git cannot answer (repeatable)")
    p = sub.add_parser("report", help="hours, tokens, API-equivalent cost and commits per group")
    p.add_argument("--since", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--until", metavar="YYYY-MM-DD", help="inclusive; default now")
    p.add_argument("--by", choices=["client", "repo", "day", "model"], default="client")
    p.add_argument("--csv", action="store_true")
    p.add_argument("--with-subagents", action="store_true", help="count subagent/workflow lines in agent_hours too")
    p.add_argument("--all-branches", action="store_true",
                   help="commits: every local+remote branch (default: HEAD of each parent repo, so squash-merged branches count once)")
    p.add_argument("--literal-prompts", action="store_true",
                   help="count every non-sidechain, non-tool-result user line as a human prompt (default drops machine-injected ones)")
    p = sub.add_parser("tag", help="override the client for one session")
    p.add_argument("session")
    p.add_argument("--client", required=True)
    p = sub.add_parser("untag", help="remove a session override")
    p.add_argument("session")
    p.add_argument("--all", action="store_true", dest="all_tags",
                   help="allow a wildcard-only SESSION (such as %%%%) to remove every tag")
    sub.add_parser("clients", help="repo -> client mapping actually seen, with token totals")
    return ap


def main(argv=None, env=None, out=None):
    out = out or sys.stdout
    env = env or Env()
    args = build_parser().parse_args(argv)
    if args.cmd == "ingest":
        s = ingest(env, full=args.full, extra_fold=args.fold)
        if not args.quiet:
            print("ingest: %d/%d files read, +%d token rows, +%d activity rows, +%d panel rows, %s, %.1fs%s" % (
                s["files_read"], s["files_total"], s["token_rows"], s["activity_rows"], s["panel_rows"],
                "db %.1f MB" % (s["db_bytes"] / 1e6), s["seconds"],
                ", %d read errors" % len(s["errors"]) if s["errors"] else ""), file=out)
        for e in s["errors"]:
            print("  error: " + e, file=sys.stderr)
        return 0
    if args.cmd == "report":
        for d in (args.since, args.until):
            if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                print("dates must be YYYY-MM-DD, got %r" % d, file=sys.stderr)
                return 2
        rep = build_report(env, args.since, args.until, args.by, args.with_subagents, args.literal_prompts,
                           all_branches=args.all_branches)
        if args.csv:
            write_report_csv(rep, out)
        else:
            print(render_report_text(rep, args.since, args.until), file=out)
        return 0
    if args.cmd == "tag":
        msg, rc = cmd_tag(env, args.session, args.client)
    elif args.cmd == "untag":
        msg, rc = cmd_untag(env, args.session, args.all_tags)
    else:
        msg, rc = cmd_clients(env), 0
    print(msg, file=out if rc == 0 else sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
