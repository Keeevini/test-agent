"""SQLite DDL and migration logic for the session store.

The DDL is split into per-table modules so each table's schema can be
maintained independently:

- :mod:`.sessions` — the ``sessions`` table (session-level properties).
- :mod:`.messages` — the ``messages`` table (conversation content).
- :mod:`.indexes`  — index definitions.
"""

from .messages import CREATE_MESSAGES_TABLE
from .sessions import CREATE_SESSIONS_TABLE
from .indexes import CREATE_INDEXES

SCHEMA_VERSION: int = 1

# Ordered list of DDL statements to execute in sequence.
_MIGRATIONS: list[tuple[int, str]] = [
    (1, CREATE_SESSIONS_TABLE),
    (1, CREATE_MESSAGES_TABLE),
]


def apply_schema(conn) -> None:
    """Apply all schema DDL statements to the connection."""
    for _version, ddl in _MIGRATIONS:
        conn.execute(ddl)
    for idx_ddl in CREATE_INDEXES:
        conn.execute(idx_ddl)
    conn.commit()


__all__ = [
    "SCHEMA_VERSION",
    "CREATE_SESSIONS_TABLE",
    "CREATE_MESSAGES_TABLE",
    "CREATE_INDEXES",
    "apply_schema",
]
