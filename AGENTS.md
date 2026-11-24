# AGENTS.md

Local-LLM agent UI: FastAPI backend streaming chat from `llama-server` (OpenAI-compatible API)
with sandboxed filesystem tools exposed to the model. There is no linter or CI; a focused
regression suite lives in `tests/`. Verify changes by running the server.

## Source navigation — required before editing code

1. Read this root `AGENTS.md` for security and runtime constraints.
2. For code changes, read `src/AGENTS.md`, then `src/INDEX.md`.
3. Follow only the relevant directory `INDEX.md` files down to the feature.
4. Open only the source files listed as owners of that behavior, plus required direct dependencies.
5. When changing or adding source files, update the nearest directory `INDEX.md`
   and every ancestor index whose structure/public contract changed.
6. If a directory in `src/` has more than three coding files (`.py`, `.js`,
   `.css`, `.html`; including `__init__.py`), it must have an `INDEX.md`.
   Directories with fewer files may also have one when navigation benefits.

**Do not use `agent/AGENTS.md` for runtime internals.**
`src/agent/` is the application’s agent runtime implementation.
Root `agent/` is reserved for future optional agent customizations and skills,
including integrations like Obsidian and personal research capabilities.
Development of the app normally happens in `src/`.

`local/` contains private machine configuration/session data;
`vault/` contains private model-facing workspace data. Never move, copy,
log, commit, or include their contents in generated documentation or examples.

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

Per-machine app values belong in `local/.env` (see `local/.env.example`). Docker Compose
2.24+ injects this optional file; `MAX_CONTEXT_TOKENS` must be a positive integer.
Docker deliberately overrides native `LLAMA_BASE_URL` with an empty value. `src/app.py` also accepts
`LLM_API_URL` as a fallback for `LLAMA_BASE_URL` (Docker Compose sets `LLM_API_URL`).

Docker-level values (container name, host port, LLM URL) belong in `.devops/.env` (see
`.devops/.env.example`), auto-read by `compose.yaml` from the project directory.

## Layout

- `src/app.py` — FastAPI composition: lifespan, static mount, cache middleware, router
  registration. Routes live in `src/api/` (`chat`, `sessions`, `health`).
- `src/agent/` — the **runtime** agent package. See `src/INDEX.md` and `src/agent/INDEX.md`
  for the internals.
  - `src/agent/core.py` — the agent loop (`LocalAgent`); each concern lives in its own module
    (`prompt.py`, `approvals.py`, `model.py`, `session_store.py`).
  - `src/agent/tools/filesystem.py` — the only filesystem layer: named tools hard-sandboxed to
    `vault/`; mutating tools require browser approval.
  - `src/agent/token_counter.py` — heuristic token budget (chars ÷ 4).
  - `src/agent/session/` — the persistent SQLite session store (schema, models, DML, usage).
- `src/static/` — the browser UI: an ES-module `app.js` bootstrap plus `js/` domain modules
  and `css/` domain stylesheets. See `src/static/INDEX.md`.
- `tests/` — focused regression tests (run with `python -m unittest discover -s tests`).
- `vault/` — the only tree the model can touch (personal data; git-ignored).
- `local/` — per-machine config/secrets (git-ignored).
- `agent/` — optional, shareable agent customizations and skills (NOT runtime code).
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
