---
description: Maintenance snapshot of spec-kit-extension-wireframe — issues, PRs, traffic, external mentions, support triage
---

# Extension Status

Read-only maintenance check for the wireframe extension repo and its catalog presence in `github/spec-kit`. Surfaces inbound support needs, engagement trends, release/catalog drift, external mentions, and repo hygiene in one pass.

## Usage

```
/extension-status                           # Full report
/extension-status --triage                  # Who needs help? (issues/PRs/mentions only)
/extension-status --traffic                 # Stars, clones, views, referrers only
/extension-status --mentions                # External GitHub mentions only
/extension-status owner/other-repo          # Run against a different repo
```

## Arguments

ARGUMENTS: $ARGUMENTS

- No args: Full report
- `--triage`: Support-needed items only (sections 1 + 4)
- `--traffic`: Engagement metrics only (section 2)
- `--mentions`: External mention search only (section 4)
- `REPO`: `owner/name` override (default: `TortoiseWolfe/spec-kit-extension-wireframe`)

---

## Instructions

### 1. Resolve target repo

Default: `TortoiseWolfe/spec-kit-extension-wireframe`.

If `$ARGUMENTS` contains an `owner/name` token (matches `/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/`), use that as `REPO`. Extract the owner as `OWNER` — it's the author.login to filter out of triage (self-authored issues/PRs).

Flags (`--triage`, `--traffic`, `--mentions`) control which sections to render. If no flags, render all five.

### 2. Support triage (section 1)

```bash
# Open issues, all authors
gh issue list --repo <REPO> --state open --limit 50 \
  --json number,title,author,createdAt,updatedAt,comments

# Open PRs, all authors
gh pr list --repo <REPO> --state open --limit 50 \
  --json number,title,author,createdAt,updatedAt

# Upstream catalog mentions of wireframe (last 30 days)
gh search issues "wireframe" --repo github/spec-kit \
  --created ">=$(date -d '30 days ago' +%Y-%m-%d)" \
  --limit 20 --json number,title,state,author,updatedAt,url
```

**Post-filter**: drop issues/PRs where `author.login == OWNER`.

**Render**: oldest-first table with columns `#`, `age`, `author`, `title`. Flag age >7d with `⚠`. Include a `url` column (clickable via terminal).

If section is empty, render `✓ No inbound items`. That's the good news signal.

### 3. Engagement (section 2)

```bash
gh api repos/<REPO> \
  -q '{stars: .stargazers_count, forks: .forks_count, watchers: .subscribers_count, pushed: .pushed_at}'

gh api repos/<REPO>/traffic/clones   -q '{count, uniques}'
gh api repos/<REPO>/traffic/views    -q '{count, uniques}'
gh api repos/<REPO>/traffic/popular/referrers
gh api repos/<REPO>/traffic/popular/paths
```

**Note**: traffic endpoints require push access to `<REPO>`. If the REPO override points at a repo the user doesn't own, these will 403 — catch and print `(no traffic access)` instead of failing.

**Render**: one compact table. Top 5 referrers and top 5 paths, sorted by `uniques` descending.

### 4. Release health (section 3)

```bash
# Local latest release
gh release list --repo <REPO> --limit 3 --json tagName,publishedAt

# Asset download counts on latest
gh api repos/<REPO>/releases/latest \
  -q '{tag: .tag_name, assets: [.assets[] | {name, downloads: .download_count}]}'

# Catalog-advertised version (only meaningful for the wireframe extension)
curl -s https://raw.githubusercontent.com/github/spec-kit/main/extensions/catalog.community.json \
  | jq -r '.extensions.wireframe.version // empty'
```

**Drift check**: if local latest tag differs from catalog version, flag as `⚠ catalog drift: catalog=vX.Y.Z, local=vA.B.C`. Suggest: "File a catalog bump PR against github/spec-kit".

Only apply the drift check when `REPO == TortoiseWolfe/spec-kit-extension-wireframe` — for override repos, skip it (we don't know their catalog relationship).

### 5. External mentions (section 4)

```bash
# Mentions anywhere on GitHub (last 30 days to keep noise low)
gh search issues "spec-kit-extension-wireframe" \
  --created ">=$(date -d '30 days ago' +%Y-%m-%d)" \
  --limit 20 --json repository,title,number,author,state,createdAt,url

# Code that references the extension (indicates adoption)
gh search code "spec-kit-extension-wireframe" \
  --limit 20 --json repository,path,url
```

**Post-filter**: drop results where `repository.nameWithOwner` is `github/spec-kit` or `<REPO>` itself. Those aren't external mentions.

**Render**: two short lists. If both empty after filtering, render `✓ No external mentions yet` — that's accurate and expected for a just-launched extension.

### 6. Maintenance hygiene (section 5)

```bash
# Branches on the repo (to spot cleanup candidates)
gh api repos/<REPO>/branches \
  -q '.[] | {name, protected, commit_sha: .commit.sha[0:7]}'

# Age of last main commit
gh api repos/<REPO>/commits/main -q '.commit.committer.date'

# Dependabot alerts (requires admin on repo; degrade gracefully)
gh api repos/<REPO>/dependabot/alerts 2>/dev/null \
  -q '[.[] | select(.state == "open")] | length' || echo "(no dependabot access)"
```

**Render**: flag any branch other than `main` or release branches (`release/*`, `v*`) as a cleanup candidate. Flag last-commit age >30d as `⚠ stale main`.

### 7. Render report

Start with a one-line TL;DR header, then sections separated by `---`.

TL;DR format:
```
📬 {N} support items • 📈 {clones} clones ({uniques}u / 14d) • 🏷️ {latest_tag} live • 🔍 {N} external mentions
```

If `--triage` / `--traffic` / `--mentions` was passed, only render that slice.

Finish with a footer listing the run timestamp in the user's local time and a UTC timestamp.

---

## Filtered views

### --triage
Sections 1 + 4 only. Header changes to: `🔎 Triage view — {N} items need attention`.

### --traffic
Section 2 only. Header: `📈 Traffic view — last 14 days`.

### --mentions
Section 4 only. Header: `🔍 External mentions — last 30 days`.

---

## Related

- `/schedule` — once the signal is worth it, schedule this daily (e.g. `0 9 * * 1-5 /extension-status --triage`)
- `/session-stats` — token/cost telemetry for the current session
- `/status` — project-internal dashboard (different scope; this one is external GitHub)

## Roadmap

Planned enhancements tracked on [`TortoiseWolfe/Claude_Commandz` issues](https://github.com/TortoiseWolfe/Claude_Commandz/issues?q=label%3Aextension-status):

1. Inbound-engagement assist (draft replies for non-owner issues/PRs)
2. Snapshot diff ("what changed since last run")
3. `/schedule` integration helper
4. Multi-extension config file
