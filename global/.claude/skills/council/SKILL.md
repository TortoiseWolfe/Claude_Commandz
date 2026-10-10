---
name: council
description: Convene a council of independent advisors to stress-test a decision, plan, architecture choice, or diff before committing to it. Spawns parallel subagents that reason in isolation, peer-review each other anonymously, then deliver one committed verdict with confidence, named risks, and recorded dissent. Use when the user says "council this", "convene the council", "stress-test this", "pressure-test this", "war room this", "red team this", "poke holes in this", "talk me out of this", or asks a real either/or with stakes — "should I X or Y", "which approach", "is this worth building", "am I overengineering this". Do NOT use for factual lookups, single-answer questions, or decisions with no genuine tradeoff.
---

# The Council

## Why this exists

A single model inherits the framing it is handed. Ask "should I do X?" and the answer bends
toward X, because X is what is in the context window and agreeableness is cheap. The council
fixes this structurally, not by asking one model to "be critical" — that produces a critique
shaped like agreement.

Three mechanisms do the work:

1. **Isolation.** Advisors reason in separate contexts. None sees another's answer, and none
   sees the user's enthusiasm.
2. **Anonymous peer review.** Advisors critique each other's work with authorship stripped.
   This is where most of the value comes from — an advisor who was polite in round one will
   name a flaw in round two, because there is no one to be polite to.
3. **A forced commitment.** The verdict must pick a side. Hedging is a failure condition.

**The council's job is not to help you feel better about a decision you have already made.**

---

## Phase 0 — Frame the question

Before spawning anything, restate the decision in neutral form. This phase is not optional
and it is not a formality — a leading frame poisons all four advisors at once.

Produce a **brief** containing:

- **The decision**, stated as a choice between named options (including "do nothing", which
  is always on the ballot whether or not the user listed it).
- **What is actually at stake** — time, money, reputation, reversibility. Say if it is cheap
  to undo; most decisions are, and that changes the whole analysis. **State the stakes without
  editorializing.** "Build time is scarce and this propagates to every fork" is not a neutral
  stake, it is the case against acting, smuggled into the brief. Write "solo maintainer;
  changes propagate to N forks" and let the advisors decide what that implies. Read the stakes
  section back and ask: does this read like an argument for one option? If yes, rewrite it.
- **Constraints and facts**, gathered from the repo, not assumed. Read `CLAUDE.md`,
  `README.md`, `package.json`, and any files the user named. If the decision touches code,
  read the code. An advisor arguing from imagination is worse than no advisor.
- **What the user believes**, quarantined in a section labeled `USER'S CURRENT LEAN` — and
  **stripped from the brief the advisors receive.** They get it only in Phase 3.

Strip advocacy language. "Should I finally rip out the legacy theme system, which is a mess?"
becomes "Option A: replace the theme system. Option B: keep it. Option C: partial migration."

**If the question is too vague to frame — no real options, no stakes, no way to be wrong —
say so and ask one clarifying question instead of convening.** A council on a non-question
produces expensive noise.

---

## Phase 1 — Convene

Spawn the advisors **in a single message with multiple Task tool calls** so they run in
parallel. Default panel size is 5. Use `subagent_type: general-purpose`.

Each advisor receives: the neutral brief, its own persona block from
`references/personas.md`, and the shared response contract below. Nothing else. No advisor
is told what the others are, and no advisor is told what the user wants.

### Shared response contract

Every advisor returns, in 200–350 words:

- **POSITION** — one sentence naming which option it backs. "It depends" is not a position;
  if it truly depends, name the variable it depends on and pick the branch you think is live.
- **REASONING** — the two or three load-bearing reasons. Not five weak ones.
- **STRONGEST OBJECTION TO MY OWN POSITION** — the best argument against what I just said.
- **FALSIFIER** — "I would change my mind if ___." Must be something observable. "If it
  turned out to be a bad idea" is not a falsifier; "if the test suite takes over 4 minutes
  after the change" is.
- **CONFIDENCE** — low / medium / high, plus what is driving the uncertainty.

Advisors that read code must cite `file:line`. **An advisor may return "I have no useful
angle on this" — that is a legitimate response and better than manufactured insight.**

### The panel

Default roster (full definitions in `references/personas.md`):

