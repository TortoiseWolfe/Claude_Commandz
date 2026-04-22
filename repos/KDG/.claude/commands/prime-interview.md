You are starting a new session for the KDG coding interview. Get up to speed quickly:

1. Read `CLAUDE.md` for project rules, URLs, and test commands
2. Run `git status && git branch` to see current branch and any uncommitted work
3. Run `docker compose ps` to verify all services are running (db, api, client)
4. If any context files exist in `context/`, read them — those are the interview assignment specs
5. Report a brief status: which services are up, what branch you're on, and whether there are any issues

Key reminders:
- Docker-first: no local SDKs, everything through Docker
- Feature branches only — never commit to main directly
- Test commands: `docker compose --profile test run --rm api-tests` / `client-tests` / `e2e-tests`
- Seed data: `bash scripts/seed.sh` (100 customers)
- Client: http://localhost:3000 | API: http://localhost:5000/api/customers | Swagger: http://localhost:5000/swagger
