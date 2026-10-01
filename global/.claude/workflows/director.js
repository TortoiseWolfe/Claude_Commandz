export const meta = {
  name: 'director',
  description: 'Opus plans and reviews; Haiku/Sonnet workers edit in sibling git worktrees behind deterministic checks',
  whenToUse: 'A batch of well-specified, mostly mechanical changes in one repo. Args: {repo, goal, base?, checks, logDir?}. Read <repo>/.claude/director.json for checks.',
  phases: [
    { title: 'Plan', detail: 'Opus splits the goal into tiered items with checks' },
    { title: 'Baseline', detail: 'pin base, build once, checks green and acceptance red at base' },
    { title: 'Work', detail: 'per item: worktree, worker, independent checker, retry/escalate' },
    { title: 'Review', detail: 'blind Opus review, at most 2 rounds' },
    { title: 'Cleanup', detail: 'prune the unused networks of the compose projects the checks name' },
  ],
}

// Invoke: Workflow({ name: 'director', args: { repo: '/abs/path', goal: '...', checks: {...}, base: 'origin/main', logDir: '<scratchpad>' } })
// Merging, pushing and worktree cleanup stay in the main loop. This script never pushes.
// Extracted from ~/.claude/skills/game-demo/references/gauntlet-workflow.md and
// ~/.claude/workflows/rescuedogs-weekly-review.js.

// args may arrive as an object OR a JSON string depending on how it was passed.
let A = args
if (typeof A === 'string') {
  try { A = JSON.parse(A) } catch (e) { A = {} }
}
if (!A || typeof A !== 'object') A = {}
const repo = A.repo ? String(A.repo).replace(/\/+$/, '') : null
const goal = A.goal ? String(A.goal) : null
const baseRef = A.base ? String(A.base) : 'origin/main'
const checks = A.checks && typeof A.checks === 'object' ? A.checks : {}
const logDir = A.logDir ? String(A.logDir) : '/tmp'
const MAX_REVIEW_ROUNDS = 2
const MAX_WORKER_ROUNDS = 5

if (!repo || !goal || Object.keys(checks).length === 0) {
  log('ERROR: args need repo (absolute path), goal, and a non-empty checks map.')
  return { error: 'missing args', need: ['repo', 'goal', 'checks'] }
}
const name = repo.slice(repo.lastIndexOf('/') + 1)
const parent = repo.slice(0, repo.lastIndexOf('/'))
const wtPath = (id) => `${parent}/${name}-wf-${id}`

// Every `docker compose` in a check must name its project. Without -p, compose names the
// project after the directory, so each worktree creates its own <dir>_default network and
// nothing ever removes it. On 2026-09-30 that exhausted Docker's address pools ("all
// predefined address pools have been fully subnetted") and blocked every new compose run.
const COMPOSE_RE = /docker[ -]compose\b[^;|&]*/g
const PROJECT_RE = /\s(?:-p|--project-name)[\s=]+([A-Za-z0-9][A-Za-z0-9_.-]*)/
const unnamed = Object.entries(checks).filter(([, cmd]) =>
  (String(cmd).match(COMPOSE_RE) || []).some((seg) => !PROJECT_RE.test(seg)))
if (unnamed.length) {
  const which = unnamed.map(([k]) => k).join(', ')
  log(`ERROR: ${which} run docker compose without -p; each worktree would leak a network. Add -p ${name.toLowerCase()}-wf (compose project names must be lowercase).`)
  return { error: 'check runs docker compose without -p', checks: unnamed.map(([k]) => k) }
}
const composeProjects = [...new Set(Object.values(checks).flatMap((cmd) =>
  (String(cmd).match(COMPOSE_RE) || []).map((seg) => seg.match(PROJECT_RE)[1])))]

