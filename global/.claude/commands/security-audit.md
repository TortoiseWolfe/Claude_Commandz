# Security Audit

Perform a structured OWASP Top 10 security review of a feature or the entire codebase.

**Access**: Security Lead (primary), all terminals (can request)

## Usage

```
/security-audit [feature-number|all]
/security-audit 003          # Audit feature 003 (User Authentication)
/security-audit all          # Full codebase audit
/security-audit --quick      # Quick checklist without deep analysis
```

## Instructions

### 1. Identify Scope

If a feature number is provided, read:
- `features/NNN-*/spec.md`
- `features/NNN-*/plan.md` (if exists)
- `docs/design/wireframes/NNN-*/*.svg` (if exists)

If `all` is provided, audit high-risk features first:
1. 003-user-authentication
2. 005-security-hardening
3. 000-row-level-security
4. 002-cookie-consent (privacy)
5. 019-analytics-consent (privacy)

### 2. OWASP Top 10 (2021) Checklist

For each category, check the spec/code and document findings:

#### A01:2021 - Broken Access Control

- [ ] RLS policies defined for all tables?
- [ ] User can only access their own data?
- [ ] Admin functions protected?
- [ ] CORS configured correctly?
- [ ] Directory traversal prevented?
- [ ] JWT/session validation on all protected routes?

**Questions to ask:**
- Who can access this data?
- What happens if user ID is manipulated?
- Are there elevation of privilege risks?

#### A02:2021 - Cryptographic Failures

- [ ] Sensitive data encrypted at rest?
- [ ] TLS/HTTPS enforced?
- [ ] Passwords hashed with strong algorithm (bcrypt, argon2)?
- [ ] No sensitive data in URLs or logs?
- [ ] Secrets stored in Supabase Vault, not code?

**Questions to ask:**
- What data is sensitive (PII, credentials, tokens)?
- How is data transmitted?
- Where are secrets stored?

#### A03:2021 - Injection

- [ ] Parameterized queries used (Supabase client)?
- [ ] User input validated and sanitized?
- [ ] No dynamic SQL construction?
- [ ] XSS prevention (output encoding)?
- [ ] Command injection prevented?

**Questions to ask:**
- Where does user input enter the system?
- Is input ever used in queries, commands, or HTML?

#### A04:2021 - Insecure Design

- [ ] Threat model documented?
- [ ] Security requirements in spec?
- [ ] Abuse cases considered?
- [ ] Rate limiting designed?
- [ ] Secure defaults?

**Questions to ask:**
- What could an attacker do with this feature?
- What's the worst-case scenario?

#### A05:2021 - Security Misconfiguration

- [ ] No default credentials?
- [ ] Error messages don't leak info?
- [ ] Security headers configured (CSP, X-Frame-Options)?
- [ ] Unnecessary features disabled?
- [ ] Dependencies up to date?

**Questions to ask:**
- What's exposed by default?
- What information do errors reveal?

#### A06:2021 - Vulnerable Components

- [ ] Dependencies audited (`npm audit`)?
- [ ] No known vulnerabilities in packages?
- [ ] Components from trusted sources?
- [ ] Unused dependencies removed?

**Questions to ask:**
- When was the last dependency audit?
- Are there any CVEs affecting our stack?

#### A07:2021 - Authentication Failures

- [ ] Strong password policy enforced?
- [ ] Multi-factor authentication available?
- [ ] Session management secure?
- [ ] Brute force protection (rate limiting)?
- [ ] Secure password recovery flow?

**Questions to ask:**
- How are credentials validated?
- What happens after failed attempts?
- How are sessions invalidated?

#### A08:2021 - Data Integrity Failures

- [ ] Integrity checks on critical data?
- [ ] Signed tokens (JWT) validated?
- [ ] CI/CD pipeline secured?
- [ ] Software updates verified?

**Questions to ask:**
- Can data be tampered with?
- How is code deployment secured?

#### A09:2021 - Logging & Monitoring Failures

- [ ] Security events logged?
- [ ] Logs don't contain sensitive data?
- [ ] Alerting configured for anomalies?
- [ ] Audit trail for sensitive operations?

**Questions to ask:**
- What events are logged?
- How would we detect an attack?

#### A10:2021 - SSRF

- [ ] URL validation on user-provided URLs?
- [ ] Internal network access blocked?
- [ ] Allowlist for external services?

**Questions to ask:**
- Does the app fetch external resources?
- Can users control destination URLs?

### 3. ScriptHammer-Specific Checks

Based on constitution and architecture:

#### Supabase Security
- [ ] RLS enabled on all tables?
- [ ] Service role key never exposed to client?
- [ ] Edge Functions validate auth?
- [ ] Anon key permissions minimized?

#### Static Export Security
- [ ] No server-side secrets in client bundle?
- [ ] Environment variables properly handled?
- [ ] API keys in Supabase Vault only?

#### Privacy (Constitution Principle VI)
- [ ] GDPR consent before data collection?
- [ ] Cookie consent implemented?
- [ ] Analytics respect user preferences?
- [ ] Data retention policies defined?

### 4. Output Format

Generate a security audit report:

```markdown
# Security Audit: [Feature/Scope]

**Date**: YYYY-MM-DD
**Auditor**: Security Lead
**Scope**: [Feature number or "Full Codebase"]

## Summary

- **Risk Level**: LOW | MEDIUM | HIGH | CRITICAL
- **Issues Found**: N
- **Blockers**: N (must fix before implementation)

## OWASP Findings

### A01: Broken Access Control
**Status**: PASS | FAIL | N/A
**Findings**:
- [Issue description]
**Remediation**:
- [How to fix]

[Repeat for each category...]

## ScriptHammer-Specific Findings

[Supabase, Static Export, Privacy findings...]

## Recommendations

1. [Priority action item]
2. [Secondary action item]

## Sign-off

- [ ] All blockers resolved
- [ ] Security Lead approves for implementation
```

### 5. Save Report

Save the audit report to:
```
docs/security/audits/YYYY-MM-DD-[scope]-audit.md
```

Create `docs/security/` directory if it doesn't exist.

## Quick Mode (--quick)

For `--quick` flag, output just the checklist with PASS/FAIL/TODO without detailed analysis:

```markdown
# Quick Security Checklist: [Feature]

| Category | Status | Notes |
|----------|--------|-------|
| A01: Access Control | PASS/FAIL/TODO | Brief note |
| A02: Cryptography | PASS/FAIL/TODO | Brief note |
| ... | ... | ... |

**Next Steps**: [What needs attention]
```

## Related Skills

- `/secrets-scan` - Scan for exposed credentials
- `/dependency-scan` - Check for vulnerable packages
- `/code-review` - General code review including security
