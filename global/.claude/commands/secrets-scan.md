# Secrets Scan

Scan the codebase for exposed credentials, API keys, tokens, and other secrets.

**Access**: Security Lead (primary), all terminals (can run)

## Usage

```
/secrets-scan                 # Scan entire codebase
/secrets-scan src/            # Scan specific directory
/secrets-scan --staged        # Scan only staged files (pre-commit)
```

## Instructions

### 1. Define Secret Patterns

Search for these high-risk patterns:

#### API Keys & Tokens

```regex
# Generic API Key patterns
[aA][pP][iI][-_]?[kK][eE][yY].*['\"][0-9a-zA-Z]{16,}['\"]
[aA][pP][iI][-_]?[sS][eE][cC][rR][eE][tT].*['\"][0-9a-zA-Z]{16,}['\"]

# Supabase
SUPABASE_SERVICE_ROLE_KEY
supabase.*service.*role
anon.*key.*=.*ey[A-Za-z0-9_-]+

# AWS
AKIA[0-9A-Z]{16}
aws_secret_access_key
AWS_ACCESS_KEY_ID

# GitHub
ghp_[a-zA-Z0-9]{36}
github_token

# Stripe
sk_live_[a-zA-Z0-9]{24}
sk_test_[a-zA-Z0-9]{24}
pk_live_[a-zA-Z0-9]{24}

# Generic secret patterns
[sS][eE][cC][rR][eE][tT].*['\"][0-9a-zA-Z]{16,}['\"]
[pP][aA][sS][sS][wW][oO][rR][dD].*['\"][^'\"]{8,}['\"]
[tT][oO][kK][eE][nN].*['\"][0-9a-zA-Z]{20,}['\"]
```

#### Private Keys

```regex
-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----
-----BEGIN PGP PRIVATE KEY BLOCK-----
```

#### Connection Strings

```regex
postgres://[^:]+:[^@]+@
mysql://[^:]+:[^@]+@
mongodb(\+srv)?://[^:]+:[^@]+@
redis://:[^@]+@
```

#### JWT Tokens (hardcoded)

```regex
eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+
```

### 2. Run Scans

Execute grep searches for each pattern category:

```bash
# API Keys
grep -rn --include="*.ts" --include="*.tsx" --include="*.js" --include="*.json" --include="*.env*" \
  -E "(api[_-]?key|api[_-]?secret|SUPABASE_SERVICE_ROLE)" .

# Private Keys
grep -rn --include="*" -E "BEGIN.*PRIVATE KEY" .

# Connection Strings
grep -rn --include="*.ts" --include="*.tsx" --include="*.js" --include="*.json" --include="*.env*" \
  -E "(postgres|mysql|mongodb|redis)://[^:]+:[^@]+@" .

# Hardcoded Tokens
grep -rn --include="*.ts" --include="*.tsx" --include="*.js" \
  -E "eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]+" .
```

### 3. Exclude Safe Patterns

Ignore these (false positives):

- Files in `node_modules/`
- Files in `.git/`
- Test fixtures with dummy values
- Documentation examples
- Environment variable references (not values): `process.env.API_KEY`
- Placeholder patterns: `your-api-key-here`, `xxx`, `***`

### 4. Check Sensitive Files

Verify these files are NOT committed:

```bash
# Should NOT exist in repo
.env
.env.local
.env.production
*.pem
*.key
id_rsa
id_ed25519
credentials.json
service-account.json
```

Check `.gitignore` includes:
```
.env*
*.pem
*.key
credentials.json
```

### 5. Output Format

```markdown
# Secrets Scan Report

**Date**: YYYY-MM-DD
**Scope**: [Directory or "Full Codebase"]
**Scanner**: Security Lead

## Summary

| Category | Found | Risk |
|----------|-------|------|
| API Keys | N | HIGH/MEDIUM/LOW |
| Private Keys | N | CRITICAL |
| Connection Strings | N | HIGH |
| Hardcoded Tokens | N | HIGH |
| Sensitive Files | N | CRITICAL |

**Overall Status**: PASS | FAIL

## Findings

### CRITICAL

| File | Line | Pattern | Description |
|------|------|---------|-------------|
| path/to/file.ts | 42 | API Key | Supabase service role key exposed |

### HIGH

[Similar table...]

### MEDIUM

[Similar table...]

## False Positives Reviewed

| File | Line | Reason Safe |
|------|------|-------------|
| docs/example.md | 15 | Documentation example |

## Recommendations

1. [Immediate action needed]
2. [Secondary recommendation]

## .gitignore Verification

- [x] `.env*` patterns included
- [ ] Missing: `credentials.json`
```

### 6. Pre-commit Mode (--staged)

For `--staged` flag, only scan files in staging area:

```bash
git diff --cached --name-only | xargs grep -n -E "[pattern]"
```

Output shorter format:
```
SECRETS SCAN: PASS/FAIL
[If FAIL, list findings]
Commit blocked until resolved.
```

### 7. Remediation Guidance

For each finding type, provide fix guidance:

**API Key in Code**:
```
BEFORE: const apiKey = "sk_live_abc123..."
AFTER:  const apiKey = process.env.STRIPE_SECRET_KEY
        // Add to .env.local (gitignored)
        // Add to Supabase Vault for production
```

**Private Key Committed**:
```
1. Rotate the key immediately (consider it compromised)
2. Remove from git history: git filter-branch or BFG
3. Add to .gitignore
4. Store in secure secrets manager
```

**Connection String**:
```
BEFORE: postgres://user:password@host/db
AFTER:  process.env.DATABASE_URL
        // Store in environment variables
        // Use Supabase connection pooler
```

## Integration

### Git Pre-commit Hook

Add to `.husky/pre-commit`:
```bash
#!/bin/sh
claude /secrets-scan --staged
```

### CI/CD Integration

Add to GitHub Actions:
```yaml
- name: Secrets Scan
  run: |
    grep -rn --include="*.ts" -E "api[_-]?key.*['\"][0-9a-zA-Z]{16,}" src/ && exit 1 || exit 0
```

## Related Skills

- `/security-audit` - Full OWASP security review
- `/dependency-scan` - Check for vulnerable packages
- `/code-review` - General code review
