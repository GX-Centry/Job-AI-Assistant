# Repository Guidelines

## Project Structure & Module Organization

- `job_agent/` contains the LangGraph backend: `api/` for FastAPI and sessions, `llm/` for model creation, `tools/` for tool execution, and `memory/` for persistence and approval workflows.
- `frontend/src/` contains Vue components, composables, and API/SSE helpers; assets live in `src/assets/` and `public/`.
- `tests/` separates backend unit, integration, and API tests. `scripts/` contains chat and live-model diagnostics. Runtime SQLite files belong in ignored `data/`.

## Build, Test, and Development Commands

Copy `.env.example` to `.env` before starting (fill in `JOB_AGENT_API_KEYS`; `JOB_AGENT_MODEL_*` only for chat). Run backend commands from the repository root through the `dev` container:

- `docker compose up --build -d`: fresh installs only — build and start the LangGraph dev server (port 2024) and the Compose-managed chat API (`api` service, host port `JOB_AGENT_API_HOST_PORT`, default 18000, with a `/health` healthcheck; follow logs via `docker compose logs -f api`). Never run this against the live online environment (it would rebuild/recreate the live `dev` container). The live 8000 uvicorn in the `dev` container remains until the planned switch-over (see README runbook); restart that uvicorn manually after backend edits.
- `docker compose exec -T dev python -m pytest`: run backend tests with a coverage report. Storage paths are auto-redirected to temporary directories (see `tests/conftest.py`), so focused subsets like `python -m pytest tests/unit/test_config.py` run directly with no extra flags.
- `docker compose exec -T dev python -m pytest --cov-fail-under=90`: the CI coverage gate, stated explicitly in CI rather than `pytest.ini`.
- `docker compose exec -T dev ruff check .`: lint Python.
- `docker compose exec -T dev ruff format --check .`: check formatting; use `ruff format .` inside the container to apply it.

Inside `frontend/`, run `npm ci` to install dependencies, `npm run dev` for Vite, `npm test` for Vitest (default pool; CI uses the same), and `npm run build` for production assets. Use `npm.cmd` if PowerShell blocks `npm`. CI pins Node 24 to match the verified runtime (24.21.0, npm 11, lockfileVersion 3).

The isolated acceptance stack runs a throwaway Compose project with placeholder credentials, port 18000, and its own volume. Every command must carry `--env-file deploy/accept.env` (switches Compose interpolation away from the real root `.env`), `-p m0r3-accept`, both `-f` files, and explicitly name the `api` service; tear it down with `down -v --remove-orphans` under the same flags and remove the image afterwards (exact commands in README). Never rebuild or restart the live `dev` container from an acceptance run.

Run Python scripts as modules, for example `docker compose exec -T dev python -m scripts.check_llm_connection`.

## Coding Style & Naming Conventions

Use four-space Python indentation, snake_case functions/modules, PascalCase classes, and Ruff formatting. Follow existing Vue/JavaScript style: two spaces, single quotes, no semicolons, camelCase functions, and PascalCase `.vue` filenames.

## Testing Guidelines

Name backend tests `test_*.py` and frontend tests `*.test.js`, colocated with frontend modules. CI enforces at least 90% coverage of `job_agent` via an explicit `--cov-fail-under=90` flag (pytest.ini only reports). Use fake models and temporary databases; reserve real LLM calls for diagnostic scripts. `tests/conftest.py` redirects all default storage paths to a temporary directory, so tests never touch `data/`.

## Commit & Pull Request Guidelines

Follow the short imperative subjects in Git history, such as `Add session management API`. Keep commits focused. PRs should describe behavior changes, link relevant issues, report validation, and include screenshots for UI changes. Pass CI's Ruff and pytest checks; validate frontend changes separately.

## Security & Configuration

Keep credentials in ignored environment files (`.dockerignore` excludes `.env*` and local overrides while keeping `.env.example` samples, and excludes `data/`, Git metadata, caches, `node_modules`, and frontend build output). Configure models with `JOB_AGENT_MODEL_*`; `JOB_AGENT_API_KEYS` maps API keys to user IDs as JSON. Set the frontend development key in `frontend/.env.local` as `VITE_API_KEY` (sample in `frontend/.env.example`). The Vite `/api` proxy target is configurable via `VITE_API_PROXY_TARGET` (default stays `http://127.0.0.1:8000` for the live environment; fresh installs point it at the Compose `api` on 18000). Do not edit the real `.env` / `.env.local` files. Chat attachments follow the README contract: images are the only model-readable kind (base64 ≤5 MiB, ≤3 per message); audio/file attachments are metadata-only (`data` is rejected) because the gateway silently drops `input_audio`; the in-browser hold-to-talk recorder (`frontend/src/utils/voiceRecorder.js`) never ships the recorded audio, only its mime/size. Preserve user isolation and the explicit approval workflow for long-term memory writes. CI runs offline by default; LangSmith tracing is a separate opt-in job gated on the repository secret.