const RULES = `House rules for this task:
- Work only inside the worktree path given. Never touch the main checkout at ${repo}, never push, never switch branches, never merge.
- Read <worktree>/CLAUDE.md first and follow it.
- Edit only the files listed. If the change needs another file, stop and say so.
- Docker-first: never run npm/pnpm/pip install or sudo on the host.${composeProjects.length ? `
- Any docker compose you run yourself must name its project, exactly as the checks do: -p ${composeProjects[0]}. Without -p, compose names the project after your worktree folder and leaves a network behind (a worker leaked one on 2026-09-30 by copying a docstring's run line).` : ''}
- Never put the user's name, email or any personal identifier into files, commits or requests.`

// ---- helpers --------------------------------------------------------------

// Custom agent types may not resolve until a session restart; fall back to general-purpose.
async function call(prompt, opts) {
  try {
    return await agent(prompt, opts)
  } catch (e) {
    if (opts.agentType && opts.agentType !== 'general-purpose') {
      log(`agentType ${opts.agentType} failed (${String(e).slice(0, 80)}); retrying as general-purpose with model ${opts.model || 'inherit'}`)
      return await agent(prompt, { ...opts, agentType: 'general-purpose' })
    }
    throw e
  }
}

// Shell proxy: a Haiku agent runs the script verbatim; plain JS parses the sentinels.
async function sh(script, label, phaseName) {
  const prompt = `You are a shell proxy. Run the script between <<<SCRIPT and SCRIPT>>> with the Bash tool, unchanged, in ONE call with timeout 600000. Run nothing else. Reply with the last 60 lines of its output copied exactly, no commentary. If the Bash call errors, reply with the error and the line __RC=PROXY_ERROR__.

<<<SCRIPT
${script}
SCRIPT>>>`
  const text = await call(prompt, { label, phase: phaseName, agentType: 'shell-proxy', model: 'haiku' })
  return String(text || '')
}

// Remove this run's compose networks once nothing uses them. prune skips any network with
// a container attached, and the label filter keeps it to the projects named in the checks.
async function pruneNetworks(phaseName) {
  if (!composeProjects.length) return
  const script = composeProjects.map((p) =>
    `docker network prune -f --filter label=com.docker.compose.project=${p} >/dev/null 2>&1; echo "__RC_PRUNE_${p.replace(/[^A-Za-z0-9]/g, '_')}=$?__"`).join('\n')
  await sh(script, 'prune networks', phaseName)
}

function sentinel(text, key) {
  // Tolerates a missing closing '__': the Haiku proxy sometimes relays "__RC_SETUP=0__" as
  // "__RC_SETUP=0" (markdown reads __x__ as bold), which once marked two good setups failed.
  const re = new RegExp('__' + key + '=([^\\s]*?)(?=__|\\s|$)', 'gm')
  let m, last = null
  while ((m = re.exec(text)) !== null) last = m[1]
  return last
}
function sentinels(text, key) {
  // Tolerates a missing closing '__': the Haiku proxy sometimes relays "__RC_SETUP=0__" as
  // "__RC_SETUP=0" (markdown reads __x__ as bold), which once marked two good setups failed.
  const re = new RegExp('__' + key + '=([^\\s]*?)(?=__|\\s|$)', 'gm')
  const out = []
  let m
  while ((m = re.exec(text)) !== null) out.push(m[1])
  return out
}
const tailOf = (text, n) => String(text).split('\n').slice(-n).join('\n')

// ---- schemas --------------------------------------------------------------

const PLAN_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          id: { type: 'string', pattern: '^[a-z0-9][a-z0-9-]{1,30}$' },
          tier: { type: 'string', enum: ['haiku', 'sonnet'] },
          title: { type: 'string' },
          instructions: { type: 'string', description: 'complete spec a worker can follow without judgment calls; exact values, exact file edits' },
          files: { type: 'array', items: { type: 'string' }, description: 'repo-relative paths the worker may change; nothing else' },
          check: { type: 'string', description: 'name of one command in the checks map (regression gate, green at base)' },
          accept_cmd: { type: 'string', description: 'shell command run from the worktree root; exits non-zero at base and 0 once the item is done' },
          acceptance: { type: 'array', items: { type: 'string' } },
          jev_checks: { type: 'array', items: { type: 'string' }, description: '2-6 narrow yes/no checks for the Jev shadow pre-screen, each quoting the exact code text the finished diff must contain' },
        },
        required: ['id', 'tier', 'title', 'instructions', 'files', 'check', 'accept_cmd', 'acceptance', 'jev_checks'],
      },
    },
    opus_keep: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: { title: { type: 'string' }, reason: { type: 'string' } },
        required: ['title', 'reason'],
      },
    },
  },
  required: ['items', 'opus_keep'],
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    verdict: { type: 'string', enum: ['pass', 'revise'] },
    blocking: { type: 'array', items: { type: 'string' }, description: 'concrete: file, line, what is wrong, what it should be' },
    notes: { type: 'string' },
  },
  required: ['verdict', 'blocking'],
}

