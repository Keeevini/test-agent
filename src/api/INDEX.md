# `src/api/` — HTTP Route Package

Route package. Each module exposes a **factory** that builds an `APIRouter` with
dependencies injected (the `agent`, `approvals`, and `sessions` objects), so
`src/app.py` remains the composition root and creates no global route state.
`app.py` calls each factory and registers the result with
`app.include_router(...)` after the static mount and cache middleware.

| File | Purpose | Public API | Notes |
|---|---|---|---|
| `__init__.py` | Package marker | — | No side effects; creates no app object. |
| `chat.py` | Chat + approval SSE | `create_chat_router(agent, approvals)` | See below. |
| `sessions.py` | Session CRUD + context | `create_sessions_router(sessions, max_context_tokens, model, tool_specs)`, `serialize_session(meta)` | See below. |
| `health.py` | Connectivity probe | `create_health_router(llama_base_url, workspace, max_context_tokens)` | See below. |

## `chat.py`
- `create_chat_router(agent, approvals) -> APIRouter`
  - `POST /api/chat` — `chat(request: ChatRequest)`. Streams `agent.stream_turn(...)`
    as `text/event-stream` SSE. Emits each event as `data: {json}\n\n`; on
    exception, logs and emits a final `{"type": "error", ...}` event. Response
    headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
  - `POST /api/approval` — `approval(request: ApprovalRequest)`. Calls
    `approvals.resolve(approval_id, approved)`; returns `{"ok": true}` or 404.
  - Models: `ChatRequest` (`session_id` pattern-validated, `message`),
    `ApprovalRequest` (`approval_id`, `approved`).
- Invariant: SSE framing and the two headers are the transport contract — do not
  change them without updating the frontend `api.js` iterator.

## `sessions.py`
- `create_sessions_router(sessions, max_context_tokens=16384, model="local",
  tool_specs=None) -> APIRouter` — `sessions` is the
  `src.agent.SessionStore` async wrapper.
  - `POST /api/session/clear` — `clear_session(SessionRequest)` → `{"ok": true}`.
  - `GET /api/sessions?limit&offset` — `list_sessions` → `{"sessions": [...]}`.
  - `POST /api/sessions` — `create_session(CreateSessionRequest)`; server assigns
    a UUID when `session_id` is omitted → `{"session_id", "title"}`.
  - `GET /api/sessions/search?q&limit` — `search_sessions` → `{"sessions": [...]}`.
  - `GET /api/sessions/{session_id}` — `get_session`; 404 when absent.
  - `PUT /api/sessions/{session_id}` — `update_session(UpdateSessionRequest)`.
  - `DELETE /api/sessions/{session_id}` — `delete_session`.
  - `GET /api/sessions/{session_id}/messages` — `get_messages` → `{"messages": [...]}`.
  - `GET /api/sessions/{session_id}/context` — `get_context`; read-only context
    analytics via `build_context_summary`; 404 when session absent.
- `serialize_session(meta) -> dict` — single projection of a session metadata
  record onto its public shape. Keys (all preserved): `session_id`, `title`,
  `model`, `created_at`, `updated_at`, `tokens_in`, `tokens_out`, `tokens_total`,
  `cost`, `tools_used`. Shared by list/search/get.
- Models: `SessionRequest`, `CreateSessionRequest`, `UpdateSessionRequest`.
- **Invariant:** `/api/sessions/search` is declared **before**
  `/api/sessions/{session_id}` so the parameterized route cannot capture
  `search` as a session_id.

## `health.py`
- `create_health_router(llama_base_url, workspace, max_context_tokens) -> APIRouter`
  - `GET /api/health` — returns `{ok, llama_base_url, workspace, max_context_tokens}`.
