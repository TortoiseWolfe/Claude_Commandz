# Start Council Discussion

Start an informal discussion thread among council members.

**Access**: Council members only (CTO, Architect, Security Lead, Toolsmith, DevOps)

## Usage

```
/council [topic]
```

**Examples**:
```
/council "Wireframe tooling strategy"
/council "Test coverage standards"
/council "Payment provider evaluation"
```

## Purpose

Council discussions are for informal deliberation before creating a formal RFC. Use this when:
- You want to gauge council sentiment on an idea
- Multiple approaches need exploration before formalizing
- A quick decision doesn't require full RFC process
- You want to build consensus before proposing

## Instructions

### 1. Verify Council Membership

This skill is for council members only. If the terminal is not one of:
- CTO
- Architect
- Security Lead
- Toolsmith
- DevOps

Output error:
> "Error: Only council members can start council discussions. Use /memo to send a request to your manager."

### 2. Identify Initiator

Ask which council member is starting the discussion (or infer from context).

### 3. Get Opening Thought

Ask the initiator for their opening comment/question to kick off the discussion.

### 4. Create Discussion File

Create `docs/interoffice/council/YYYY-MM-DD-[slug].md`:

```markdown
# Council Discussion: [Topic]

**Started**: [Today's Date]
**Initiated By**: [Initiator]
**Participants**: [Initiator]
**Status**: active

## Topic

[Topic description]

## Thread

### [[Initiator]] [Today's Date] [Time]

[Opening thought from initiator]

## Resolution

<!-- Fill in when status changes to "resolved" or "escalated-to-rfc" -->
```

### 5. Output Confirmation

```
Council discussion started: [Topic]

File: docs/interoffice/council/[filename]
Status: active
Initiated by: [Initiator]

Other council members can add comments by editing the Discussion Thread section.

To conclude:
- Update Status to "resolved" and fill Resolution section
- Or escalate to /rfc if formal decision needed
```

## Adding to Discussion

Council members can contribute by:
1. Reading the discussion file
2. Adding a new entry in the Thread section:

```markdown
### [[Member]] YYYY-MM-DD HH:MM

[Your comment]
```

3. Adding their name to the Participants list

## Resolving Discussion

When the discussion reaches a conclusion:

**If resolved informally**:
```markdown
**Status**: resolved

## Resolution

[Summary of what was decided/agreed]
```

**If escalation needed**:
```markdown
**Status**: escalated-to-rfc

## Resolution

Discussion escalated to RFC-XXX for formal vote.
```

Then run `/rfc [topic]` to create the formal RFC.

ARGUMENTS: $ARGUMENTS