// ---- Phase 1: Plan (Opus) -------------------------------------------------
phase('Plan')
const checkMenu = Object.entries(checks).map(([k, v]) => `- ${k}: ${v}`).join('\n')
const plan = await call(
  `You are the head engineer planning a batch of work in ${repo}. Cheaper models will do the edits; you decide what they do.

READ-ONLY: read ${repo}/CLAUDE.md and whatever code you need, but do not create worktrees, write files, commit, or run builds or tests. The Baseline stage runs every check for you, and the workers build. The goal:
${goal}

Split it into items. For each item:
- tier "haiku" for mechanical edits with no judgment left; "sonnet" for a straightforward build from your spec. Anything needing design judgment, client input or taste goes in opus_keep instead, with the reason.
- instructions must be a complete spec: exact values, exact files, exact text where it matters. A worker should never have to decide anything.
- files: every repo-relative path the worker may touch. No two items may share a file.
- Items run in parallel from the same base and can't see each other's changes. If one item's correctness depends on another's change (docs describing behaviour another item changes, a test for code another item adds), merge them into ONE item, or put the dependent one in opus_keep.
- check: the NAME of one regression command from this menu (it must already pass at ${baseRef}):
${checkMenu}
- accept_cmd: a shell command run from the worktree root that FAILS at ${baseRef} and PASSES once the item is done. Prefer \`git grep -q\` / \`test -f\` / a single Docker test; the host grep is ugrep and skips gitignored files. No host installs.
- acceptance: the plain-language criteria the reviewer will hold it to.
- jev_checks: 2-6 yes/no questions for a cheap text-matching pre-screen (Jev). Each must quote the EXACT code text the finished diff should contain, for example: "In the change, is plywood-5/8 written as \`actual: 19.0 / 32\`?". Jev compares text and does no arithmetic: "19/32 inch" scored 0.69 on a wrong value, while the exact code text scored 0.03.
Use short kebab-case ids.`,
  { label: 'plan', phase: 'Plan', schema: PLAN_SCHEMA, effort: 'high', agentType: 'Plan' }
)
if (!plan || !Array.isArray(plan.items)) {
  log('Planner returned nothing usable; stopping.')
  return { error: 'plan failed' }
}

// Plain-code guards: unknown check names and file overlaps are deferred, not silently dropped.
const deferred = []
const claimed = new Map()
const items = []
for (const it of plan.items) {
  if (!checks[it.check]) { deferred.push({ id: it.id, reason: `unknown check "${it.check}"` }); continue }
  const clash = it.files.find((f) => claimed.has(f))
  if (clash) { deferred.push({ id: it.id, reason: `shares ${clash} with ${claimed.get(clash)}` }); continue }
  it.files.forEach((f) => claimed.set(f, it.id))
  items.push(it)
}
log(`Planned ${plan.items.length} items: ${items.length} dispatchable, ${deferred.length} deferred, ${plan.opus_keep.length} kept for Opus`)
deferred.forEach((d) => log(`deferred ${d.id}: ${d.reason}`))

