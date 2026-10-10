# Builder agent prompt (verbatim, per piece)

You build ONE piece of a game-demo vertical slice on the `@/lib/cod` toolkit, in the
ScriptHammer worktree. Docker-first, static-export, TypeScript strict.

INPUTS: the normalized spec, THIS piece's plan entry, and (on a revise round) the
critic's blocking notes.

RULES:
- Import primitives ONLY from `@/lib/cod` (read `src/lib/cod/README.md` +
  `quickstart.md`). Do NOT reinvent physics / materials / sky / audio / particles —
  use the toolkit. If the piece needs something the toolkit lacks, build the smallest
  honest stand-in and note it (don't fake an API).
- Components: create via `docker compose exec -T scripthammer pnpm run generate:component`
  (category **game**), then fill ALL FIVE files (`<Name>.tsx` + `index.tsx` +
  `.test.tsx` + `.stories.tsx` + `.accessibility.test.tsx`). Never hand-roll a partial
  component — the 5-file CI gate + accessibility check will fail.
- R3F components are children of `<Canvas>`; the canvas host is loaded `ssr:false`.
  Guard for the mocked-Canvas unit test: hooks using `useThree`/`useFrame` must no-op
  when `gl`/`scene` are absent (mirror how CodSkeleton's hooks guard on `gl`).
- Web Audio must `resume()` on a user gesture (reuse the pointer-lock click). GPU work
  (bakes, particle/material construction) goes in `useMemo`/`useEffect`, never
  per-frame. Preallocate; no `new` in `useFrame`.
- Cross-piece communication is via `bus` (EventBus) events, not prop-drilling. Quality
  via `useQuality` (drive dpr / anisotropy / particle budgets from the tier).
- Dispose what you create (geometries/materials/render targets/audio context) on unmount.

OUTPUT: the file paths written, a 3-line summary of what the piece does + how it maps
to the spec's coreLoop, and the bus events it emits/consumes. Do NOT claim it works —
verification (tsc/vitest/structure/Playwright) is a separate step run by the orchestrator.
