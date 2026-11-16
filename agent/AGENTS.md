# AGENTS.md — agent internals

This file documents the **agent layer** in `src/agent/`. For the overall project layout and how to
run the app, see the root `AGENTS.md`.

## What lives here

`src/agent/` is a Python package. Each concern lives in its own module so a
capability can be developed without editing the others:

- `core.py` — the agent loop (`LocalAgent`, capped at 12 model/tool steps per turn). It
  orchestrates the other modules below.
- `prompt.py` — the model-facing `SYSTEM_PROMPT`.
- `approvals.py` — the `ApprovalManager` that gates mutating tools behind browser approval.
- `model.py` — the `ModelClient` (streaming HTTP/SSE client to `llama-server`).
- `session_store.py` — the async `SessionStore` wrapper (per-session asyncio locks +
  system-prompt seeding) over the persistent store.
- `token_counter.py` — heuristic token budget (chars ÷ 4), not an exact tokenizer.
- `tools/` — the filesystem layer. `tools/filesystem.py` exposes `WorkspaceTools` (the named
  capabilities hard-sandboxed to the vault) and `ToolError`. `tools/__init__.py` re-exports them.
- `session/` — the persistent SQLite session store:
  - `schema/` — DDL, split per table (`sessions.py`, `messages.py`, `indexes.py`).
  - `models.py` — row → model mapping (`SessionMeta`, `SessionMessage`).
  - `session_repo.py` / `message_repo.py` — repository mixins (session-property CRUD /
    message CRUD).
  - `store.py` — the `SessionStore` that composes the repositories.
  - `usage.py` — per-turn `UsageTracker` (tokens, cost, tools used).

## Imports

Inside the package use relative imports (`from .token_counter import ...`,
`from .tools import ...`). Outside the package (the FastAPI app in `src/app.py`) use
`from src.agent import ...` and `from src.agent.tools import WorkspaceTools`.

## Constraints

- `tools/filesystem.py` (host code) is the **authorization layer**, not the system prompt. Keep
  the path sandbox (resolve + containment check) and the approval gate for mutating tools
  intact; treat file contents as untrusted input.
- `opencode.json` points OpenCode at the local llama server (`Qwen 27B` on `127.0.0.1:8080`);
  edits take effect immediately.
