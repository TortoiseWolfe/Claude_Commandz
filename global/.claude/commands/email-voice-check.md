---
description: Screen a draft client email for AI-ish voice (em-dashes, tricolons, "furthermore", stacked appositives) before staging or sending
---

# Email Voice Check

Audit a draft client email against AI-voice tells before it goes into a Gmail draft or out the door. Prompted by Greg (Chattanooga.Digital) calling an update "a bit AI-ish" on 2026-07-24. This is the email-tuned sibling of `good_prompt_bad_prompt`'s `/voice-check`.

## Usage

```
/email-voice-check                     # paste the draft interactively
/email-voice-check "text to check"     # inline text
/email-voice-check path/to/draft.txt   # a file
```

For a Talk post or Deck comment, use the chat profile:

```bash
printf '%s' "$BODY" | python3 ~/.claude/scripts/email_voice_check.py --stdin --chat --strict
```

ARGUMENTS: $ARGUMENTS

---

## Instructions

### Step 1 — resolve the input
- Empty → ask for the draft body (paste).
- Quoted string → inline text.
- A path that resolves → read the file.
- Anything else → treat as inline text.

Use only the **body** (skip To/Cc/Subject headers and any quoted reply history).

### Step 2 — run the detector
```bash
printf '%s' "$BODY" | python3 ~/.claude/scripts/email_voice_check.py --stdin
```
Add `--strict` when it's your OWN outgoing email and you want the zero-tolerance gate (any signal = FAIL):
```bash
printf '%s' "$BODY" | python3 ~/.claude/scripts/email_voice_check.py --stdin --strict
# Greg or devops list: add --recipient greg --html body.html (the HTML you will stage) to gate the 17 Oct format
```
Capture: signal count, structural count, verdict (PASS / FLAG / MAJOR FLAG / FAIL), contraction ratio, mid-sentence dash count, and the per-match list.

For chat, `--chat` turns off email-only jargon glosses, acronym expansion, reply quoting, HTML format checks, the 350-word limit, and questions-to-the-reader. It keeps promises and open-work checks under `--strict`, and adds hard chat checks for messages over 60 words, machine-like data, glosses, and document-style formatting. A short numbered choice list of up to three lines is allowed. Label-colon openers and narration phrases are checked in every profile.

### Step 3 — report + fix
Show the verdict and every match. For each, apply a concrete rewrite. A **structural** hit (em-dash overuse, closing tricolon, stacked appositives, even contraction spread) means the prose reads AI even if the words look fine — fix the *structure*, not just the word.

| Tell (matched) | Rewrite |
|---|---|
| mid-sentence ` — ` / `–` (em-dash overuse) | replace with a comma, a period, or parentheses; most sentences need none |
| closing tricolon ("did X, did Y, and did Z") | split into two sentences or cut an item |
| stacked appositive ("X, the Y, …") | assume they know, or explain in a separate sentence |
| "utilizing" | "using" |
| "furthermore" / "moreover" | "also" / (drop) |
| "it is worth noting" / "I would recommend" | (drop — just say it) / "Try…" |
| "reach out" / "touch base" / "circle back" | "email" / "ask" / "call" |
| "I hope this email finds you well" / "please don't hesitate" | (drop) |
| "not just X but Y" | say the one thing that matters |
| expanded forms ("did not", "it is") | contract them ("didn't", "it's") |
| `[open_work]` "still open", "not yet", "next up", "we plan" (strict) | **HOLD, not a style fix.** It is unfinished work: do it first (a lane, or the top of the roadmap to-do list), then say what was done. Never trim the line or tell the client what wasn't done |
| `[question_to_reader]` any prose question (strict) | **HOLD.** Research and decide it ourselves. Keep it only if just the reader can know the answer, then rerun with `--only-they-know` |
| `[access_ask]` a sentence asking the reader for access: "add <address> as a Viewer", "invite me", "give me access", "grant/share access", "sign-in for", "admin access", "add me to", "send me the password" (strict hard FAIL; warns without `--strict`) | **HOLD until proven.** First read the client repo's access record (Harmonic Egg: `docs/access-inventory.md`), the call decisions in `~/.claude/state/lanes/*<client>*` and `~/meetings/<client>/`, and `python3 ~/repos/hub/scripts/hub-ask.py`, then try the access live (fleet browser, API). If we hold it, cut the ask. If the record and the live check both show we lack it, rerun with `--access-proof "<the record and the live check, in one sentence>"` (20+ characters) and name the same two things in report.md. With no proof the draft asks for nothing; the gap becomes a hub card (31 Oct rule) |

### Step 4 — verdict banner
- **✓ Send it.** PASS, 0 structural.
- **⚠️ Fix first.** FLAG/MAJOR — apply the rewrites and re-run until it reads human.

## What this does NOT check
Whether the claims are TRUE, or whether the content/scope is right — only whether it *sounds* human. Run it, fix the tells, then stage the draft (to a client: `python3 ~/.claude/scripts/gmail_api.py draft-create`; the connector's create_draft is refused for anyone but Jonathan). Never send without the user's explicit go.

## Related
- `~/.claude/scripts/email_voice_check.py` — the detector (stdlib-only; email-tuned fork of `good_prompt_bad_prompt/scripts/detect_llm.py`)
- memory `feedback_stage_emails_as_gmail_drafts` — the draft-staging workflow this gates
