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
  under a per-session lock and yields SSE dicts. Event lifecycle per step:
  `status` → model `reasoning_delta`/`content_delta` → `tool_call` /
  `approval_required` / `tool_result` for each tool → `context` (usage) → `done`.
  On the step cap it yields `error`. Persists user/assistant/tool messages to the
  store and schedules a one-shot title after the first streamed turn.
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
- `ApprovalManager()`; `create(session_id, tool_name, arguments) -> PendingApproval`;
  `resolve(approval_id, approved) -> bool`; `wait(item) -> bool`.
- `PendingApproval` dataclass: `approval_id`, `session_id`, `tool_name`,
  `arguments`, `event`, `approved`.
- Invariant: `resolve` sets the event (unblocking `wait`); `wait` pops the pending
  item. Mutating tools are gated here, not in the prompt.

## `session_store.py` — `SessionStore`
- Async wrapper over the persistent `session.store.SessionStore`. Keeps per-process
  `asyncio.Lock`s in `lock(session_id)`.
- `messages(session_id)` seeds a `SYSTEM_PROMPT` on first use; `append_message`,
  `update_message_content`, `clear`, `close`.
- Proxies for the endpoints: `list`, `get`, `create`, `search`, `update_title`,
  `delete`, `get_messages`, `record_model_call`, `list_model_calls`.

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
