# `src/agent/session/schema/` — SQLite DDL

DDL for the session store, split per table so each can be maintained
independently. `apply_schema` runs the table DDLs then the indexes in order.

| File | Purpose | Public API | Notes |
|---|---|---|---|
| `__init__.py` | DDL entry + version | `apply_schema`, `SCHEMA_VERSION`, `CREATE_SESSIONS_TABLE`, `CREATE_MESSAGES_TABLE`, `CREATE_MODEL_CALLS_TABLE`, `CREATE_INDEXES` | See below. |
| `sessions.py` | `sessions` table DDL | `CREATE_SESSIONS_TABLE` | See below. |
| `messages.py` | `messages` table DDL | `CREATE_MESSAGES_TABLE` | See below. |
| `model_calls.py` | `model_calls` table DDL | `CREATE_MODEL_CALLS_TABLE` | See below. |
| `indexes.py` | Index DDL | `CREATE_INDEXES` | See below. |

## `__init__.py`
- `apply_schema(conn) -> None` — executes the ordered `_MIGRATIONS` (sessions
  table, then messages table, then model_calls table), then the guarded v1 → v2 migration (adds the
  nullable `activity_json` column to existing `messages` tables via
  `PRAGMA table_info` + `ALTER TABLE`), then `CREATE_INDEXES`, then commits.
  Idempotent on reopen.
- `SCHEMA_VERSION: int = 2` — a **code constant only**. There is no `schema_version`
  table or migration ledger in the database; do not claim version tracking exists
  in the DB. `_MIGRATIONS` is the ordered DDL list.

## `sessions.py`
- `CREATE_SESSIONS_TABLE` — `sessions` table: `session_id` PK, `title`, `model`,
  `created_at`, `updated_at`, `tokens_in/out/total`, `cost`, `tools_used`
  (JSON text, default `'[]'`).

## `messages.py`
- `CREATE_MESSAGES_TABLE` — `messages` table: `session_id`, `seq`, `role`,
  `content`, `activity_json` (nullable, versioned presentation metadata);
  primary key `(session_id, seq)`;
  `FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE`.

## `model_calls.py`
- `CREATE_MODEL_CALLS_TABLE` — `model_calls` table: one row per completed model
  invocation. `session_id`, `call_key`, `kind` (CHECK `IN ('chat','title')`),
  `assistant_seq` (nullable), `prompt_tokens` / `completion_tokens` (CHECK
  `>= 0`), `prompt_source` / `completion_source`, `recorded_at`
  (default `datetime('now')`); primary key `(session_id, call_key)`;
  `FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE`.
  Carries only token counts and provenance — no prompt text or file content.

## `indexes.py`
- `CREATE_INDEXES` — `idx_sessions_updated ON sessions(updated_at)`,
  `idx_messages_seq ON messages(session_id, seq)`. Support the list/search and
  ordered-message queries.
