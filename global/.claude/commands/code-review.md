---
description: Perform a comprehensive code review covering security, performance, code quality, and test coverage
---

Execute a systematic code review. FIX issues directly rather than documenting them.

## Output Constraints
- Bullet points only, no prose explanations
- One summary table at end, not per-phase
- Skip "next steps" unless errors block progress
- No redundant confirmations

## Step 0: Free panel (shadow until the scoreboard says READY)

Scripts live in `~/.claude/scripts/`. Never print the panel's config or keys.

1. Resolve the class: `echo '{"repos":["<repo name>"]}' | python3 ~/.claude/scripts/class_resolver.py` and read `class`.
2. `client`: skip the panel entirely and go to "First" below.
3. `own` or `public`: save the diff to a temp file, then start the panel in the background (Bash `run_in_background`):
   `python3 ~/.claude/scripts/panel_review.py --class <class> --repo <repo> --diff <diff file> --spec <spec file> --acceptance <acc file> > <panel out file> 2>&1`
   - spec file = the PR title and body, or the text "review this change"
   - acceptance file = the text "no bugs, no security issues"
4. When it finishes, run `python3 ~/.claude/scripts/panel_scoreboard.py should-skip-claude --panel-json <panel out file> --class <class> --ref <PR number or HEAD sha>`.
   - `yes`: report the panel's verdict and blocking findings as the review, say plainly it was the free panel, and STOP. (Live mode; this only turns on once `panel_scoreboard.py status` says READY.)
   - `no`: carry on with the normal review below; the panel result is only recorded at the end.

**First**: Identify test runner and package manager from package.json.

## Phase 1: Security Audit

Use Explore agents to scan for:

1. **Encryption patterns**
   - Search: `crypto.subtle`, `ECDH`, `AES-GCM`, `generateKey`, `bcrypt`, `argon2`
   - Verify: Keys are non-extractable where possible, IVs/salts are random
   - Check: Private keys/secrets never persisted to client storage

2. **XSS vulnerabilities**
   - Search: `dangerouslySetInnerHTML`, `innerHTML`, `v-html`, user content rendering
   - Verify: Sanitization library used (DOMPurify, sanitize-html, etc.)
   - Check: No raw string interpolation in HTML contexts

3. **SQL/NoSQL injection**
   - Search: Raw queries, string concatenation in queries
   - Verify: Parameterized queries or ORM used
   - Check: User input never directly in query strings

4. **Authentication & authorization**
   - Search: `getUser`, `getSession`, `req.user`, auth middleware
   - Verify: All sensitive routes check authentication
   - Check: CSRF protection on state-changing operations

5. **Rate limiting**
   - Search: `rate_limit`, `RateLimiter`, `express-rate-limit`, failed attempts
   - Verify: Login, password reset, API endpoints protected
   - Check: Both client-side and server-side limits exist

6. **Secrets & environment**
   - Search: API keys, tokens, credentials, passwords in code
   - Verify: All secrets in environment variables
   - Check: No secrets in client-side code or public configs

## Phase 2: Performance Analysis

1. **Large components without memoization** (React/Vue)
   - Find components >300 LOC without React.memo/useMemo/computed
   - FIX: Add memoization where appropriate

2. **Expensive computations**
   - Search: Crypto operations, large data transforms, regex on user input
   - Check: Results cached appropriately
   - FIX: Add caching/memoization where missing

3. **Polling vs realtime**
   - Search: `setInterval`, `setTimeout` loops, polling patterns
   - Check: Could use WebSockets/SSE/realtime subscriptions instead
   - FIX: Convert to realtime where appropriate

4. **Duplicate event listeners**
   - Search: `addEventListener`, `on(`, event subscriptions
   - Check: Multiple components listening to same global events
   - FIX: Consolidate to single listener with event bus/context

5. **Query patterns**
   - Search: N+1 queries, repeated fetches, missing pagination
   - Check: Data fetched efficiently
   - FIX: Batch queries, add caching, implement pagination

## Phase 3: Code Quality

1. **Duplicate files**
   - Find files with similar names/content
   - FIX: Delete duplicates, keep canonical version

2. **Linter disables**
   - Search: `eslint-disable`, `@ts-ignore`, `@ts-expect-error`, `# noqa`, `// nolint`
   - Review: Each disable for legitimacy
   - FIX: Remove unnecessary disables, fix underlying issues

3. **TODO comments**
   - Search: `TODO`, `FIXME`, `HACK`, `XXX`, `BUG`
   - Categorize: Quick fix vs needs issue
   - FIX: Resolve quick fixes, create issues for complex ones

4. **Unimplemented stubs**
   - Search: `throw new Error('Not implemented')`, `pass`, `...`, empty bodies
   - FIX: Implement or remove dead code

5. **Dead code**
   - Search: Unused exports, unreachable code, commented-out code
   - FIX: Delete immediately

## Phase 4: Test Coverage

1. **Untested modules**
   - List all files in services/, lib/, utils/, core/
   - Check: Corresponding test file exists
   - FIX: Create tests for critical untested code

2. **Excluded/skipped tests**
   - Check test config for excluded patterns
   - Search: `it.skip`, `describe.skip`, `test.skip`, `@pytest.mark.skip`
   - Investigate: Why excluded/skipped?
   - FIX: Enable or delete with explanation

3. **Test quality**
   - Run the project's test suite
   - Verify: All tests pass
   - Report: Total test count and any failures

## Phase 5: Documentation

1. **Update CODE-REVIEW-ISSUES.md** - Track findings and fixes
2. **Update security docs** (if security findings)
3. **Create GitHub issues** (optional, ask user first) - one per finding, no duplicates

## Output Format

End with ONE summary table:

| Category | Found | Fixed | Remaining |
|----------|-------|-------|-----------|
| Security | X | Y | Z |
| Performance | X | Y | Z |
| Code Quality | X | Y | Z |
| Test Coverage | X | Y | Z |

**CRITICAL**: Fix issues directly. Do not defer work to "later" or create issues as a way to avoid fixing things.

## Last step: Score the panel

If the panel ran (own or public) and you did the Claude review above, record the pair:
`python3 ~/.claude/scripts/panel_scoreboard.py add --repo <repo> --ref <PR number or HEAD sha> --class <class> --panel-json <panel out file> --claude-verdict <pass|revise> --claude-blocking <count of blocking findings left or fixed> [--note "<why they differ>"]`
`revise` means you found at least one blocking issue. Skip this step for client class or if the panel produced no output.
