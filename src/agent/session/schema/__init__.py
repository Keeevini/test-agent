"""SQLite DDL and migration logic for the session store.

The DDL is split into per-table modules so each table's schema can be
maintained independently:

- :mod:`.sessions` — the ``sessions`` table (session-level properties).
- :mod:`.messages` — the ``messages`` table (conversation content).
- :mod:`.model_calls` — the ``model_calls`` table (per-model-call usage ledger).
- :mod:`.indexes`  — index definitions.
"""

from .messages import CREATE_MESSAGES_TABLE
from .model_calls import CREATE_MODEL_CALLS_TABLE
from .sessions import CREATE_SESSIONS_TABLE
from .indexes import CREATE_INDEXES

SCHEMA_VERSION: int = 2

# Ordered list of DDL statements to execute in sequence.
_MIGRATIONS: list[tuple[int, str]] = [
    (1, CREATE_SESSIONS_TABLE),
    (1, CREATE_MESSAGES_TABLE),
    (1, CREATE_MODEL_CALLS_TABLE),
]


def apply_schema(conn) -> None:
    """Apply all schema DDL statements to the connection.

    Version 2 adds the nullable ``activity_json`` column to the
    ``messages`` table for existing databases. ``CREATE TABLE IF NOT
    EXISTS`` does not alter old tables, so the column is added with a
    guarded ``ALTER TABLE`` after the table DDL. New databases already
    include the column in ``CREATE_MESSAGES_TABLE``.
    """
    for _version, ddl in _MIGRATIONS:
        conn.execute(ddl)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "activity_json" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN activity_json TEXT")
    for idx_ddl in CREATE_INDEXES:
        conn.execute(idx_ddl)
    conn.commit()


__all__ = [
    "SCHEMA_VERSION",
    "CREATE_SESSIONS_TABLE",
    "CREATE_MESSAGES_TABLE",
    "CREATE_MODEL_CALLS_TABLE",
    "CREATE_INDEXES",
    "apply_schema",
]
