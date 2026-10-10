---
name: game-demo
description: "Use to scaffold a playable 3D game demo inside a ScriptHammer app from a short spec (genre / theme / core mechanic / look). Runs the gauntlet loop: fan out builder subagents — each building on the @/lib/cod procedural game toolkit — paired with blind critics, plop-scaffold the 5-file components + an ssr:false route, verify in-container, and loop until every critic is wowed. Requires the CoD game-toolkit (feature 051)."
---

# /game-demo

Turn a one-line game spec into a **playable vertical slice** running at a new
`/game/<name>` route in a ScriptHammer app — built on the harvested, asset-free
`@/lib/cod` procedural toolkit (physics, materials, sky+IBL, audio, particles,
camera-feel, quality tiers, event bus). Zero art/audio assets.

The engine is Matt Shumer's **gauntlet loop**: *Task → Build method (fan out
subagents, each with a blind critic) → Bar (don't stop until every critic is
wowed)*. Here the "task" is a game piece, the "build method" is a builder subagent
writing against the toolkit, and the "bar" is a critic scoring it against the spec.

## Usage

```
/game-demo "<spec>"                       # e.g. "top-down survival-automation on a dead-earth farm, darkly satirical"
/game-demo "<spec>" --name <PascalName>   # component/route base name (default: derived from the spec)
/game-demo "<spec>" --rounds <N>          # max builder↔critic rounds per piece (default 2)
/game-demo "<spec>" --dry-run             # plan + design only; no scaffolding or files
```

## Prerequisites (check first, fail loudly)

- **The toolkit exists.** `src/lib/cod/index.ts` (the `@/lib/cod` barrel) + `src/lib/cod/README.md` must be present (feature 051). If not, stop and tell the user to land the toolkit PR first.
- **Docker-first.** Every `pnpm` / `plop` / test command runs in the container: `docker compose exec -T scripthammer pnpm …`. Never host `pnpm`/`npm`/`sudo`.
- **Static export.** The demo is a `'use client'` `page.tsx` that `dynamic(() => import(component), { ssr:false, loading:<Loader/> })`. No `src/app/api/`, no server routes, browser env only `NEXT_PUBLIC_*`.
- Read `src/lib/cod/README.md` + `features/enhancements/051-cod-game-toolkit/quickstart.md` so builders target the real API.

## Steps

Run these in order. Announce each phase. Use the prompts in `references/` verbatim
for subagents (they are the contract).

0. **Preflight.** Confirm the prerequisites above. Read the toolkit README + quickstart. Resolve the base `--name` (PascalCase; derive from the spec if absent). Confirm the dev container is up (`docker compose ps`).

1. **Normalize the spec** → a structured object per `references/game-spec.md`: `{ title, genre, coreLoop, mechanics[], entities[], look, tone, camera }`. Keep it small — this is a ~3-minute vertical slice, not a finished game.

2. **Plan (one agent).** Dispatch one planner agent with `references/planner.md` → a **build-list** of 3–6 independent PIECES (e.g. level/world, player mechanics, one entity/system, HUD/feel), each mapped to specific `@/lib/cod` primitives and the component(s)/route it needs. The planner also picks the single "core loop" the slice must nail.

3. **Gauntlet (fan out builders + blind critics).** For each piece, run a **builder ↔ critic loop** until the critic passes or `--rounds` is hit:
   - **builder** (`references/builder.md`) — writes the piece's code against `@/lib/cod`; scaffolds any component via `plop` (Step 4); returns a diff summary + how it maps to the spec.
   - **critic** (`references/critic.md`) — a **blind** reviewer (given only the spec + the piece's output, not the builder's rationale) scores it against the spec + a quality bar and returns `pass|revise` + specifics. On `revise`, feed the critique back to the builder.
   - **Orchestrate deterministically.** Prefer a **Workflow** (`pipeline` over the pieces: build → critique, looping per piece) so pieces advance independently and the loop-until-pass is explicit — see the template in `references/gauntlet-workflow.md`. Fall back to parallel `Agent` calls (one message) if Workflow is unavailable. Cap total rounds; on a cap-hit, keep the best attempt and **log that it was capped** (never silently ship a failed critic).

4. **Scaffold + assemble.** Per `references/scaffold.md`: create components via `docker compose exec -T scripthammer pnpm run generate:component` (category **game** → `Features/Game/*`, all 5 files — never hand-roll partial components); hand-author the `ssr:false` `page.tsx` route (mirror `src/app/game/cod-skeleton/page.tsx`); wire pieces together via the toolkit's `bus` (events across the `<Canvas>` boundary) and `useQuality` (tiers). Keep the WebGL-probe + `<FallbackPanel>` a11y carve-out.

5. **Verify (green gate — the real bar).** In-container: `pnpm exec tsc --noEmit` (0 errors in new files), `pnpm exec vitest run <new paths>` (all green), `node scripts/validate-structure.js` (no new violations), and a **Playwright smoke** of `/game/<name>` (route 200 → follow the trailing-slash redirect; canvas renders; console clean of shader/runtime errors). Any red → feed back into Step 3. The demo is not "playable" until this is green.

6. **Harvest.** Report: the new route + components, the core loop, what each critic passed on, what was cut/capped, and the exact commands to run it live. Do NOT commit unless asked.

## Constraints (non-negotiable — the generated demo must honor these)

- **Docker-first**; **static export** (`ssr:false`, no server routes); **5-file component CI gate** (always via plop); **canvas-a11y carve-out** (`FallbackPanel` + WebGL probe; axe covers only DOM chrome; Pa11y exclusion + a manual-review note); **MIT attribution** intact for anything derived from `@/lib/cod`.
- **No Supabase** unless the spec explicitly asks for save/share (and then only that slice).

## Honesty rules

- **No silent caps.** If a piece was cut, a round-cap was hit, or a critic was overridden, say so in the harvest.
- **Vertical slice, not a game.** Always frame the output as a playable slice, and say what a full build would add.
- **Verify before "playable".** Never claim it works until tsc + vitest + validate:structure + the Playwright smoke are green; report the actual results.
- **Real spec only.** Build from the user's spec; don't invent mechanics they didn't ask for (offer them as follow-ups instead).
