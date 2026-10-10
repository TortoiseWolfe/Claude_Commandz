# Gauntlet-loop Workflow template

When the Workflow tool is available, drive the per-piece gauntlet as a `pipeline` so
pieces advance independently and the build↔critique loop is explicit. The MAIN loop
still owns plop + the container verification (Steps 4–5) — the workflow only runs the
build↔critique gauntlet.

```js
export const meta = {
  name: 'game-demo-gauntlet',
  description: 'Build each game piece on @/lib/cod, gated by a blind critic',
  phases: [{ title: 'Build' }, { title: 'Critique' }],
};

const pieces = args.pieces;            // from the planner (Step 2)
const rounds = args.rounds ?? 2;

const VERDICT = {
  type: 'object', additionalProperties: false,
  properties: {
    verdict: { type: 'string', enum: ['pass', 'revise'] },
    score: { type: 'number' },
    blocking: { type: 'array', items: { type: 'string' } },
    strongest: { type: 'string' },
  },
  required: ['verdict', 'score', 'blocking'],
};

const results = await pipeline(pieces, async (piece) => {
  let out = null, verdict = null, round = 0;
  do {
    out = await agent(BUILDER_PROMPT(piece, verdict), {
      label: `build:${piece.id}`, phase: 'Build',
    });
    verdict = await agent(CRITIC_PROMPT(piece, out), {
      label: `critique:${piece.id}`, phase: 'Critique', schema: VERDICT, effort: 'high',
    });
    round++;
  } while (verdict && verdict.verdict === 'revise' && round < rounds);
  return { piece, out, verdict, capped: !!verdict && verdict.verdict === 'revise' };
});
return results;
```

Notes:
- `BUILDER_PROMPT`/`CRITIC_PROMPT` are the verbatim `references/builder.md` +
  `references/critic.md` bodies, interpolated with the piece (+ the last critique on a
  revise round). The critic is **blind** — pass it only the spec, the plan entry, and
  the builder's OUTPUT, never the builder's reasoning.
- Builders WRITE files in the worktree; within a piece the loop is serial (build →
  critique → rebuild). Different pieces run in parallel via `pipeline` (no barrier).
- Cap rounds; a piece still `revise` at the cap returns `capped: true` — surface it in
  the harvest (never silently ship a failed critic).
- After the workflow returns, the MAIN loop assembles the route (`references/scaffold.md`)
  and runs the green gate (Step 5). Keep plop + the container checks in the main loop so
  you stay in control of them.