// ---- Phase 2: Baseline (barrier: one image build, one base tree) ----------
phase('Baseline')
const baseWt = wtPath('base')
const usedChecks = [...new Set(items.map((i) => i.check))]
const baseScript = [
  'set -u',
  `cd ${repo} || { echo "__RC=NO_REPO__"; exit 0; }`,
  'git fetch --prune origin >/dev/null 2>&1; echo "__RC_FETCH=$?__"',
  `BASE=$(git rev-parse ${baseRef}); echo "__BASE=\${BASE}__"`,
  'echo "__PORCELAIN=$(git status --porcelain | sha1sum | cut -c1-12)__"',
  `if [ -e ${baseWt} ]; then git worktree remove --force ${baseWt} >/dev/null 2>&1; fi`,
  `git worktree add --detach ${baseWt} "$BASE" >/dev/null 2>&1; echo "__RC_BASEWT=$?__"`,
  `cd ${baseWt}`,
  ...usedChecks.map((c) => `( ${checks[c]} ) > ${logDir}/director-${name}-base-${c}.log 2>&1; echo "__RC_BASECHECK_${c}=$?__"`),
  ...items.map((i) => `( ${i.accept_cmd} ) > /dev/null 2>&1; echo "__RC_BASEACCEPT_${i.id.replace(/-/g, '_')}=$?__"`),
].join('\n')
const baseOut = await sh(baseScript, 'baseline', 'Baseline')
const baseSha = sentinel(baseOut, 'BASE')
const porcelain = sentinel(baseOut, 'PORCELAIN')
if (!baseSha || !/^[0-9a-f]{40}$/.test(baseSha) || sentinel(baseOut, 'RC_BASEWT') !== '0') {
  log('Baseline setup failed; stopping.')
  await pruneNetworks('Baseline')
  return { error: 'baseline failed', output: tailOf(baseOut, 30) }
}
const ready = []
for (const it of items) {
  const checkRc = sentinel(baseOut, `RC_BASECHECK_${it.check}`)
  const acceptRc = sentinel(baseOut, `RC_BASEACCEPT_${it.id.replace(/-/g, '_')}`)
  if (checkRc !== '0') { deferred.push({ id: it.id, reason: `check ${it.check} is red at base (rc ${checkRc})` }); continue }
  if (acceptRc === '0') { deferred.push({ id: it.id, reason: 'accept_cmd already passes at base, so it proves nothing' }); continue }
  ready.push(it)
}
log(`Base ${baseSha.slice(0, 8)}; ${ready.length} items cleared baseline`)

// ---- Phase 3+4: per item, pipeline (no barrier) ---------------------------
function workerPrompt(it, wt, feedback) {
  return `${RULES}

Worktree: ${wt} (branch wf/${it.id}, based on ${baseSha.slice(0, 12)}).
Task: ${it.title}

Spec:
${it.instructions}

Files you may change: ${it.files.join(', ')}
Acceptance: ${it.acceptance.join(' | ')}
You may run, from the worktree root: ${checks[it.check]}
and: ${it.accept_cmd}
${feedback ? `\nThe previous attempt was rejected. Fix exactly this, adding a new commit:\n${feedback}\n` : ''}
Commit with git -C ${wt} commit and a message saying what changed and why. Reply with files changed and the commit SHA.`
}

