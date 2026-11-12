# Working agreements for this repository

## Commits
- Commit regularly: after each logical, working step (a fix, a feature slice, a refactor),
  not one large commit at the end of a session.
- Only commit when the checks for the touched area pass (see below). Never commit secrets
  (`.env`, `.env.litellm`) or files that contain source vocabulary terms.
- Commit messages in English, imperative mood, a short subject line plus a body explaining why.
- Work on a feature branch, never directly on `main`. Do not push unless asked.

## Checks before committing
- Backend: `cd backend && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests`
  and `docker compose -f docker-compose.dev.yaml exec -T backend pytest -q`
- API changes: `cd backend && APP_ENV=dev .venv/bin/python scripts/export_openapi.py`, then
  regenerate `frontend/src/api/schema.d.ts` (`npm run gen:api`) and commit both.
- Frontend (no Node on the host; use the pinned image):
  `docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v "$PWD/frontend":/app -w /app node:22.23.3-alpine sh -c "npm run check:api && npm run lint && npm run typecheck && npx vitest run && npm run build"`
