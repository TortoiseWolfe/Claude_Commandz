---
description: Update this repo's graphify knowledge graph and run the lint pass
scope: personal
---

Refresh the knowledge graph and review what the agent wrote. This is Karpathy's
third operation — **lint** — the one every tutorial skips and the reason these
systems rot.

## Input
$ARGUMENTS

Optional repo path, so this can run against another repo without `cd`. Defaults
to the current working directory.

> Verified against graphify 0.9.23. **Unknown flags are silently ignored** by
> this CLI — always confirm the expected output changed, never assume.

## Step 0 — Preflight

If `graphify-out/graph.json` is absent, this repo was never set up. Say so, point
at `/graphify-init`, and **stop**. Do not silently initialize.

## Step 1 — Staleness check (cheap, do this first)

```bash
graphify check-update .
```
Purpose-built and cron-safe. **Empty output means nothing is pending — this is
the authoritative signal.** Trust it over the SHA comparison below.

`GRAPH_REPORT.md` also records the commit it was built from, but **a SHA
mismatch alone does not justify re-extracting.** Installing the skill, editing
`CLAUDE.md`, or committing `graphify-out/` all move HEAD without changing a
single indexed file. Measured across five repos after a round of graphify
commits: all five reported "STALE" by SHA, and **zero had an indexed source file
change** — the diffs were entirely `.claude/skills/graphify/*` and config.

On a content repo that false positive is expensive: it triggers a semantic
re-extract, which is hours of `claude-cli` quota for no new information.

So filter the diff before believing it:

```bash
BUILT=$(grep -oP 'Built from commit: `\K[0-9a-f]+' graphify-out/GRAPH_REPORT.md)
git -c core.quotepath=false diff --name-only "$BUILT..HEAD" \
  | grep -vE '^(graphify-out/|\.claude/|\.husky/|\.githooks/|CLAUDE\.md|AGENTS\.md|\.gitattributes|\.graphifyignore|\.claudeignore|\.gitignore|\.prettierignore)'
```

`core.quotepath=false` is load-bearing: git wraps paths containing non-ASCII in
double quotes, and a leading `"` defeats the `^graphify-out/` anchor. Without it
a wiki article named `Career_Strategy_w-_Chelsea_—_Skills…md` slips through and
fakes a refresh.

The excluded set is everything **graphify itself installs**: its skill directory,
the `CLAUDE.md`/`AGENTS.md` section, the ignore files, the hook scripts
(`.husky/`, `.githooks/`), and `.gitattributes` — which it edits to register the
`graph.json` merge driver. None of these feed the graph.

**Empty result → the graph is current regardless of what the SHA says.** Report
that and stop. Only files that actually feed the graph should trigger work.

Then sanity-check what survives against what this repo actually indexes. Binary
assets that were never in the graph (`.skp`, `.png`, `.glb`, `Zone.Identifier`
files) will still show up in a raw diff and are not a reason to re-extract.

### Step 1b — Merge-commit check (do not skip; the hook is wrong here)

The post-commit hook computes its changed-file list with
`git diff --name-only HEAD~1 HEAD`. For a **merge commit** that diffs against the
*first parent only*, so every file arriving from the merged branch is invisible
to the rebuild. The graph then looks fresh — the commit SHA matches — while
being materially incomplete.

This is measured, not theoretical: after a real merge in `cd-hubzilla` the hook
rebuilt the graph to **151 nodes**; a full re-extract of the same commit found
**394**. The hook had missed 62% of the repo.

