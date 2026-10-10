# Normalizing a game spec

Turn the user's one-line spec into this structured object. Fill gaps with sensible,
genre-appropriate defaults — but do NOT invent mechanics the user would object to
(offer those as follow-ups in the harvest instead).

```
{
  title:     string      // name the game (short); ask only if truly ambiguous
  genre:     string      // "top-down survival-automation", "first-person explorer", …
  camera:    'first-person' | 'top-down' | 'third-person' | 'orbit'
  coreLoop:  string      // the ONE loop the slice must nail, in a sentence
  mechanics: string[]    // 2-4 concrete verbs: place, harvest, defend, craft, dash…
  entities:  string[]    // things in the world: bot, plant, enemy, resource node…
  look:      string      // art direction in a phrase: dead-earth palette, neon night…
  tone:      string      // playful / grim / satirical / cozy / …
}
```

**Scope discipline.** This is a ~3-minute **vertical slice**, not a finished game.
Pick the smallest set of mechanics/entities that demonstrates the `coreLoop` + the
`tone`. Everything else is deferred to the harvest's "what a full build would add".

**Camera → toolkit fit.** `first-person` reuses the CodSkeleton controller pattern
directly. `top-down`/`third-person`/`orbit` still use `CharacterController` +
`StaticWorld` for collision, but the camera rig differs — the planner must call that
out so the builder adapts the camera (not the physics).
