# Clean Transcript for GPT Context

Clean the specified transcript file(s) by removing filler content while preserving actionable, educational material suitable for context engineering a custom GPT.

## Input
$ARGUMENTS

If no file specified, ask which transcript file(s) to clean.

## What to REMOVE

### Conversational Filler
- Pre-session waiting/greetings ("Hey, how's it going?", waiting for people to join)
- Technical difficulties ("My internet is laggy", "Can you hear me?", "Let me share my screen")
- Pure small talk (weather, pets, family chat, personal life tangents)
- Pleasantries ("You're so kind", "Thank you so much everyone")
- Meta-commentary ("I'm going to share my screen now", "Can everyone see this?")

### Verbal Filler
- Remove all: "um", "uh", "you know", "like" (as filler), "right?", "okay so"
- Incomplete/abandoned sentences that trail off
- Repeated false starts

### Off-Topic Content
- Personal job situations not teaching the main subject
- Tangential Q&A unrelated to the core topic
- Inside jokes or references that don't add educational value

## What to KEEP

### Core Educational Content
- Strategic frameworks and methodologies (named systems, step-by-step processes)
- Specific actionable advice and best practices
- Examples, templates, and sample scripts
- Statistics and data points
- Tool recommendations and how-to instructions
- Relevant Q&A that clarifies the topic for all learners

### Structure Elements
- Course/topic context (week number, topic name)
- Section transitions that help organize content
- Summary points and key takeaways

## What to FLAG
Mark ambiguous content with `[REVIEW: reason]` for human review:
- Personal anecdotes that might illustrate a teaching point
- Motivational content that could provide valuable context
- Q&A where relevance is unclear
- Tangents that might circle back to strategy

## Critical: Preserve URL and timestamps (non-negotiable)

Every cleaned transcript **must** carry source traceability from the raw file through to the cleaned markdown. Never strip these:

1. **Source URL:** the raw transcript's line 2 should be `Source: https://youtu.be/<VIDEO_ID>`. The cleaned output **must** keep this as line 2 (right under the H1 title). If the raw file's line 2 contains a URL in any other format (e.g., `https://youtu.be/...`), normalize it to `Source: https://youtu.be/...`.

2. **Timestamp anchors:** the raw transcript may contain inline timestamps like `[0:15:32]` before dialogue chunks. When you create a cleaned `##` section header, find the timestamp of the first piece of dialogue that section summarizes, and prepend a clickable YouTube deep-link anchor to the header:

   ```markdown
   ## [0:15:32](https://youtu.be/VIDEOID?t=932) Section Title
   ```

   The `?t=` value is the timestamp expressed in total seconds (0:15:32 → 932). Convert H:MM:SS correctly. If the raw input has no timestamps, skip the anchor for that section and note `[REVIEW: no timestamp available]` at the end of the cleaned file's front matter.

3. **Never drop either one during cleaning.** They are load-bearing for downstream Custom GPTs and Claude Projects that cite sources with clickable deep-links. See `memory/feedback_transcript_workflow.md` for the reasoning.

## Output Format

```markdown
# Title of Session (Week/Topic if applicable)
Source: https://youtu.be/VIDEOID

---

## [0:00:15](https://youtu.be/VIDEOID?t=15) Section Header

- Bullet points for lists
- **Bold** for emphasis on key terms
- Keep paragraphs short (2-3 sentences max)

### Subsection for detailed breakdowns

---

## [0:08:42](https://youtu.be/VIDEOID?t=522) Next Major Section
```

## Process
1. Read the entire transcript first
2. Extract the Source URL from raw line 2 (or wherever it lives — normalize to `Source: https://youtu.be/...`)
3. Identify the core educational structure and topics
4. Remove clear filler content
5. Clean verbal filler from remaining content
6. Reorganize into logical sections with markdown headers
7. For each section, find the first timestamp in the raw content it covers and compute the `?t=SECONDS` anchor
8. Prepend `[H:MM:SS](url?t=N)` to each section header
9. Flag any ambiguous sections with `[REVIEW: reason]`
10. Add section breaks (---) between major topics
11. Write the cleaned version with `Source:` as line 2

Report the approximate reduction percentage when complete, and confirm that URL and at least one timestamp anchor are present.
