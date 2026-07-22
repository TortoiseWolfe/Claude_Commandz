---
description: Wire this repo up with a graphify knowledge graph (one-time setup)
scope: personal
---

Set up a persistent knowledge graph for a repository so Claude Code can query a map
instead of re-reading files every session.

## Input
$ARGUMENTS

If no path given, use the current working directory.

> Every command below was verified against graphify 0.9.23 on this machine.
> Note that **unknown flags are silently ignored** by this CLI — a typo does not
> error, it just quietly does nothing. Check for the expected output file, never
> assume a flag worked.

## Preflight — stop if any of these fail

1. `graphify --version` must resolve. If not, tell the user to run:
   ```bash
   uv tool install --python 3.12 "graphifyy[sql,leiden]"
   ```
   The `--python 3.12` pin is required — the `leiden` extra is gated on
   `python_version < "3.13"` and this host runs 3.13.5. Then **stop**.
2. Target must be a git repo (`git rev-parse --git-dir`).
3. If `graphify-out/graph.json` already exists, this repo is already set up —
   tell the user to run `/graph-refresh` instead and **stop**.

## Step 1 — Classify the repo

```bash
git ls-files | sed 's/.*\.//' | sort | uniq -c | sort -rn | head -12
git ls-files | wc -l
```

- **Code repo** — majority `.ts/.tsx/.js/.py/.cs/.rb/.go/.rs/.sql`
- **Content repo** — majority `.md/.txt/.pdf/.docx`

**State the classification and counts before continuing.** It decides whether the
first extraction is free or costs tokens, so the user should see the reasoning.

## Step 2 — Write `.graphifyignore`

`.gitignore` is respected automatically and merges with this file
(`.graphifyignore` evaluates last, and can only ever exclude *more*).

```
# NEVER index our own generated output. This line is not optional.
graphify-out/

node_modules/
.next/
dist/
build/
coverage/
storybook-static/
.venv/
*.lock
package-lock.json
pnpm-lock.yaml
*.min.js
*.map
```

**The `graphify-out/` rule is the single most important line in this file.**
Graphify's own docs say to commit `graphify-out/`, and `export wiki` writes
100–600 markdown articles into it. Those are tracked files in the repo, so the
next `graphify extract` **scans them as source documents** — the tool re-ingests
its own summaries.

On a content repo that is expensive *and* corrupting: a TranScripts refresh that
should have touched 4 new files began working through **145 generated wiki
articles**, 34 deep before it was killed. The resulting graph would cite its own
summaries as sources — the "AI quoting AI" feedback loop, where small errors
harden into facts because nothing outside the graph ever contradicts them.

`.gitignore` does not save you here: `graphify-out/` is *meant* to be committed,
so git-ignoring it defeats the purpose. The exclusion has to live in
`.graphifyignore`, which controls indexing rather than version control. Verify:
```bash
graphify extract . --code-only 2>&1 | head -3   # doc count must exclude wiki/
```

**Content repos:** also exclude machine-generated source data. In TranScripts,
`*/timed/*.json` are raw caption fragments — large, no concepts, and they would
cost a full semantic pass for nothing.

**Privacy gate (mandatory):** if the repo has any `private/` directories, add
`**/private/` explicitly even when `.gitignore` already covers them. Belt and
braces — `--no-gitignore` would otherwise sweep them in, and a graph is far
harder to un-leak than a file.

## Step 3 — Ignore files for git and Claude

Append to `.gitignore`:
```
# graphify — the rest of graphify-out/ is meant to be committed
graphify-out/cost.json
graphify-out/20*/
```
(`graphify-out/20*/` catches the dated backup dirs `cluster-only` creates on
every re-cluster.)

Create or append `.claudeignore`:
```
graph.json
graphify-out/
```
This matters: graphify writes into the workspace on every run, and without it
each write invalidates Claude Code's prompt cache, forcing a full re-upload at
cache-write rates on the next turn.

## Step 4 — Install the project-scoped skill

```bash
graphify install --project
```

Project-scoped keeps this contained to repos you deliberately set up. Writes a
`CLAUDE.md` section plus a `PreToolUse` hook nudging toward `graphify query`.

Do **not** pass `--strict` on first setup — it *blocks* the session's first raw
file read and redirects it to the graph, which is disorienting before the graph
has earned trust. Add later with `graphify install --project --strict`.

## Step 5 — Extract

**Code repo** — free, local tree-sitter AST, no API key, nothing leaves the machine:
```bash
graphify extract . --code-only
```

**Content repo** — the graph comes entirely from the LLM semantic pass, so it
costs real usage. Pick the backend deliberately:

```bash
graphify extract . --backend claude-cli    # no API key — shells out to Claude Code
graphify extract . --backend claude        # requires ANTHROPIC_API_KEY (separate billing)
```

`claude-cli` is the right default on this machine — no API key is configured and
none is needed. It reuses the Claude Code subscription. The catch: it is **forced
serial** (`max-concurrency` pinned to 1), so a few hundred files is slow and
consumes subscription usage rather than dollars.

**Report the file count and get an explicit go/no-go first.** Check for a
configured key before assuming a backend is available:
```bash
env | grep -E "ANTHROPIC_API_KEY|GOOGLE_API_KEY|OPENAI_API_KEY" | sed 's/=.*/=SET/'
```

Say the `--code-only` tradeoff out loud: it skips docs entirely. ScriptHammer,
for instance, has 967 doc files that stay out of the graph. Offer a doc-only
second pass as follow-up rather than silently dropping that knowledge.

