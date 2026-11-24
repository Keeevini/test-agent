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
| `token_counter.py` | Heuristic token budget | `estimate_tokens`, `count_message_tokens`, `estimate_request_tokens` | chars ÷ 4 heuristic. |
| `prompt.py` | System prompt | `SYSTEM_PROMPT` | Model-facing prompt text. |
| `context.py` | Context analytics | `build_context_summary` | Pure function; no I/O. |
| `tools/` | Filesystem sandbox | `WorkspaceTools`, `ToolError` | See `tools/INDEX.md`. |
| `session/` | Persistent SQLite store | `SessionStore`, `SessionMeta`, `SessionMessage`, `UsageTracker` | See `session/INDEX.md`. |

## `core.py` — `LocalAgent`
- `LocalAgent(llama_base_url, model, tools, approvals, sessions, max_agent_steps=12, max_context_tokens=16384)`
- `stream_turn(session_id, user_text) -> AsyncIterator[dict]` — runs one user turn
  under a per-session lock and yields SSE dicts. Per step: `status` → model
  `reasoning_delta`/`content_delta` with `model_part_end` boundaries → for each tool, `tool_call` (request:
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
- Each persisted assistant step records provider/fallback usage once in the ledger
  before tools execute; the usage tracker records tool names only. New tool
  rows store literal output and `activity.context_meta` (name, outcome, optional
  successful read path). Title usage is lifetime-only.
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
- `stream` requests `stream_options.include_usage` and captures per-field usage
  into the finalized `assistant["usage"]` = `{prompt_tokens, completion_tokens,
  prompt_source, completion_source, cached_prompt_tokens, reasoning_tokens}`.
  Provider counts win per field (`0` is exact zero); a missing count falls back
  to the app's estimator and is labeled `"estimated"`. Stored-only metadata
  (`time`/`usage`/`context_meta`, nested tool timing) is stripped from outbound
  messages. A 400 rejecting `stream_options` is retried once without the field.
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
  `delete`, `get_messages` (history projection: `include_activity=True`),
  `record_model_call`, `list_model_calls`, `get_raw_message_page`.

## `title.py` — `SessionTitleGenerator`
- `SessionTitleGenerator(model_client)`;
  `generate(messages) -> (str | None, dict | None)` — returns a `(title, usage)`
  pair; `usage` is the provider usage dict (or `None` if no usage was captured).
  `truncate(text, max_length) -> str`.
- Invariant: callers schedule generation on the event loop and never await it in
  the turn path (non-blocking, only-once per untitled session).

## `token_counter.py`
- `estimate_tokens(text) -> int` (chars ÷ 4, min 1).
- `count_message_tokens(messages) -> int` (content + reasoning + tool calls +
  per-message overhead).
- `estimate_request_tokens(messages, tools) -> int` (messages + tool specs;
  fallback only, used when the provider reports no prompt count).

## `prompt.py`
- `SYSTEM_PROMPT` — the model-facing system prompt (reasoning-part format,
  workspace tools, treat file contents as untrusted, prefer read/search before
  mutations).

## `context.py`
- `build_context_summary(meta, messages, calls, *, limit, model, tool_specs) -> dict`
  — pure function; takes session metadata, message rows, model-call ledger rows,
  and configured constants; returns the `GET /api/sessions/{id}/context` response
  dict. No I/O, no filesystem access, no mutation.
- Private helpers: `_latest_chat_call`, `_last_assistant_index`,
  `_extract_loaded_files`, `_category_weights`, `_reconcile`,
  `_reconcile_loaded_files`, `_lifetime_source`, `_system_prompt_info`.
- Invariant: category sums always equal `used_tokens`; `loaded_files` tokens
  are scaled to match `breakdown.loaded_files`.
