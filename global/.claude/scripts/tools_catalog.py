#!/usr/bin/env python3
"""tools_catalog.py: build a one-line-per-tool catalog of our own command-line tools, and pick the ones a prompt needs.

Use when: a lane or a person needs to know which existing script or hook already does a job, before writing a new helper (build writes ~/.claude/scripts/TOOLS.md, match prints the relevant lines for a prompt file).

Usage: tools_catalog.py build [--check] | match PROMPT_FILE [--max N]

Standard library only. The catalog holds header text only (first sentence, "Use when:" line, usage line), never file contents beyond the header.

Include rule: files directly inside ~/.claude/scripts, ~/.claude/hooks and ~/repos/hub/scripts whose extension is .py .sh .js .mjs .cjs or .ps1.
Repo rule: each repo named in ~/repos/hub/repos.tsv that exists on disk and whose CLAUDE.md, README.md, .mcp.json or .claude/commands name an MCP server, tool or slash command gets one line (names only, never config values; playwright and the SpecKit commands are skipped as boilerplate).
Exclude rule: subfolders (tests/, __pycache__/, examples/ ...), any name containing ".bak", dotfiles, and every other extension (.md .json .tsv .txt .log .prompt .ref ...).
"""
import argparse
import ast
import math
import os
import re
import sys
import tempfile
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
SCRIPTS_DIR = HOME / ".claude" / "scripts"
HOOKS_DIR = HOME / ".claude" / "hooks"
HUB_SCRIPTS_DIR = HOME / "repos" / "hub" / "scripts"
REPOS_DIR = HOME / "repos"
REPOS_LIST = HOME / "repos" / "hub" / "repos.tsv"
CATALOG = SCRIPTS_DIR / "TOOLS.md"
CATALOG_LABEL = "~/.claude/scripts/TOOLS.md"

EXTENSIONS = {".py", ".sh", ".js", ".mjs", ".cjs", ".ps1"}
SAFETY = ["redact_view.py", "scrub_known_secrets.py", "coop_scrubber.py", "secret-guard.py",
          "ps_redacted.sh", "redact_process.py", "secret_scrubber.py", "output-scrubber.py"]
MAX_WHAT = 200
DEFAULT_MAX = 12
NAME_WEIGHT = 3.0
DESC_WEIGHT = 1.0
MIN_SCORE = 25.0
MIN_WORDS = 3      # at least this many different prompt words must hit the tool
MIN_DESC_HITS = 4  # with no file-name word among them, the description alone needs this many
COMMON_SHARE = 0.25  # a word in more than this share of tools says nothing

STOP = set("""
a about above after again all also an and any are as at be because been before being below between both but by can
could did do does doing down during each few for from further had has have having he her here hers him his how i if
in into is it its itself just me more most my no nor not now of off on once only or other our out over own same she
should so some such than that the their them then there these they this those through to too under until up us very
was we were what when where which while who whom why will with would you your yours
file files script scripts tool tools use used uses using run runs running need needs get gets make makes new one two
see say says please must may might shall via per etc eg ie lane lanes prompt task job work works step steps
claude code hub repo repos every print put open read write show state name path folder never still check line text live put today session jonathan hatch
""".split())

TOKEN = re.compile(r"[a-z0-9]+")
BANNER = re.compile(r"^[\W_]*$")


# ---------- header extraction ----------

def _clean_lines(lines):
    out = []
    for ln in lines:
        s = ln.rstrip()
        out.append("" if BANNER.match(s) else s.strip())
    while out and not out[0]:
        out.pop(0)
    return out


def _hash_block(lines):
    """Contiguous '#' comment lines after the shebang and any encoding or tool-directive lines."""
    i = 0
    if lines and lines[0].startswith("#!"):
        i = 1
    while i < len(lines):
        s = lines[i].strip()
        if not s or re.match(r"#.*(-\*-|coding[:=]|shellcheck|vim?:|noqa|pylint|eslint)", s):
            i += 1
            continue
        break
    block = []
    while i < len(lines) and lines[i].strip().startswith("#"):
        s = lines[i].strip()
        if re.match(r"#.*(-\*-|coding[:=]|shellcheck|vim?:)", s) and not block:
            i += 1
            continue
        block.append(re.sub(r"^#+\s?", "", s))
        i += 1
    return _clean_lines(block)