## Step 6 — Cluster (required — extract alone writes no report)

```bash
graphify cluster-only .
```

`extract` writes `graph.json` but **not** `GRAPH_REPORT.md`. Clustering detects
communities and writes the report; skip it and the user has a graph they cannot read.

Two caveats to surface honestly:
- **`graph.html` is skipped above 5,000 nodes.** Don't point the user at a file
  that was never written. Offer `graphify tree` instead — it emits a D3
  collapsible-tree HTML with no node cap.
- **Community names need an LLM backend.** Without one, graphify falls back to
  naming each community after its **hub node** — `react`, `messaging.ts`,
  `test-user-factory.ts`. Those are mechanical, not descriptive.

  **`--missing-only` is a trap here.** It treats hub-derived names as
  already-labeled and skips the LLM entirely, reporting success while changing
  nothing. To actually get semantic names, omit it:
  ```bash
  graphify label . --backend claude-cli        # relabels all
  graphify label . --backend claude-cli --missing-only   # SKIPS hub-named ones
  ```
  Measured on ScriptHammer: `--missing-only` renamed 0 of 568. Without it, all
  573 became semantic — `Messaging Gate & UI Widgets`, `Local Message Cache &
  Encryption`, `Sign-In & Password Reset`. Roughly 6 LLM calls at batch-size 100,
  so it is cheap relative to extraction and worth doing on any repo you navigate.

  **Verify by inspecting the labels file, not the report.** Grepping
  `Community [0-9]` in `GRAPH_REPORT.md` counts community *ID references* in the
  node listings, not unnamed labels — it will mislead you:
  ```bash
  python3 -c "import json;v=json.load(open('graphify-out/.graphify_labels.json'));print(list(v.values())[:10])"
  ```
- Clustering is **order-dependent on `PYTHONHASHSEED`**. Bare runs shift the
  community count between invocations (576 → 565 → 563 observed). The git hook
  pins `PYTHONHASHSEED=0` for exactly this reason — do the same for reproducible
  manual runs:
  ```bash
  PYTHONHASHSEED=0 graphify cluster-only .
  ```

## Step 7 — Exports

```bash
graphify export wiki
```
573 community articles plus `wiki/index.md`, an explicit agent entry point. **This
is the artifact that helps Claude Code.** Always generate it.

The Obsidian vault is a separate call and is **opt-in**:
```bash
graphify export obsidian            # or: --dir ~/some/vault
```
Measure before recommending it. On ScriptHammer it produced **8,209 notes / 35 MB**,
one per graph node, and the notes carry only connections and frontmatter — no
source content. At that granularity a vault is thousands of stubs, which makes
browsing worse rather than better. Rule of thumb: skip it above ~2,000 nodes
unless the user specifically wants to browse in Obsidian.

## Step 8 — Verify before reporting success

```bash
grep -c '"id"' graphify-out/graph.json     # must be non-zero
```

If the repo has `private/` dirs, this is a **hard gate**:
```bash
grep -ri "private/" graphify-out/graph.json | head
```
Must return nothing. If it returns anything: delete `graphify-out/`, fix
`.graphifyignore`, re-extract. Commit nothing until clean.

## Step 9 — Refresh wiring

**Code repo:**
```bash
graphify hook install
```
Post-commit + post-checkout hooks and a git merge driver that union-merges
`graph.json` so parallel commits never leave conflict markers. Rebuilds are
AST-only — free and fast on every commit.

**Content repo:** no hook. Rebuilds run the LLM semantic pass, so firing per
commit spends tokens on every file change. Direct the user to `/graph-refresh`
after a batch instead.

The hook records the current interpreter as its first choice but falls back
through three more probes (`graphify-out/.graphify_python`, the `graphify`
launcher on PATH, then `python3`/`python`), so an upgrade degrades gracefully
rather than silently breaking. Re-running `graphify hook install` after an
upgrade is still tidier, but it is not the landmine it looks like.

**Check where the hooks actually landed** — it varies per repo and determines
whether collaborators inherit them. Three observed cases:

| Location | Trigger | Shared? |
|---|---|---|
| `.husky/` | repo uses husky | **Yes — tracked** |
| `.githooks/` | `core.hooksPath` is set | **Yes — tracked** |
| `.git/hooks/` | neither | No — local only |

```bash
git config core.hooksPath          # non-empty => hooks are tracked
graphify hook status
```
If the hooks are tracked, say so before committing — you are pushing a rebuild
hook onto everyone else on the repo.

The hook records the current interpreter as its first choice but falls back
through three more probes (`graphify-out/.graphify_python`, the `graphify`
launcher on PATH, then `python3`/`python`), so an upgrade degrades gracefully
rather than silently breaking.

**YAML is not a code language to graphify.** There is no YAML tree-sitter
grammar in the code path, so `--code-only` silently skips every `.yml`. On a
Drupal repo that means the entire config-sync layer stays out of the graph
(Chattanooga-Digital: 161 `.yml` files, none indexed). Say so rather than
implying the graph covers the repo.

## Output

Report classification and counts; nodes / edges / communities; the
`EXTRACTED` / `INFERRED` / `AMBIGUOUS` split and token cost; top community hubs;
whether `graph.html` was written or skipped; whether the Obsidian vault was
generated and why; the refresh mode chosen; and the privacy-gate result.

Then suggest one concrete query to try.
