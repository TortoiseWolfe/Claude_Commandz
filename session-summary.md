---
description: Generate a continuation prompt for the next session
---

# /session-summary

Generate a concise prompt to prime the next Claude Code session with context from this conversation.

## Output Format

```
## Session Continuation: [Brief Topic]

### What We Built/Changed
- [Bullet list of files created or modified]
- [Key features or fixes implemented]

### Key Decisions Made
- [Important design choices or principles established]

### Current State
- [What's working]
- [What's been tested]

### Next Steps
1. [Immediate next action]
2. [Follow-up tasks]

### Files to Reference
- `path/to/key/file.md`
- `path/to/another/file.py`
```

## Instructions

1. Review the conversation to identify:
   - Files created, modified, or discussed
   - Problems solved and how
   - Design decisions and principles established
   - What was tested vs what still needs testing

2. Keep it scannable - bullet points over paragraphs

3. Focus on WHAT and WHERE, not detailed HOW (the files contain that)

4. Include file paths so the next session can read them directly

5. End with clear next steps so the session can start immediately

DO NOT include:
- Git commit details (commits are in history)
- Lengthy code snippets (read from files instead)
- Conversation back-and-forth (just conclusions)