async function runCheck(it, wt, round) {
  const L = `${logDir}/director-${name}-${it.id}-r${round}`
  const out = await sh([
    'set -u',
    `cd ${wt} || { echo "__RC=NO_WT__"; exit 0; }`,
    `( ${checks[it.check]} ) > ${L}-check.log 2>&1; rc=$?; tail -n 15 ${L}-check.log; echo "__RC_CHECK=\${rc}__"`,
    `( ${it.accept_cmd} ) > ${L}-accept.log 2>&1; rc=$?; tail -n 8 ${L}-accept.log; echo "__RC_ACCEPT=\${rc}__"`,
    'echo "__HEAD=$(git rev-parse HEAD)__"',
    'echo "__DIRTY=$(git status --porcelain | wc -l | tr -d \' \')__"',
    `git diff --name-only ${baseSha}..HEAD | sed 's/^/__FILE=/;s/$/__/'`,
    ...(it.jev_checks && it.jev_checks.length ? [
      `cat > ${L}-jev-acc.txt <<'JEV_ACC_EOF'\n${it.acceptance.join('\n')}\nJEV_ACC_EOF`,
      `cat > ${L}-jev-q.json <<'JEV_Q_EOF'\n${JSON.stringify(it.jev_checks)}\nJEV_Q_EOF`,
      `git diff ${baseSha}..HEAD | python3 ~/.claude/scripts/jev_precheck.py --acceptance ${L}-jev-acc.txt --questions-file ${L}-jev-q.json`,
    ] : []),
  ].join('\n'), `check:${it.id}`, 'Work')
  const reasons = []
  const rcCheck = sentinel(out, 'RC_CHECK'), rcAccept = sentinel(out, 'RC_ACCEPT')
  const head = sentinel(out, 'HEAD'), dirty = sentinel(out, 'DIRTY')
  const files = sentinels(out, 'FILE')
  if (rcCheck !== '0') reasons.push(`${it.check} failed (rc ${rcCheck})`)
  if (rcAccept !== '0') reasons.push(`accept_cmd failed (rc ${rcAccept})`)
  if (!head || head === baseSha) reasons.push('no commit on the branch')
  if (dirty !== '0') reasons.push(`uncommitted changes (${dirty})`)
  const stray = files.filter((f) => !it.files.includes(f))
  if (stray.length) reasons.push(`touched files outside scope: ${stray.join(', ')}`)
  if (files.length === 0) reasons.push('diff against base is empty')
  const jevMinRaw = sentinel(out, 'JEV_MIN')
  const jev = jevMinRaw === null ? null : { min: /^[0-9.]+$/.test(jevMinRaw) ? Number(jevMinRaw) : null, raw: jevMinRaw, per: sentinels(out, 'JEV_Q[0-9]+') }
  return { pass: reasons.length === 0, reasons, head, jev, tail: tailOf(out, 25) }
}

