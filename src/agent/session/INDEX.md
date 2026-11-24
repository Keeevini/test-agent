# `src/agent/session/` — Persistent SQLite Store

The persistent session store: a single SQLite database file with WAL and
foreign keys, split into repository mixins and DDL modules. `store.py` is the
public entry; the repositories and schema are maintained independently.

| File | Purpose | Public API | Notes |
|---|---|---|---|
| `__init__.py` | Package re-exports | `SessionMeta`, `SessionMessage`, `SessionStore`, `UsageTracker` | `__all__` marker. |
| `store.py` | SQLite connection + lifecycle | `SessionStore` (extends both repo mixins) | See below. |
| `session_repo.py` | `sessions` table CRUD | `SessionsRepository` | See below. |
| `message_repo.py` | `messages` table CRUD | `MessagesRepository` | See below. |
| `models.py` | Row dataclasses | `SessionMeta`, `SessionMessage`, `_utcnow` | See below. |
| `usage.py` | Per-turn usage tracking | `UsageTracker` | See below. |
| `schema/` | DDL | `apply_schema`, `SCHEMA_VERSION`, `CREATE_MODEL_CALLS_TABLE` | See `schema/INDEX.md`. |

## `store.py` — `SessionStore`
- `SessionStore(db_path)` — opens a thread-safe `sqlite3` connection
  (`check_same_thread=False`, a `threading.Lock`), sets `row_factory=Row`,
  enables `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`, then applies
  the schema.
- `close()` — closes the connection under the lock.
- Inherits session CRUD from `SessionsRepository` and message CRUD from
  `MessagesRepository`.

## `session_repo.py` — `SessionsRepository`
- `create_session(session_id, title='Untitled Session', model=None) -> SessionMeta`
  — `INSERT ... ON CONFLICT(session_id) DO NOTHING`; repeated creates preserve
  messages, title, usage, and timestamps.
- `get_session(session_id) -> SessionMeta | None`.
- `list_sessions(limit=50, offset=0) -> list[SessionMeta]` — `ORDER BY updated_at DESC`.
- `search_sessions(query, limit=50) -> list[SessionMeta]` — case-insensitive `LIKE`
  on title / session_id / model.
- `update_title(session_id, title)`; `delete_session(session_id)` (CASCADE to messages).
- `update_usage(session_id, tokens_in=0, tokens_out=0, cost=0.0, tools_used=None)`
  — additive counters; `tools_used` names are merged (deduplicated).
- `set_model(session_id, model)`.
- `record_model_call(session_id, call_key, kind, assistant_seq, usage) -> bool`
  — inserts one durable ledger row into `model_calls` and increments the
  lifetime `sessions` counters **only** on a real insert. Idempotent per
  `(session_id, call_key)` (`INSERT OR IGNORE`); a duplicate returns `False`
  and never double-counts. A missing session (FK violation) returns `False`
  and is never recreated. Validates `usage` (`prompt_tokens` /
  `completion_tokens` are `int >= 0`, `prompt_source` / `completion_source`
  are `str`) and raises `ValueError` otherwise.
- `list_model_calls(session_id) -> list[dict]` — ledger rows oldest first,
  ordered by `recorded_at` then `assistant_seq` (never by same-second
  timestamps alone). "Latest chat call" is `kind == 'chat'` + max `assistant_seq`.

## `message_repo.py` — `MessagesRepository`
- `get_messages(session_id, *, include_activity=False) -> list[dict]` —
  ordered by `seq`; assistant JSON content is deserialized back to a dict.
  Projects a top-level `tool_call_id` onto tool rows (from version-1 activity
  metadata, or inferred for legacy batches). Attaches the `activity` field
  only when `include_activity=True` (history projection); the model
  projection (`include_activity=False`) keeps `tool_call_id` but omits
  `activity`.
- `append_message(session_id, role, content, *, activity=None) -> int` —
  returns the new `seq` (`MAX(seq)+1`); content may be a `str` (user/system)
  or `dict` (assistant), JSON-encoded. `activity` is optional presentation
  metadata stored in the `activity_json` column (separate from model content).
  Touches the session's `updated_at`. New tool output is literal text with
  `activity.context_meta` projected as top-level `context_meta`; legacy context
  JSON envelopes without activity are still decoded.
- `get_raw_message_page(session_id, offset, limit) -> dict` — read-only
  paginated page under the store lock: `COUNT(*)` + bounded `SELECT seq, role,
  content, activity_json ... ORDER BY seq LIMIT ? OFFSET ?`. Returns `{total, offset, limit,
  has_more, messages}` where each message is `{seq, role, created_ms, raw}` and
  `raw` is the decoded stored message with activity when present (no inferred IDs). `created_ms` is
  extracted from `raw.time.created` when present, else `None`.
- `update_message_content(session_id, seq, content)` — replaces content by `seq`
  (used to stamp timing metadata); leaves `activity_json` untouched.
- `update_message_activity(session_id, seq, activity)` — replaces the
  `activity_json` column by `seq`; leaves `content` untouched.

## `models.py`
- `SessionMeta` — session row dataclass (`session_id`, `title`, `model`,
  `created_at`, `updated_at`, `tokens_in/out/total`, `cost`, `tools_used`);
  `from_row(row) -> SessionMeta`.
- `SessionMessage` — message row dataclass (`session_id`, `seq`, `role`,
  `content`); `to_dict() -> dict` — assistant content parsed as JSON; tool rows
  parsed when the stored JSON is a genuine tool object, else legacy raw text.
- `_utcnow() -> str` — current UTC time as `YYYY-MM-DD HH:MM:SS` (SQLite-compatible).

## `usage.py` — `UsageTracker`
- `UsageTracker` dataclass: `tokens_in`, `tokens_out`, `cost`, `tools_used`.
- `record_tokens(prompt_tokens, completion_tokens)`; `record_tool(tool_name)`
  (deduplicated); `flush(store, session_id)` — persists via `store.update_usage`
  and resets the tracker.
