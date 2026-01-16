# Create RFC (Request for Comments)

Create a new RFC for council debate and consensus voting.

**Access**: Council members only (CTO, Architect, Security Lead, Toolsmith, DevOps)

## Usage

```
/rfc [title]
```

**Examples**:
```
/rfc "Payment Provider Selection"
/rfc "Wireframe Validation Strategy"
/rfc "Test Coverage Requirements"
```

## Instructions

### 1. Verify Council Membership

This skill is for council members only. If the terminal is not one of:
- CTO
- Architect
- Security Lead
- Toolsmith
- DevOps

Output error:
> "Error: Only council members can create RFCs. Use /memo to send a request to your manager."

### 2. Determine Next RFC Number

```bash
# Find highest RFC number
ls docs/interoffice/rfcs/RFC-*.md 2>/dev/null | sed 's/.*RFC-\([0-9]*\).*/\1/' | sort -n | tail -1
```
If no RFCs exist, start at 001. Otherwise, increment by 1.

### 3. Gather RFC Details

Ask the author for:
1. **Summary**: One paragraph describing the proposal
2. **Motivation**: Why is this change needed?
3. **Proposal**: Detailed description of the proposed solution
4. **Alternatives**: What other approaches were considered? (at least 2)
5. **Impact Assessment**: How does this affect codebase, workflow, documentation?
6. **Target Decision Date**: When should voting conclude?

### 4. Create RFC File

Create `docs/interoffice/rfcs/RFC-XXX-[slug].md` with this template:

```markdown
# RFC-XXX: [Title]

**Status**: draft
**Author**: [Author Terminal]
**Created**: [Today's Date]
**Target Decision**: [Target Date]

## Stakeholders (Consensus Required)

| Stakeholder | Vote | Date |
|-------------|------|------|
| CTO | pending | - |
| Architect | pending | - |
| Security Lead | pending | - |
| Toolsmith | pending | - |
| DevOps | pending | - |

**Votes**: approve | reject | abstain
**Required for Decision**: All non-abstaining stakeholders must approve

## Summary

[Summary from author]

## Motivation

[Motivation from author]

## Proposal

[Proposal from author]

## Alternatives Considered

### Alternative A: [Name]
[Description]

### Alternative B: [Name]
[Description]

## Impact Assessment

| Area | Impact | Mitigation |
|------|--------|------------|
| Codebase | [Impact] | [Mitigation] |
| Workflow | [Impact] | [Mitigation] |
| Documentation | [Impact] | [Mitigation] |

## Discussion Thread

### [Author] [Date] - Initial Proposal

[Opening remarks from author]

## Dissent Log

| Stakeholder | Objection | Response |
|-------------|-----------|----------|
| - | - | - |

## Decision Record

**Decided**: -
**Outcome**: -
**Decision ID**: -
```

### 5. Output Confirmation

```
RFC-XXX created: [Title]

Status: draft
Author: [Author]
Target Decision: [Date]

Next steps:
1. Review and refine the RFC
2. Run /rfc-propose XXX to submit for council review
3. Council members will be notified on their next /prep
```

## State Transitions

- **draft**: Author is still working on it (not visible to others)
- Use `/rfc-propose XXX` to move to **proposed** state

ARGUMENTS: $ARGUMENTS