const results = await pipeline(ready, async (it) => {
  const wt = wtPath(it.id)
  const setup = await sh([
    'set -u',
    `cd ${repo}`,
    `if git show-ref --verify --quiet refs/heads/wf/${it.id} || [ -e ${wt} ]; then echo "__RC_SETUP=EXISTS__"; exit 0; fi`,
    `git worktree add -b wf/${it.id} ${wt} ${baseSha} >/dev/null 2>&1; echo "__RC_SETUP=$?__"`,
  ].join('\n'), `setup:${it.id}`, 'Work')
  const rcSetup = sentinel(setup, 'RC_SETUP')
  if (rcSetup !== '0') return { id: it.id, status: rcSetup === 'EXISTS' ? 'refused-existing-branch' : 'setup-failed', tier: it.tier }

  let tier = it.tier, escalated = false, checkFails = 0, reviewRounds = 0, workerRounds = 0
  let firstCheckPass = null, firstReviewPass = null, feedback = null, lastCheck = null, lastVerdict = null, jevAtFirstReview = null
  while (workerRounds < MAX_WORKER_ROUNDS) {
    workerRounds++
    await call(workerPrompt(it, wt, feedback), tier === 'haiku'
      ? { label: `work:${it.id}:haiku`, phase: 'Work', agentType: 'worker-mechanical', model: 'haiku' }
      : { label: `work:${it.id}:sonnet`, phase: 'Work', agentType: 'worker-builder', model: 'sonnet', effort: 'medium' })
    lastCheck = await runCheck(it, wt, workerRounds)
    if (firstCheckPass === null) firstCheckPass = lastCheck.pass
    if (!lastCheck.pass) {
      checkFails++
      feedback = `Checker failures: ${lastCheck.reasons.join('; ')}\nOutput tail:\n${lastCheck.tail}`
      if (checkFails === 1) continue
      if (tier === 'haiku' && !escalated) { tier = 'sonnet'; escalated = true; log(`${it.id}: escalating to sonnet`); continue }
      break
    }
    lastVerdict = await call(
      `Review one change blind. Worktree ${wt}, base commit ${baseSha}. Get the diff yourself with git -C ${wt} diff ${baseSha}..HEAD and read ${wt}/CLAUDE.md. Read-only: never edit, commit, checkout or push.

Item: ${it.title}
Spec given to the worker:
${it.instructions}
Acceptance: ${it.acceptance.join(' | ')}

The regression check and acceptance command already pass. Judge what tests miss: values wrong against the spec, requirements dropped, scope creep, broken repo conventions, misleading docs. pass = you'd merge it as is. revise = at least one concrete blocking item (file, line, wrong, should be).`,
      { label: `review:${it.id}`, phase: 'Review', agentType: 'reviewer-senior', model: 'opus', effort: 'high', schema: VERDICT_SCHEMA })
    reviewRounds++
    if (firstReviewPass === null) { firstReviewPass = !!lastVerdict && lastVerdict.verdict === 'pass'; jevAtFirstReview = lastCheck.jev }
    if (lastVerdict && lastVerdict.verdict === 'pass') {
      return { id: it.id, status: 'ready', branch: `wf/${it.id}`, worktree: wt, head: lastCheck.head, startTier: it.tier, tier, escalated, workerRounds, reviewRounds, firstCheckPass, firstReviewPass, jevAtFirstReview, notes: lastVerdict.notes || '' }
    }
    if (reviewRounds >= MAX_REVIEW_ROUNDS) break
    feedback = `Reviewer blocking items:\n- ${(lastVerdict ? lastVerdict.blocking : ['reviewer returned nothing']).join('\n- ')}`
  }
  return { id: it.id, status: 'capped', branch: `wf/${it.id}`, worktree: wt, startTier: it.tier, tier, escalated, workerRounds, reviewRounds, firstCheckPass, firstReviewPass, jevAtFirstReview,
    lastCheckReasons: lastCheck ? lastCheck.reasons : [], lastBlocking: lastVerdict ? lastVerdict.blocking : [] }
})

// ---- Report (plain code) --------------------------------------------------
const done = results.filter(Boolean)
const byTier = {}
for (const r of done) {
  const t = r.startTier || 'unknown'
  byTier[t] = byTier[t] || { dispatched: 0, firstCheckPass: 0, escalated: 0, ready: 0 }
  byTier[t].dispatched++
  if (r.firstCheckPass) byTier[t].firstCheckPass++
  if (r.escalated) byTier[t].escalated++
  if (r.status === 'ready') byTier[t].ready++
}
const reviewed = done.filter((r) => r.firstReviewPass !== null && r.firstReviewPass !== undefined)
const metrics = {
  byTier,
  opusFirstRoundPassRate: reviewed.length ? reviewed.filter((r) => r.firstReviewPass).length / reviewed.length : null,
  ready: done.filter((r) => r.status === 'ready').length,
  capped: done.filter((r) => r.status === 'capped').length,
  lost: ready.length - done.length,
  // Shadow Jev pre-screen: would "every check >= 0.5" have agreed with Opus's first-round verdict?
  jevShadow: (() => {
    const scored = done.filter((r) => r.jevAtFirstReview && r.jevAtFirstReview.min !== null && r.firstReviewPass !== null)
    return { scored: scored.length, agreedWithOpus: scored.filter((r) => (r.jevAtFirstReview.min >= 0.5) === r.firstReviewPass).length }
  })(),
}
log(`Ready ${metrics.ready}, capped ${metrics.capped}, deferred ${deferred.length}, lost ${metrics.lost}`)
phase('Cleanup')
await pruneNetworks('Cleanup')
return {
  repo, baseRef, baseSha, porcelainAtStart: porcelain,
  worktrees: [baseWt, ...done.map((r) => r.worktree).filter(Boolean)],
  results: done, deferred, opusKeep: plan.opus_keep, metrics,
}
