Run the Playwright E2E site audit against a target environment.

## Usage

- `/site-audit` — run against production (default)
- `/site-audit local` — run against local dev (localhost:8080)
- `/site-audit staging` — run against staging
- `/site-audit production` — run against production

## Instructions

Parse the argument from `$ARGUMENTS` to determine the target. Default to `production` if no argument given.

Run the appropriate make target:

- `local` → `make test-local`
- `staging` → `make test-staging`
- `production` → `make test-production`

After the audit, summarize:
- Score: passed / total (percentage)
- List every failure with the test name
- If score is 100%, say so

If local dev is requested and containers aren't running, suggest `make up` first.
