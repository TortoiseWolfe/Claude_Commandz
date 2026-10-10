# Verdict template

Output as markdown in the conversation. Do not write it to a file unless asked — a verdict is
read once, at the moment of deciding.

Length target: **under 500 words.** The council did a lot of work; the reader does not need
all of it. Compression is part of the deliverable.

---

```
## Verdict: <the option, named>

**Confidence: <N>%** — <why that number, in one clause. A 3–2 split is not 85%.>

<One paragraph. The reasoning that survived peer review, in plain language. Lead with the
reason that did the most work. If the council disagrees with what the user was leaning
toward, that goes in the first two sentences — not buried.>

**What would make this wrong**
<The single biggest risk, stated concretely enough to check. One item, not five. Five risks
is a way of avoiding the responsibility of ranking them.>

**Dissent — <Advisor name>**
> <Quoted verbatim, 1–3 sentences. Never paraphrased into agreement. If the panel was
> unanimous, replace this section with a flag: "Unanimous — treat with suspicion, see below.">

**Do this next**
1. <First concrete action. Something that can start today.>
2. <The check that tells you to continue or stop, with the threshold that decides it.>

---
<Panel table>
```

## Panel table

Six columns. Keep it to one line per advisor — this is a reference, not a transcript.

| Advisor | Position | Confidence | Peer rank | Flagged for unsupported claims |
|---|---|---|---|---|

Add a final row only if something notable happened: an advisor abstaining, an advisor changing
position, or the peer review flagging nothing at all.

## Transcript

Offer it; do not print it. "Full transcript available — say the word." Most of the time the
verdict is what gets used, and pasting 2,000 words of advisor output buries the one paragraph
that matters.

## Health check

Append **only when something is off** — a clean run needs no commentary:

- **Unanimous panel** — "All five agreed on the first pass. That usually means the question
  was not really open, or the framing leaked. Worth re-reading the brief before trusting this."
- **Nothing flagged in peer review** — "No advisor flagged an unsupported claim, which is
  either unusually rigorous work or a soft review. Weight accordingly."
- **Cheap to reverse** — "This is undoable in an afternoon. A council was more machinery than
  the decision needed — next time, just try it."
- **Blocked on evidence** — "The Evidence Officer is right that this cannot be answered without
  <measurement>. The verdict above is the best available guess; the measurement would beat it."
