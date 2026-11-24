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
| `tool_links.py` | Batch-scoped result association | `associate_tool_results(messages)` | Result index to unique preceding assistant call; no positional inference. |
| `schema/` | DDL | `apply_schema`, `SCHEMA_VERSION`, `CREATE_MODEL_CALLS_TABLE` | See `schema/INDEX.md`. |

## `store.py` — `SessionStore`
- `SessionStore(db_path)` — opens a thread-safe `sqlite3` connection
  (`check_same_thread=False`, a `threading.Lock`), sets `row_factory=Row`,
  enables foreign keys, checks/initializes the canonical schema, then enables WAL.
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
- `update_usage(session_id, cost=0.0, tools_used=None)` — cost/tool names only, no token write. Session get/list/search derive token totals via a selected-page aggregate of `model_calls`.
- `set_model(session_id, model)`.
- `record_model_call(session_id, call_key, kind, assistant_seq, usage) -> bool` — title-only (`title`, `title`, null). Duplicate key returns false; missing session never recreated. Chat rows use atomic assistant append. Required counts nonnegative ints, sources provider/estimated; optional cached/reasoning counts preserve null or zero.
- `list_model_calls(session_id) -> list[dict]` — ledger rows oldest first,
  ordered by `recorded_at` then `assistant_seq` (never by same-second
  timestamps alone). "Latest chat call" is `kind == 'chat'` + max `assistant_seq`.

## `message_repo.py` — `MessagesRepository`
- `get_messages(session_id, *, include_activity=False, include_seq=False)` — chronological canonical projection. Assistant JSON decoded; tool content remains literal and `tool_call_id` comes from its column. Activity and seq are opt-in.
- `append_message(session_id, role, content, *, activity=None, tool_call_id=None) -> int` — returns new `seq`, touches session; tool requires literal string and nonempty explicit ID; non-tool requires null ID. `activity_json` holds outcome/presentation data only.
- `append_assistant_with_usage(session_id, content, usage, *, activity=None)` — one lock/transaction for assistant + unique `chat:<seq>` ledger row; rollback both on failure. Assistant content never stores `usage`.
- `get_raw_message_page(session_id, offset, limit)` — bounded count + page query, at most one preceding non-tool row for page-boundary association. Entries `{seq, role, created_ms, raw, tool_name?}`. `raw` is the full decoded stored message plus activity and chat usage joined by seq from a bounded ledger query; name is derived, not persisted twice. Timestamps only from stored `time.created`.
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
  always literal text; repository attaches the explicit column ID.
- `_utcnow() -> str` — current UTC time as `YYYY-MM-DD HH:MM:SS` (SQLite-compatible).

## `usage.py` — `UsageTracker`
- `record_tool(tool_name)`; `flush(store, session_id)` — persist deduplicated executed tool names only. Never records model tokens.