| Advisor | Asks |
|---|---|
| **The Adversary** | What is the strongest case that this is a mistake? |
| **The Maintainer** | Who pays for this in eighteen months, and what does it cost forks? |
| **The Shipper** | Is this even on the critical path, and what is the smallest version that ships? |
| **The Architect** | Where does this leak, couple, or calcify? |
| **The Evidence Officer** | Which claims here are measured, and which are vibes? |

Swap or add from the bench when the decision calls for it — **The Economist** (pricing,
margin, willingness to pay), **The User** (does anyone downstream notice or care), **The
Teacher** (does this make explicable content), **The Security Officer**. Cap the panel at 7;
past that, advisors repeat each other and the peer review turns mushy.

**Custom panels:** "convene a council of security experts" → build 3–5 specialists in that
domain using the same response contract. Keep The Adversary on every panel regardless.

### If the panel converges

When every advisor backs the same option on the first pass, **do not proceed to peer review** —
peer review among people who already agree produces polish, not scrutiny. Instead spawn one
more advisor with a single instruction: *make the best possible case for <the option nobody
picked>, and you may not concede in your POSITION line.*

This is not theater. Convergence usually means one of two things, and the forced steelman tells
you which: the case for the other option is genuinely thin (the steelman comes back weak, and
now you have evidence rather than an echo), or the brief leaked a lean and every advisor
inherited it (the steelman comes back strong, and Phase 0 needs rewriting before any of this
counts). Run peer review after the steelman lands, with it in the pool.

---

## Phase 2 — Anonymous peer review

Spawn one review task per advisor, again in parallel. Each receives every response **except
its own**, labeled `RESPONSE A / B / C / D` with all persona identity removed. Strip
tell-tale phrasing that would reveal which is which.

Each reviewer returns:

- **UNSUPPORTED CLAIMS** — quote any assertion presented as fact that is not backed by a
  citation, a measurement, or sound reasoning. This is the primary output. Ranking rewards
  eloquence; flagging rewards rigor.
- **STRONGEST POINT MADE BY ANOTHER** — quoted, with which response.
- **WHAT EVERY RESPONSE MISSED** — the shared blind spot, if there is one.
- **RANKING** — best to worst, one line of reasoning each.

Reviewers may not soften. A review that flags nothing must say explicitly: "I found no
unsupported claims," and that admission is itself reported in the verdict.

**Quick mode** (`council quick: ...`) skips this phase. It costs roughly half as much and is
worth noticeably less — say so when using it.

---

## Phase 3 — The verdict

You are the chair. De-anonymize, weigh, and **commit**.

Only now do you read `USER'S CURRENT LEAN`. Compare it to where the council landed and state
the gap plainly — if the council disagrees with the user, that is the single most valuable
sentence in the output and it goes near the top, not buried under pleasantries.

Use the structure in `references/verdict-template.md`. It must contain:

- **The call** — one option, named, in the first sentence.
- **Confidence** — a percentage, with the reason for the number. Do not write 85% because it
  sounds decisive; if the council split 3–2 you are not at 85%.
- **Why** — the reasoning that survived peer review.
- **What could make this wrong** — the single biggest risk, concretely, not a list of five.
- **Dissent** — the minority position **in its own words**, quoted, never paraphrased away.
  If one advisor held out, the reader gets to judge that argument themselves.
- **Next action** — the first concrete step, and the falsifier that tells you to stop.

### Banned in the verdict

These phrases mean the chair failed to do its job. Rewrite rather than ship them:

> "Both options have merit" · "It ultimately depends on your priorities" · "There is no
> right answer here" · "You know your situation best" · "Whichever aligns with your goals"

Every one of those is true and none of them is useful. The council was convened *because*
both options have merit.

**Report cost honestly.** Full mode runs roughly 100–200k tokens. If the decision is
reversible in an afternoon, say the council was overkill for it — that is useful feedback
about when to reach for this skill.

---

## Failure conditions

The council has failed, regardless of how polished the output looks, if:

- Every advisor agreed on the first pass **and no forced steelman was run.** Real disagreement
  is the product. Unanimity means the question was not a real question, or the framing leaked —
  run the steelman in Phase 1 and re-read Phase 0 before believing the consensus.
- The Adversary produced only objections the user could have written themselves.
- Peer review flagged nothing across the whole panel.
- The verdict hedges.
- No advisor read the code on a decision about code.

Say when this happens. A council that reports "we all agreed too easily, which is suspicious"
is more useful than one that dresses up weak agreement as a mandate.
