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
- `apply_schema(conn)` — initialize an empty DB transactionally, or reopen the exact version. Reject incompatible DBs without migration or deletion. `SCHEMA_VERSION = 3`, recorded in `PRAGMA user_version`.

## `sessions.py`
- `CREATE_SESSIONS_TABLE` — `sessions` table: `session_id` PK, `title`, `model`,
  `created_at`, `updated_at`, `tokens_in/out/total`, `cost`, `tools_used`
  (JSON text, default `'[]'`).

## `messages.py`
- `CREATE_MESSAGES_TABLE` — `messages` table: `session_id`, `seq`, `role`,
  `content`, `tool_call_id` (required for tools, null otherwise), `activity_json` (nullable, versioned presentation metadata);
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
