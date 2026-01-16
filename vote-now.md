---
description: Quick RFC voting with automatic state transitions and consensus detection
---

# Vote Now - Expedited RFC Voting

Cast votes on pending RFCs with automatic state management and decision record generation.

**Access**: Council members only (CTO, Architect, Security Lead, Toolsmith, DevOps, Product Owner, UX Designer)

## Usage

```
/vote-now                              # Show pending RFCs awaiting your vote
/vote-now [number] [vote]              # Vote on specific RFC
/vote-now [number] reject "[reason]"   # Reject with required reason
```

**Examples**:
```
/vote-now                              # "You have 2 RFCs pending"
/vote-now 001 approve                  # Vote approve on RFC-001
/vote-now 002 reject "Missing security impact analysis"
/vote-now 001 abstain
```

## Arguments

ARGUMENTS: $ARGUMENTS

- No args: Display pending RFCs for current council member
- `number`: RFC number (001, 002, etc.)
- `vote`: `approve` | `reject` | `abstain`
- `reason`: Required for reject votes (in quotes)

---

## Instructions

### 1. Verify Council Membership

Council members only. Valid voters:
- CTO
- Architect
- Security Lead
- Toolsmith
- DevOps
- Product Owner
- UX Designer

If terminal is not a council member:
```
┌─────────────────────────────────────────────────┐
│ ERROR: Council Members Only                     │
├─────────────────────────────────────────────────┤
│ /vote-now is restricted to council members.     │
│ Use /memo to send feedback to your manager.     │
└─────────────────────────────────────────────────┘
```

### 2. Identify Voter

Determine which council member is voting:
- Infer from terminal primer context ("You are the Architect terminal")
- If ambiguous, ask: "Which council member are you voting as?"

### 3. Parse Arguments

**If no arguments**: Go to Step 4 (Show Pending)
**If arguments provided**: Go to Step 5 (Record Vote)

---

### 4. Show Pending RFCs (No Arguments)

Use the script to list pending RFCs:

```bash
python3 scripts/rfc-consensus.py pending
```

For JSON output (to parse voter-specific pending):
```bash
python3 scripts/rfc-consensus.py pending --json
```

**Output format**:
```
┌─────────────────────────────────────────────────┐
│ Pending Votes for [Voter]                       │
├─────────────────────────────────────────────────┤
│ RFC-001: Add QA Lead Role                       │
│   Status: proposed | Your vote: pending         │
│   Votes: 1✓ 0✗ 0○ 5?                            │
│                                                 │
│ RFC-002: Add Technical Writer Role              │
│   Status: proposed | Your vote: pending         │
│   Votes: 1✓ 0✗ 0○ 5?                            │
├─────────────────────────────────────────────────┤
│ Quick vote: /vote-now 001 approve               │
└─────────────────────────────────────────────────┘
```

If no pending RFCs:
```
┌─────────────────────────────────────────────────┐
│ No Pending Votes                                │
├─────────────────────────────────────────────────┤
│ All RFCs are either decided or you have voted.  │
└─────────────────────────────────────────────────┘
```

---

### 5. Record Vote (With Arguments)

#### 5.1 Parse Vote Arguments

Extract from ARGUMENTS:
- `rfc_number`: First arg (e.g., "001")
- `vote_type`: Second arg ("approve", "reject", "abstain")
- `reason`: Third arg if rejecting (required for reject)

Validate:
- If `vote_type` not in [approve, reject, abstain]: Error "Vote must be: approve, reject, or abstain"
- If `vote_type` is `reject` and no reason: Error "Reject votes require a reason in quotes."

#### 5.2 Cast Vote Using Script

Use `rfc-consensus.py` to record the vote:

```bash
# For approve/abstain:
python3 scripts/rfc-consensus.py cast [number] [voter] [vote]

# For reject (include reason):
python3 scripts/rfc-consensus.py cast [number] [voter] reject --reason "[reason]"
```

**Examples**:
```bash
python3 scripts/rfc-consensus.py cast 006 CTO approve
python3 scripts/rfc-consensus.py cast 006 Security reject --reason "Missing threat model"
python3 scripts/rfc-consensus.py cast 006 Toolsmith abstain
```

The script handles:
- RFC validation (exists, votable state)
- Vote table updates
- Dissent logging for reject votes
- Returns tally and consensus status

---

### 6. Check Consensus (Automatic)

The cast command output includes consensus status. Check explicitly with:

```bash
python3 scripts/rfc-consensus.py check [number] --json
```

**Response includes**:
- `consensus: true/false` - Whether consensus reached
- `rejected: true/false` - Whether RFC was rejected
- `tally` - Vote counts
- `reason` - Status explanation

---

### 7. Auto-Finalize on Consensus

**IMPORTANT**: If script output indicates consensus or rejection:

```bash
python3 scripts/rfc-consensus.py finalize [number]
```

This automatically:
- Updates RFC status to `decided` or `rejected`
- Creates `DEC-XXX-[slug].md` for approved RFCs
- Links Decision ID in RFC

**Output confirmation**:
```
┌─────────────────────────────────────────────────┐
│ RFC-XXX DECIDED - CONSENSUS REACHED             │
├─────────────────────────────────────────────────┤
│ Title: [RFC Title]                              │
│ Outcome: APPROVED                               │
│ Decision: DEC-XXX                               │
│                                                 │
│ Final tally:                                    │
│   Approve: N                                    │
│   Reject:  0                                    │
│   Abstain: N                                    │
│                                                 │
│ Decision record created:                        │
│   docs/interoffice/decisions/DEC-XXX-[slug].md  │
│                                                 │
│ Next: /broadcast "[RFC Title] Decided"          │
└─────────────────────────────────────────────────┘
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| List pending RFCs | `python3 scripts/rfc-consensus.py pending` |
| Tally votes | `python3 scripts/rfc-consensus.py tally [number]` |
| Cast vote | `python3 scripts/rfc-consensus.py cast [num] [voter] [vote]` |
| Check consensus | `python3 scripts/rfc-consensus.py check [number] --json` |
| Finalize decision | `python3 scripts/rfc-consensus.py finalize [number]` |

## Token Savings

This integration reduces token usage by ~17,500/cycle:
- Vote tallying done by script (not AI parsing tables)
- Automatic consensus detection (no manual counting)
- DEC-XXX file generated by script (not AI templating)

---

## Error Messages

| Scenario | Message |
|----------|---------|
| Not council member | "Only council members can use /vote-now" |
| Invalid vote type | "Vote must be: approve, reject, or abstain" |
| Reject without reason | "Reject votes require a reason in quotes" |
| RFC not found | "RFC-XXX not found. Run /vote-now to see available RFCs." |
| RFC in draft | "RFC-XXX is still in draft state" |
| RFC already decided | "RFC-XXX has already been decided" |
| Already voted | "You already voted [vote] on RFC-XXX" |

---

## Related Skills

- `/rfc [title]` - Create new RFC
- `/rfc-vote [number] [vote]` - Standard voting skill
- `/council [topic]` - Informal discussion before RFC
- `/broadcast [title]` - Announce decision to all terminals
