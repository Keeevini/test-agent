# `src/agent/` — Agent Runtime

The runtime agent package: orchestration, model client, approval gate, tool
layer, and persistence. `__init__.py` re-exports the public API
(`ApprovalManager`, `LocalAgent`, `SessionStore`); import from the package
root, not the submodules.

| File | Purpose | Public API | Notes |
|---|---|---|---|
| `__init__.py` | Package re-exports | `ApprovalManager`, `LocalAgent`, `SessionStore` | `__all__` marker. |
| `core.py` | Agent loop | `LocalAgent` | See below. |
| `model.py` | llama-server SSE client | `ModelClient` | See below. |
| `approvals.py` | Approval gate | `ApprovalManager`, `PendingApproval` | See below. |
| `session_store.py` | Async store wrapper | `SessionStore` | See below. |
| `title.py` | Session title generation | `SessionTitleGenerator` | See below. |
| `token_counter.py` | Heuristic token budget | `estimate_tokens`, `count_message_tokens` | chars ÷ 4 heuristic. |
| `prompt.py` | System prompt | `SYSTEM_PROMPT` | Model-facing prompt text. |
| `tools/` | Filesystem sandbox | `WorkspaceTools`, `ToolError` | See `tools/INDEX.md`. |
| `session/` | Persistent SQLite store | `SessionStore`, `SessionMeta`, `SessionMessage`, `UsageTracker` | See `session/INDEX.md`. |

## `core.py` — `LocalAgent`
- `LocalAgent(llama_base_url, model, tools, approvals, sessions, max_agent_steps=12, max_context_tokens=16384)`
- `stream_turn(session_id, user_text) -> AsyncIterator[dict]` — runs one user turn
  under a per-session lock and yields SSE dicts. Per step: `status` → model
  `reasoning_delta`/`content_delta` →, for each tool, `tool_call` (request:
  `tool_call_id`/`name`/`arguments`/`alias`) → optionally `approval_required` →
  `approval_decision` (`status`: `"approved"`|`"denied"`/`alias`) → `tool_result`
  (`tool_call_id`/`name`/`ok`/`result`/`alias`) → `context` (usage) → `done`.
  On the step cap it yields `error`. Invalid-JSON args emit the request with the
  raw argument string, then a failed `tool_result` (not executed). Persists
  user/assistant/tool messages to the store and schedules a one-shot title after
  the first streamed turn. `alias` is a display-only label resolved from the
  current tool registry (`tools.aliases`); `None` when unconfigured.
- Durable lifecycle: missing/duplicate tool-call IDs are normalized on the call
  objects before the assistant row is persisted; one durable tool outcome is
  stored per normal attempted call (via a local `settle` helper) before the
  associated SSE event; a mutating tool's approval `pending` state is persisted
  before `approval_required` and the `approval_decision` is emitted before any
  result. A `try/finally` around the tool batch persists a synthetic
  `interrupted` outcome for any unsettled call and discards/`cancelled`-marks any
  still-pending approval when the generator is closed mid-step.
- `close()` — closes the model client.
- Invariant: `stream_turn` is the single place that mutates the message list and
  the store for a turn; `time` stamps (`created`/`streamed`/`completed`) are
  stamped here and read by the history UI.

## `model.py` — `ModelClient`
- `ModelClient(llama_base_url, model, tools)`; `stream(messages) -> (assistant, events)`.
  Returns a finalized `assistant` dict and an async iterator of `reasoning_delta`
  / `content_delta` events. The `assistant` dict is populated in the iterator's
  `finally` block (so it is set even on disconnect). Accepts a server origin, an
  `/v1` base, or the full chat endpoint.
- `close()` — closes the httpx client.

## `approvals.py` — `ApprovalManager`
- `ApprovalManager()`; `create(session_id, tool_name, arguments, on_decision=None)
  -> PendingApproval`; `resolve(approval_id, approved) -> bool`;
  `wait(item) -> bool`; `discard(item) -> None`.
- `PendingApproval` dataclass: `approval_id`, `session_id`, `tool_name`,
  `arguments`, `event`, `approved`, `on_decision`.
- `resolve` is first-wins: returns `False` if the item is absent **or** already
  decided; invokes the `on_decision` callback (synchronous, no await) before
  writing the decision, propagating callback exceptions so a failed persistence
  is not acknowledged. `wait` only awaits the event (it no longer pops the item);
  `discard` is synchronous and idempotent, removing only the identical item.
- Invariant: the first decision wins and cannot be overwritten; core owns removal
  via `discard` in a `finally` block. Mutating tools are gated here, not in the
  prompt.

## `session_store.py` — `SessionStore`
- Async wrapper over the persistent `session.store.SessionStore`. Keeps per-process
  `asyncio.Lock`s in `lock(session_id)`.
- `messages(session_id)` seeds a `SYSTEM_PROMPT` on first use (model projection:
  `include_activity=False`). `append_message(session_id, role, content, *,
  activity=None)`, `update_message_content`, `update_message_activity`, `clear`,
  `close`.
- Proxies for the endpoints: `list`, `get`, `create`, `search`, `update_title`,
  `delete`, `get_messages` (history projection: `include_activity=True`).

## `title.py` — `SessionTitleGenerator`
- `SessionTitleGenerator(model_client)`; `generate(messages) -> str | None`;
  `truncate(text, max_length) -> str`.
- Invariant: callers schedule generation on the event loop and never await it in
  the turn path (non-blocking, only-once per untitled session).

## `token_counter.py`
- `estimate_tokens(text) -> int` (chars ÷ 4, min 1).
- `count_message_tokens(messages) -> int` (content + reasoning + tool calls +
  per-message overhead).

## `prompt.py`
- `SYSTEM_PROMPT` — the model-facing system prompt (reasoning-part format,
  workspace tools, treat file contents as untrusted, prefer read/search before
  mutations).
