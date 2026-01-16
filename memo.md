# Send Memo to Manager

Send a message to a manager terminal via the interoffice communication system.

## Usage

```
/memo [to] [subject]
```

**Examples**:
```
/memo architect "Toggle validation issue"
/memo toolsmith "Skill file bug report"
/memo cto "Strategic concern about timeline"
```

## Arguments

- `to`: Target manager (cto, architect, security-lead, toolsmith, devops, coordinator)
- `subject`: Brief subject line (in quotes if multi-word)

If arguments not provided, prompt for them.

## Valid Recipients

| Recipient | File |
|-----------|------|
| cto | `docs/interoffice/memos/to-cto.md` |
| architect | `docs/interoffice/memos/to-architect.md` |
| security-lead | `docs/interoffice/memos/to-security-lead.md` |
| toolsmith | `docs/interoffice/memos/to-toolsmith.md` |
| devops | `docs/interoffice/memos/to-devops.md` |
| coordinator | `docs/interoffice/memos/to-coordinator.md` |

## Instructions

1. **Parse arguments**: Extract recipient and subject from ARGUMENTS
2. **Validate recipient**: Must be one of the valid recipients above
3. **Determine sender**: Ask which terminal is sending (or infer from context)
4. **Get priority**: Ask for priority level (normal, urgent, fyi)
5. **Get message body**: Ask for the full message content
6. **Get action requested**: Ask if any specific action is needed (optional)

## Memo Format

Insert this block at the TOP of the memo file (after the header comment, before any existing memos):

```markdown
---

## YYYY-MM-DD HH:MM - From: [Sender Terminal]
**Priority**: [normal|urgent|fyi]
**Re**: [Subject]

[Message body]

**Action Requested**: [Action or "None"]
```

## Implementation

1. Read the target memo file
2. Find the position after `<!-- Newest first...` comment
3. Insert the new memo entry
4. Write the updated file

## Confirmation

After creating the memo, output:

```
Memo sent to [recipient].

To: [Recipient]
From: [Sender]
Subject: [Subject]
Priority: [Priority]

[recipient] will see this memo on their next /prep.
```

## Routing Reference

If the sender is unsure who to contact, suggest based on topic:

| Topic | Route To |
|-------|----------|
| Architecture, patterns, dependencies | architect |
| Security, auth, OWASP | security-lead |
| Skills, commands, tooling | toolsmith |
| CI/CD, Docker, deployment | devops |
| Strategic, priorities, risk | cto |
| Task queue, workflow | coordinator |

ARGUMENTS: $ARGUMENTS