def _js_block(lines):
    i = 0
    while i < len(lines) and (lines[i].startswith("#!") or not lines[i].strip()
                              or re.match(r"""^\s*['"]use strict['"];?\s*$""", lines[i])):
        i += 1
    if i >= len(lines):
        return []
    s = lines[i].strip()
    block = []
    if s.startswith("//"):
        while i < len(lines) and lines[i].strip().startswith("//"):
            block.append(re.sub(r"^//+\s?", "", lines[i].strip()))
            i += 1
    elif s.startswith("/*"):
        while i < len(lines):
            t = lines[i].strip()
            done = "*/" in t
            t = re.sub(r"^/\*+\s?", "", t)
            t = re.sub(r"\s?\*+/.*$", "", t)
            t = re.sub(r"^\*\s?", "", t)
            block.append(re.sub(r"^@(file|fileoverview|description|module|overview)\b\s*", "", t))
            i += 1
            if done:
                break
    return _clean_lines(block)


def _ps_block(lines):
    for i, ln in enumerate(lines):
        if ln.strip().startswith("<#"):
            block = []
            for t in lines[i:]:
                done = "#>" in t
                t = re.sub(r"^\s*<#\s?", "", t)
                t = re.sub(r"\s?#>.*$", "", t)
                block.append(t.strip())
                if done:
                    break
            return _clean_lines(block)
        if ln.strip() and not ln.strip().startswith("#"):
            break
    return _hash_block(lines)


