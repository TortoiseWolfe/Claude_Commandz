# Scaffolding a demo — plop + the route

## Components (the 5-file gate — always via plop)

```
docker compose exec -T scripthammer pnpm run generate:component
```
Prompts: `name` (PascalCase) · `category` = **game** (→ Storybook `Features/Game/*`) ·
`hasProps` / `withHooks` as needed. Emits the 5 files under
`src/components/game/<Name>/` (`<Name>.tsx`, `index.tsx`, `.test.tsx`,
`.stories.tsx`, `.accessibility.test.tsx`). Fill them all — CI's "Validate Component
Structure" + `accessibility` checks require every file.

## Route (hand-authored; static-export-safe)

Create `src/app/game/<name>/page.tsx`, mirroring `src/app/game/cod-skeleton/page.tsx`:

```tsx
'use client';
import dynamic from 'next/dynamic';
import Loader from '@/components/game/Loader';
const Game = dynamic(() => import('@/components/game/<Name>'), {
  ssr: false,
  loading: () => <Loader />,
});
export default function Page() {
  return (
    <main className="…container…">
      <h1>…</h1>
      <nav aria-label="Breadcrumb">…back to /game…</nav>
      <div className="mx-auto max-w-7xl"><Game /></div>
    </main>
  );
}
```

## Canvas host + a11y carve-out (mirror CodSkeleton)

The top component probes WebGL (`isWebGLAvailable`) and renders `<FallbackPanel onRetry>`
when unavailable or on `webglcontextlost`; otherwise the `<Canvas>` (dpr from
`useQuality`, an `aria-label`). axe can't audit canvas, so the `.accessibility.test.tsx`
covers only the DOM chrome/fallback; add a Pa11y exclusion for the new route if the
repo uses `config/pa11yci.json`.

## Wiring

Compose the pieces as children of `<Canvas>`. Cross-boundary state (HUD ↔ scene) flows
through `bus` (EventBus) events; quality flows through `useQuality`. Keep the demo's
own event vocabulary defined in one place (a typed `EventBus<YourEvents>` or additions
to `GameEvents`).
