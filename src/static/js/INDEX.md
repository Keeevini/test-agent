# `src/static/js/` — ES Modules

Explicit ES modules, organized by domain. The module graph is a DAG; the only
cross-controller coupling is the `sessions` → `conversation` lifecycle hook,
wired in `app.js`. `api.js` is the single transport module; no other module
calls `fetch`.

| File | Purpose | Public API | Notes |
|---|---|---|---|
| `api.js` | Transport-only HTTP layer | `getHealth`, `listSessions`, `createSession`, `deleteSession`, `getSessionMessages`, `respondToApproval`, `decodeSseEvents`, `streamChat` | No DOM, no state. See below. |
| `conversation/` | Transcripts, activity cards, streaming | `createConversationController`, `initComposer`, message/activity renderers | See `conversation/INDEX.md`. |
| `sessions/` | Active session state + sidebar history | `createSessionsController`, `renderSessionList`, title-scroll | See `sessions/INDEX.md`. |
| `shell/` | Shell status, dialogs, responsive drawer | `createStatusView`, `initMobileSidebar`, `showConfirmDelete` | See `shell/INDEX.md`. |

## `api.js` — transport
- `getHealth() -> Promise<object>` — `GET /api/health`.
- `listSessions(query='') -> Promise<Array|null>` — `GET /api/sessions` (or
  `/api/sessions/search?q=` when a query is set). Returns `null` on non-OK so
  callers can distinguish "no results" from a transport failure.
- `createSession({session_id, title}) -> Promise<{session_id, title}>` —
  `POST /api/sessions`.
- `deleteSession(id) -> Promise<object>` — `DELETE /api/sessions/{id}`.
- `getSessionMessages(id) -> Promise<{messages}>` —
  `GET /api/sessions/{id}/messages`.
- `respondToApproval(approval_id, approved) -> Promise<object>` —
  `POST /api/approval`.
- `decodeSseEvents(response) -> AsyncGenerator<object>` — incremental SSE
  decoder: normalizes CRLF, keeps a trailing `\r` across chunks so a split CRLF
  pair is still normalized, and flushes any final trailing frame after EOF.
  Skips `[DONE]` and malformed frames.
- `streamChat(payload, signal) -> AsyncGenerator<object>` — `POST /api/chat`
  and yields decoded SSE events.

## Invariants
- `api.js` never touches the DOM or holds session/turn state.
- Every endpoint helper preserves the existing request/response JSON shape.
- `decodeSseEvents` is the single place that parses the SSE byte stream; the
  controllers consume its yielded JSON objects.
