# Organizational Audit

Conduct a comprehensive review of terminal roles, responsibilities, and organizational effectiveness.

## Usage

```
/audit
/audit [date]
```

**Examples**:
```
/audit                    # Creates audit for today
/audit 2026-Q1            # Creates quarterly audit with custom name
```

## Purpose

The organizational audit is a structured "meet and greet" where each terminal role:
1. Introduces itself and its responsibilities
2. Assesses whether it has adequate context and tooling
3. Provides suggestions for improvement
4. Identifies gaps or missing roles

## Audit Template

Create `docs/interoffice/audits/YYYY-MM-DD-organizational-review.md`:

```markdown
# Organizational Audit: YYYY-MM-DD

**Conducted By**: [Initiator]
**Date**: YYYY-MM-DD
**Participants**: [List terminals that provided input]

## Executive Summary

[Fill after collecting all responses]

---

## Terminal Responses

### Council Members

#### CTO
**Role Understanding**: [How CTO understands its responsibilities]
**Context Assessment**: [Getting enough info? Too much? Too little?]
**Tooling Adequacy**: [Are skills/commands sufficient?]
**Key Dependencies**: [Who does CTO need to interact with?]
**Suggestions**: [What would improve effectiveness?]
**Missing Roles**: [Any organizational gaps identified?]
**Suggested Title**: [Better name for this role?]

#### Architect
[Same structure]

#### Security Lead
[Same structure]

#### Toolsmith
[Same structure]

#### DevOps
[Same structure]

#### Product Owner
[Same structure]

---

### Contributors

#### Coordinator
[Same structure]

#### Planner
[Same structure]

#### Generator 1/2/3
[Same structure - can consolidate if similar]

#### Viewer
[Same structure]

#### Reviewer
[Same structure]

#### Validator
[Same structure]

#### Inspector
[Same structure]

#### Author
[Same structure]

#### Tester
[Same structure]

#### Implementer
[Same structure]

#### Auditor
[Same structure]

---

## Findings

### Themes
[Common patterns across responses]

### Gaps Identified
[Missing roles or capabilities]

### Improvement Opportunities
[Consolidated suggestions]

---

## Action Items

| # | Finding | Proposed Action | Owner | RFC? |
|---|---------|-----------------|-------|------|
| 1 | [Finding] | [Action] | [Who] | [Y/N] |

---

## RFCs Created

| RFC | Title | Status |
|-----|-------|--------|
| RFC-XXX | [Title] | draft |
```

## Audit Questions (per terminal)

Ask each terminal these 7 questions:

1. **Role Understanding**
   > "How do you understand your responsibilities? What is your primary focus?"

2. **Context Assessment**
   > "When you run `/prep [role]`, do you get enough context? Too much? What's missing?"

3. **Tooling Adequacy**
   > "Are your assigned skills/commands sufficient? What tools would help?"

4. **Key Dependencies**
   > "Which other terminals do you need to interact with most? Are those handoffs smooth?"

5. **Suggestions**
   > "What would make you more effective? Any process improvements?"

6. **Missing Roles**
   > "Are there gaps in the organization? Roles we should add or consolidate?"

7. **Suggested Title**
   > "Do you have a better name for your role? What title better captures what you do?"

## Conducting the Audit

### Option A: Sequential (One Terminal at a Time)

1. Open each terminal
2. Run `/prep [role]`
3. Ask the 7 questions
4. Record responses in audit document

### Option B: Parallel (Using Interoffice System)

1. CTO creates audit document
2. CTO uses `/broadcast "Organizational Audit"` to announce
3. Each terminal sends responses via `/memo cto "Audit Response"`
4. CTO consolidates into audit document

### Option C: Council Discussion First

1. Run `/council "Organizational Audit"` for council input
2. Council members discuss and identify themes
3. Contributors send memos with their perspectives
4. Consolidate into audit document

## After the Audit

1. Review all responses
2. Identify common themes and gaps
3. Create RFCs for structural changes using `/rfc [title]`
4. Track action items to completion
5. Schedule next audit (recommended: quarterly)

## Cadence

**Recommended**: Quarterly organizational audits
- Q1: January
- Q2: April
- Q3: July
- Q4: October

## Related Skills

- `/council` - Start informal discussion
- `/rfc` - Create formal proposal
- `/broadcast` - Announce to all terminals
- `/memo` - Send message to manager

ARGUMENTS: $ARGUMENTS