def header_lines(path):
    """The header of one tool as a list of text lines (blank line = paragraph break); [] when none."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ext = Path(path).suffix
    lines = text.splitlines()
    if ext == ".py":
        try:
            doc = ast.get_docstring(ast.parse(text), clean=True)
        except (SyntaxError, ValueError):
            doc = None
        if doc and doc.strip():
            return _clean_lines(doc.splitlines())
        return _hash_block(lines)
    if ext in (".js", ".mjs", ".cjs"):
        return _js_block(lines)
    if ext == ".ps1":
        return _ps_block(lines)
    return _hash_block(lines)


def _squash(s):
    return re.sub(r"\s+", " ", s).replace("|", "/").strip()


def first_sentence(lines):
    para = []
    for ln in lines:
        if not ln:
            if para:
                break
            continue
        para.append(ln)
    text = _squash(" ".join(para))
    if not text:
        return ""
    m = re.search(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(`])", text)
    if m:
        text = text[:m.start()]
    return text[:MAX_WHAT].rstrip()


def find_labeled(lines, label):
    pat = re.compile(r"^\s*" + label + r"\s*:?\s*(.*)$", re.I)
    for i, ln in enumerate(lines):
        m = pat.match(ln)
        if not m:
            continue
        parts = [m.group(1)] if m.group(1) else []
        j = i + 1
        if not parts:
            while j < len(lines) and not lines[j]:
                j += 1
            if j < len(lines):
                parts.append(lines[j])
        elif label == "use when":
            while j < len(lines) and lines[j]:
                parts.append(lines[j])
                j += 1
        text = _squash(" ".join(parts))
        if text:
            return text[:MAX_WHAT].rstrip()
    return ""


def describe(path):
    """(what, when) for a tool; what == '' means no usable header."""
    lines = header_lines(path)
    what = first_sentence(lines)
    if len(re.findall(r"[A-Za-z]", what)) < 8:
        return "", ""
    when = find_labeled(lines, "use when")
    if when:
        when = "Use when: " + when
    else:
        usage = find_labeled(lines, "usage")
        if usage and not what.lower().startswith("usage"):
            when = "Usage: " + usage
    return what, when


# ---------- catalog ----------

def is_tool(p):
    n = p.name
    return (p.is_file() and not n.startswith(".") and ".bak" not in n and p.suffix in EXTENSIONS)


def collect(scripts_dir, hooks_dir, hub_dir=None):
    tools = []
    for d in (scripts_dir, hooks_dir, hub_dir):
        if d is None:
            continue
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if is_tool(p):
                what, when = describe(p)
                tools.append({"path": str(p), "name": p.name, "what": what, "when": when})
    tools.sort(key=lambda t: t["path"])
    return tools


def tool_line(t):
    parts = [t["path"], t["what"] or "(no header)"]
    if t["when"]:
        parts.append(t["when"])
    return "- " + " | ".join(parts)


# ---------- repo tools and MCP servers ----------

MCP_TOOL = re.compile(r"mcp__([A-Za-z0-9]+(?:_[A-Za-z0-9]+)*?)__([A-Za-z0-9_]+)")
MCP_IMAGE = re.compile(r"\bmcp/([a-z0-9][a-z0-9._-]*)")
BOLD_LABEL = re.compile(r"^\s*(?:[-*]\s+)?\*\*([^*:\n]{2,40}?):?\*\*")
GENERIC_SERVERS = {"playwright"}      # in nearly every repo's .mcp.json: says nothing about the repo
GENERIC_COMMAND_PREFIXES = ("speckit.",)  # the SpecKit commands are copied into many repos
MAX_REPO_ITEMS = 4
SHARED_COMMAND_REPOS = 3
RULE_OPENING = re.compile(r"^(Never|Always|No |Do not|Don't|Auto-generated|Instructions for|This file)", re.I)


SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_TEXT = 200


def _safe_name(name):
    """A server, command or repo name from a repo's files: only plain name characters, else ''.
    Repo text ends up in lane prompts, so anything that is not a plain name is dropped."""
    name = str(name)
    return name if SAFE_NAME.match(name) else ""


def _safe_text(s, limit=MAX_TEXT):
    """Repo-derived prose: one line, no pipe, no control characters, capped."""
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(s))
    return _squash(s)[:limit].strip()


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def purpose_sentence(text):
    """First sentence of the first plain paragraph (at least 40 letters) after the title; '' when none."""
    in_fence = False
    para = []
    paras = []
    for raw in text.splitlines():
        ln = raw.strip()
        if ln.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not ln or re.match(r"^(#|>|[-*+]\s|\d+[.)]\s|\||<|!\[|\[!\[|---|===|This file provides guidance)", ln):
            if para:
                paras.append(" ".join(para))
                para = []
            continue
        para.append(ln)
    if para:
        paras.append(" ".join(para))
    for p in paras:
        p = re.sub(r"https?://\S+", "", p)
        p = re.sub(r"[*`]|\*\*CRITICAL\*\*:?", "", p)
        p = re.sub(r"\s+", " ", p).strip(" :-")
        if len(re.findall(r"[A-Za-z]", p)) < 40 or RULE_OPENING.match(p):
            continue
        sent = first_sentence([p])
        if len(re.findall(r"[A-Za-z]", sent)) >= 30:
            return sent
    return ""


def _unique(seq):
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def repo_mcp_items(repo, docs):
    """Names of MCP servers and tools a repo's own docs and .mcp.json name (names only, never values)."""
    items = []
    servers = {}
    for text in docs:
        for m in MCP_TOOL.finditer(text):
            servers.setdefault(m.group(1).replace("_", "-"), [])
            full = m.group(0)
            if full not in servers[m.group(1).replace("_", "-")]:
                servers[m.group(1).replace("_", "-")].append(full)
        for m in MCP_IMAGE.finditer(text):
            servers.setdefault(m.group(1).rstrip(".,)"), [])
    mcp_json = repo / ".mcp.json"
    if mcp_json.is_file():
        try:
            import json
            data = json.loads(_read_text(mcp_json))
            block = data.get("mcpServers", data) if isinstance(data, dict) else {}
            for name in (block.keys() if isinstance(block, dict) else []):
                if str(name).lower() not in GENERIC_SERVERS:
                    servers.setdefault(str(name), [])
        except ValueError:
            pass
    for name, tools in servers.items():
        if name.lower() in GENERIC_SERVERS or not _safe_name(name):
            continue
        tools = [t for t in tools if _safe_name(t)]
        items.append(f"MCP server {name}" + (f" ({', '.join(tools[:2])})" if tools else ""))
    # bold labels inside a section whose heading mentions MCP, for example "**YouTube Transcript:** ..."
    for text in docs:
        in_mcp = False
        for ln in text.splitlines():
            if ln.startswith("#"):
                in_mcp = "mcp" in ln.lower()
                continue
            m = BOLD_LABEL.match(ln) if in_mcp else None
            if m:
                label = re.sub(r"\s*MCP$", "", m.group(1).strip(), flags=re.I)
                if re.match(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,39}$", label):
                    items.append(f"MCP: {label}")
    if not items:
        for text in docs:
            heads = [ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#") and "mcp" in ln.lower()]
            if heads:
                head = re.sub(r"[^A-Za-z0-9 ._/-]", "", heads[0])[:50].strip()
                if head:
                    items.append("MCP section: " + head)
                break
    if not items and "mcp" in repo.name.lower():
        items.append("MCP server (the repo itself)")
    return _unique(items)


def repo_commands(repo):
    cmd_dir = repo / ".claude" / "commands"
    try:
        names = sorted(p.stem for p in cmd_dir.iterdir() if p.suffix == ".md")
    except OSError:
        return []
    return [n for n in names if _safe_name(n) and not n.startswith(GENERIC_COMMAND_PREFIXES)]


def repo_entry(repo, shared=frozenset()):
    """One catalog entry for a repo, or None when its docs name nothing worth listing."""
    docs = [_read_text(repo / n) for n in ("CLAUDE.md", "README.md")]
    items = repo_mcp_items(repo, docs)
    cmds = [c for c in repo_commands(repo) if c not in shared]
    if not items and not cmds:
        return None
    what = _safe_text(purpose_sentence(docs[1]) or purpose_sentence(docs[0]))
    if len(re.findall(r"[A-Za-z]", what)) < 8:
        what = f"Repository {repo.name}"
    bits = items[:MAX_REPO_ITEMS]
    if cmds:
        bits.append("slash commands " + ", ".join("/" + c for c in cmds[:6]) + (" ..." if len(cmds) > 6 else ""))
    doc = "CLAUDE.md" if docs[0] else "README.md"
    when = "Tools: " + "; ".join(bits) + f". Read {repo}/{doc} first"
    return {"path": str(repo), "name": repo.name, "what": what, "when": when, "repo": True}


def collect_repos(repos_dir, repos_list):
    """Repo tools: every repo named in hub/repos.tsv that exists on disk and whose docs name a tool."""
    out = []
    text = _read_text(repos_list)
    repos = []
    for i, ln in enumerate(text.splitlines()):
        if i == 0 or not ln.strip():
            continue
        name = ln.split("\t")[0].strip()
        if not name or name.startswith(".") or not _safe_name(name):
            continue
        repo = Path(repos_dir) / name
        if repo.is_dir():
            repos.append(repo)
    # a slash command copied into three or more repos is boilerplate, not a tool of one repo
    counts = {}
    for repo in repos:
        for c in repo_commands(repo):
            counts[c] = counts.get(c, 0) + 1
    shared = frozenset(c for c, n in counts.items() if n >= SHARED_COMMAND_REPOS)
    for repo in repos:
        e = repo_entry(repo, shared)
        if e:
            out.append(e)
    out.sort(key=lambda t: t["path"])
    return out


def safety_rank(name):
    return SAFETY.index(name) if name in SAFETY else None


def render(tools, repos=None):
    safety = sorted((t for t in tools if t["name"] in SAFETY), key=lambda t: (safety_rank(t["name"]), t["path"]))
    rest = [t for t in tools if t["name"] not in SAFETY]
    out = ["<!-- GENERATED FILE. Do not edit. Regenerate with: python3 ~/.claude/scripts/tools_catalog.py build -->",
           "# Our own command-line tools", "",
           "One line per tool: full path | what it does | when to use it. Taken from each tool's own header.", "",
           "## Safety tools (use these first)", ""]
    out += [tool_line(t) for t in safety]
    if repos:
        out += ["", "## Repo tools and MCP servers (check here before building anything)", "",
                "One line per repo whose own docs name a tool, MCP server or slash command: repo path | what the repo is | tools and the doc to read first.", ""]
        out += [tool_line(t) for t in repos]
    out += ["", "## All other tools (sorted by path)", ""]
    out += [tool_line(t) for t in rest]
    return "\n".join(out) + "\n"


def atomic_write(path, text):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_build(args):
    tools = collect(Path(args.scripts_dir), Path(args.hooks_dir), Path(args.hub_dir))
    bare = [t for t in tools if not t["what"]]
    if args.check:
        for t in bare:
            print("no usable header: " + t["path"])
        print(f"{len(tools)} tools, {len(bare)} without a usable header")
        return 1 if bare else 0
    repos = collect_repos(Path(args.repos_dir), Path(args.repos_list))
    atomic_write(args.catalog, render(tools, repos))
    print(f"wrote {args.catalog}: {len(tools)} tools, {len(repos)} repos, {len(bare)} without a usable header")
    return 0


# ---------- matcher ----------

def stem(w):
    if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def words(text):
    return {stem(w) for w in TOKEN.findall(text.lower()) if len(w) >= 3 and w not in STOP and not w.isdigit()}


def parse_catalog(text):
    tools = []
    for ln in text.splitlines():
        if not ln.startswith("- /"):
            continue
        parts = ln[2:].split(" | ", 2)
        path = parts[0].strip()
        tools.append({"path": path, "name": Path(path).name, "what": parts[1] if len(parts) > 1 else "",
                      "when": parts[2] if len(parts) > 2 else "", "line": ln})
    return tools


def name_words(name):
    stem_part = re.sub(r"\.[A-Za-z0-9]+$", "", name)
    return {stem(w) for w in TOKEN.findall(stem_part.lower()) if len(w) >= 3}


def rank(tools, prompt_text, limit):
    pw = words(prompt_text)
    n = len(tools)
    docs = []
    df = {}
    for t in tools:
        nw = name_words(t["name"])
        dw = words(t["what"] + " " + t["when"])
        docs.append((nw, dw))
        for w in nw | dw:
            df[w] = df.get(w, 0) + 1
    scored = []
    for t, (nw, dw) in zip(tools, docs):
        s = 0.0
        name_hits = 0
        desc_hits = 0
        for w in pw:
            if df.get(w, 0) > COMMON_SHARE * n:
                continue
            idf = math.log(1 + n / df[w]) if w in df else 0.0
            if w in nw:
                s += NAME_WEIGHT * idf
                name_hits += 1
            elif w in dw:
                s += DESC_WEIGHT * idf
                desc_hits += 1
        # a real match: a file-name word (or four description words), three different words in all, a high score
        if s >= MIN_SCORE and name_hits + desc_hits >= MIN_WORDS and (name_hits or desc_hits >= MIN_DESC_HITS):
            scored.append((-s, t["path"], t))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [x[2] for x in scored[:limit]]


def fallback_safety_lines(scripts_dir, hooks_dir):
    lines = []
    for name in SAFETY:
        for d in (scripts_dir, hooks_dir):
            p = Path(d) / name
            if p.is_file():
                lines.append(f"- {p}")
                break
    return lines


def closing():
    return (f"The full list is {CATALOG_LABEL}. Write a new helper only when nothing there fits.")


def build_match(args):
    tools = None
    cat = Path(args.catalog)
    if cat.is_file():
        tools = parse_catalog(cat.read_text(encoding="utf-8", errors="replace"))
    if not tools:
        tools = collect(Path(args.scripts_dir), Path(args.hooks_dir), Path(args.hub_dir))
        for t in tools:
            t["line"] = tool_line(t)
    safety = sorted((t for t in tools if t["name"] in SAFETY), key=lambda t: (safety_rank(t["name"]), t["path"]))
    rest = [t for t in tools if t["name"] not in SAFETY]
    prompt = Path(args.prompt_file).read_text(encoding="utf-8", errors="replace")
    picked = rank(rest, prompt, max(0, args.max))
    return [t["line"] for t in safety], [t["line"] for t in picked]


def cmd_match(args):
    try:
        safe, picked = build_match(args)
    except Exception:
        try:
            safe, picked = fallback_safety_lines(Path(args.scripts_dir), Path(args.hooks_dir)), []
        except Exception:
            safe, picked = [], []
    print("## Tools you already have for this job")
    print()
    for ln in safe + picked:
        print(ln)
    print()
    print(closing())
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tools_catalog.py", description=__doc__.splitlines()[0])
    ap.add_argument("--scripts-dir", default=str(SCRIPTS_DIR))
    ap.add_argument("--hooks-dir", default=str(HOOKS_DIR))
    ap.add_argument("--hub-dir", default=str(HUB_SCRIPTS_DIR))
    ap.add_argument("--catalog", default=str(CATALOG))
    ap.add_argument("--repos-dir", default=str(REPOS_DIR))
    ap.add_argument("--repos-list", default=str(REPOS_LIST))
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write TOOLS.md")
    b.add_argument("--check", action="store_true", help="list tools with no usable header; write nothing")
    m = sub.add_parser("match", help="print the tools section for a prompt file")
    m.add_argument("prompt_file")
    m.add_argument("--max", type=int, default=DEFAULT_MAX)
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        # a lane start must never fail on a bad flag: match degrades, build reports
        if argv is None:
            argv = sys.argv[1:]
        if argv and argv[0:1] == ["match"] or "match" in (argv or [])[:4]:
            print("## Tools you already have for this job\n")
            for ln in fallback_safety_lines(SCRIPTS_DIR, HOOKS_DIR):
                print(ln)
            print("\n" + closing())
            return 0
        return e.code if isinstance(e.code, int) else 2
    return cmd_build(args) if args.cmd == "build" else cmd_match(args)


if __name__ == "__main__":
    sys.exit(main())