```bash
BUILT=$(grep -oP 'Built from commit: `\K[0-9a-f]+' graphify-out/GRAPH_REPORT.md)
git log --merges --oneline "$BUILT..HEAD" 2>/dev/null
```

**Order matters: this is a modifier, not a trigger.** Run the indexed-file filter
*first*. Merge commits only matter once you have established that a rebuild is
actually needed:

- **0 indexed files changed** → **CURRENT, regardless of merge commits.** Stop.
  Merges between an ancestor and HEAD do not imply missed content; the hook's
  blind spot only matters if the hook was the thing that rebuilt.
- **Files changed AND merge commits present** → `update` inherits the blind spot.
  Use `extract --force` (Step 2).
- **Files changed, no merges** → plain `update` is sufficient.

Measured false positive: ScriptHammer after merging PR #336 reported 2 merge
commits since its build SHA, but 0 indexed files changed — the diff was 10 files,
all graphify config. Treating the merge count as a standalone trigger would have
forced a full re-extract for nothing. On a content repo that is hours of quota.

## Step 2 — Update

```bash
graphify update .
```

Re-extracts only changed files against the SHA-256 cache, **and clusters in the
same pass** — no separate `cluster-only` needed here. No LLM, no API cost for
code. Expect output like `0 code changed; 1442 unchanged; 0 deleted` when idle.

**If Step 1b found merge commits**, `update` is not enough — it inherits the same
changed-file blind spot. Force a full re-scan instead:
```bash
graphify extract . --code-only --force
PYTHONHASHSEED=0 graphify cluster-only . --no-viz    # --no-viz above 5,000 nodes
```
`--force` skips the incremental manifest gate and re-dispatches everything. On a
code repo this is still free (local AST) — roughly two minutes for 1,400 files —
so when in doubt, force. The only cost is wall-clock.

**Also force after installing a new language extra.** Extras change what the
parser can see, and previously-skipped files stay skipped under an incremental
run. ScriptHammer sat at **0 nodes from 6 `.sql` files** because its first
extract predated `graphifyy[sql]`; a `--force` re-extract recovered 27 nodes,
including the monolithic Supabase migration and the RLS policy contracts.

For content repos, changed docs need the semantic pass:
```bash
graphify extract . --backend claude-cli
```
`claude-cli` needs no API key — it shells out to Claude Code — but is forced
serial, so it is slow and consumes subscription usage. State the changed-file
count and get a go/no-go before running it. Because only changed files are
re-extracted, a routine refresh is usually a handful of files, not the whole corpus.

## Step 3 — Re-export so browsable artifacts don't drift

```bash
graphify export wiki
```
Only re-run `graphify export obsidian` if a vault already exists — check for
`graphify-out/obsidian/` first. Don't create a 35 MB vault of stub notes on a
refresh the user didn't ask for.

### Step 3b — Re-merge into the global graph (it goes stale silently)

**Only if this repo is a member of the global graph.** The post-commit hook
rebuilds the local graph but never re-merges — `--global` is not in the hook — so
the global copy drifts behind with no warning anywhere.

```bash
graphify global list                     # is this repo's tag here?
```

If listed, compare its node count against the local graph and re-merge when they
disagree:
```bash
graphify extract . --code-only --global --as <tag>
```
The merge prunes the old copy rather than duplicating (`+394 nodes, -74 pruned`),
so re-running is safe and idempotent. Observed drift after one merge commit:
global held 74 nodes while the repo had 394.

Use the **same tag** as the original merge. A new tag silently creates a second
copy of the repo in the global graph instead of replacing the first —
`graphify global list` is the check, and `graphify global remove <tag>` is the fix.

> **Do not oversell the global graph.** It **concatenates; it does not link.**
> Measured across three CD repos: 792 nodes and 1,063 links, where 1,063 is
> exactly the per-repo sum (948+108+7) — **zero cross-repo edges**. Nodes carry a
> `repo` tag, so you get cross-repo *search* in one file, not cross-repo
> *understanding*. Graphify is arguably right to refuse: the only labels shared
> between those repos were generic (`log()`, `warn()`, `name`, `version`), and
> linking them would fabricate edges. Note also that the file is NetworkX
> node-link format — edges are under `links`, not `edges`, which is an easy way
> to misread it as empty.

## Step 4 — Reflect (the work-memory layer)

```bash
graphify reflect --if-stale
```
Aggregates `graphify-out/memory/` outcomes into `reflections/LESSONS.md`. A true
no-op when `LESSONS.md` is already newer than every input, so it is safe to run
every time.

This only produces anything if Q&A outcomes were recorded via
`graphify save-result --question ... --answer ... --outcome useful|dead_end|corrected`.
If `graphify-out/memory/` is empty, say so plainly — an empty LESSONS.md is not a
failure, it means nothing has been fed back yet.

## Step 5 — The human lint pass

This is the actual point of the command. Surface for review, **do not auto-delete
anything**:

1. **New `AMBIGUOUS` edges** — the model flagging its own uncertainty. Check the
   `EXTRACTED / INFERRED / AMBIGUOUS` split in `GRAPH_REPORT.md` and compare to
   the previous run.
2. **Stale nodes** — nodes whose source file changed since the node was written.
   `graphify reflect --graph graphify-out/graph.json` marks these
   "code changed — re-verify".
3. **Orphans** — nodes with no inbound edges, usually deleted or renamed code.
4. **Suspicious hubs** — build output or vendored code that leaked past
   `.graphifyignore` and now ranks as an architectural hub. Check `god-nodes`:
   ```bash
   graphify god-nodes --top 15
   ```
   A `.next` or `node_modules` entry here means the ignore rules need fixing.

5. **Self-ingestion — check this before every refresh.** `graphify-out/` must be
   in `.graphifyignore`. `export wiki` writes hundreds of markdown articles
   there, `graphify-out/` is meant to be committed, and tracked files are
   scannable — so without the rule the tool re-ingests its own summaries and the
   graph begins citing itself.
   ```bash
   grep -q '^graphify-out/$' .graphifyignore || echo "MISSING — fix before refreshing"
   ```
   Caught live: a TranScripts refresh that should have touched 4 new files began
   working through 145 generated wiki articles. `.gitignore` cannot fix this —
   the rule must be in `.graphifyignore`, which governs indexing rather than
   version control.

Present these as a short list with a recommendation each. The user decides.

## Step 6 — Privacy re-check

If the repo has `private/` directories, re-run the gate — new files may have
landed since setup:
```bash
grep -ri "private/" graphify-out/graph.json | head
```
Must return nothing.

## Output

Report:
- Whether anything actually changed (and say so plainly if nothing did)
- **Whether merge commits forced a full re-extract** (Step 1b) — and if so, the
  node delta versus the incremental graph, since that number is the evidence the
  hook alone is not sufficient
- **Whether the global graph needed re-merging**, and the drift if it did
- Files re-extracted, token cost if any
- Node / edge / community deltas since the last run. Community counts drift
  between bare runs because clustering is order-dependent on `PYTHONHASHSEED`;
  prefix manual re-clusters with `PYTHONHASHSEED=0` (the git hook already does)
  before reporting a delta as meaningful
- The lint findings from Step 5, each with a recommendation
- Privacy-gate result, if applicable

Keep it short when the answer is "nothing changed." That is the common case and
should cost the user two lines, not a report.
