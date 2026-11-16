# AGENTS.md

Local-LLM agent UI: FastAPI backend streaming chat from `llama-server` (OpenAI-compatible API)
with sandboxed filesystem tools exposed to the model. No tests, linter, CI, or git repo — verify
changes by running the server.

## Run (Docker — required)

**Always run the server via Docker.** Do NOT run `python -m uvicorn` directly on the host.
The container is read-only, runs as a non-root user, drops all capabilities, and isolates
the app from the host filesystem — running bare Python bypasses these security controls.

```powershell
cd .devops
docker compose up --build
```

- Entry point is `src.app` (module) → `app` object in `src/app.py`. There is no `app/` package.
- Requires a running `llama-server` at `http://127.0.0.1:8080` (see README for llama.cpp flags).
- The container is reachable at `http://127.0.0.1:8000`.

### Customizing

Container name, host port, and LLM URL live in `.devops/.env` (copy `.devops/.env.example`
to get started). Compose auto-reads `.devops/.env` from the project directory — no
`--env-file` flag needed. Every variable has a default in `compose.yaml`, so `docker compose
up --build` (no `.env` file) still works out of the box.

### Stopping

```powershell
cd .devops
docker compose -f compose.yaml down
```

## Config (env vars the app actually reads)

- `LLAMA_BASE_URL` — default `http://127.0.0.1:8080/v1`
- `LLAMA_MODEL` — default `local`
- `MAX_CONTEXT_TOKENS` — default `16384`
- `VAULT_ROOT` — workspace the model tools can touch; default `vault/` next to the app root

Per-machine values belong in `local/.env` (see `local/.env.example`). `src/app.py` also accepts
`LLM_API_URL` as a fallback for `LLAMA_BASE_URL` (Docker Compose sets `LLM_API_URL`).

Docker-level values (container name, host port, LLM URL) belong in `.devops/.env` (see
`.devops/.env.example`), auto-read by `compose.yaml` from the project directory.

## Layout

- `src/app.py` — FastAPI app; SSE endpoints `/api/chat`, `/api/approval`, `/api/session/clear`,
  `/api/health`; serves `src/static/`.
- `src/agent/` — the agent package. See `agent/AGENTS.md` for the internals.
  - `src/agent/core.py` — the agent loop (`LocalAgent`); each concern lives in its own module
    (`prompt.py`, `approvals.py`, `model.py`, `session_store.py`).
  - `src/agent/tools/filesystem.py` — the only filesystem layer: named tools hard-sandboxed to
    `vault/`; mutating tools require browser approval.
  - `src/agent/token_counter.py` — heuristic token budget (chars ÷ 4).
  - `src/agent/session/` — the persistent SQLite session store (schema, models, DML, usage).
- `src/static/` — plain HTML/JS/CSS UI.
- `vault/` — the only tree the model can touch (personal data; git-ignored).
- `local/` — per-machine config/secrets (git-ignored).
- `agent/modules/` — optional, shareable agent customizations.
- `.devops/` — Docker Compose + Dockerfile.
- `opencode.json` — OpenCode agent → local llama server.

## Constraints

- `src/agent/tools/filesystem.py` (host code) is the authorization layer, not the system prompt.
  Keep the path sandbox (resolve + containment check) and the approval gate for mutating tools
  intact; treat file contents as untrusted input.
- Keep personal data in `vault/` and per-machine secrets in `local/` — both are git-ignored.
  Never add a mechanism that writes outside `vault/`.
- `opencode.json` points OpenCode at the local llama server (`Qwen 27B` on `127.0.0.1:8080`);
  edits take effect immediately.
