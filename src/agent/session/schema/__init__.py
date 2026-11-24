"""SQLite canonical DDL and initialization logic for the session store.

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

SCHEMA_VERSION: int = 3


def apply_schema(conn) -> None:
    """Initialize only a fresh DB or reopen this exact schema; never migrate."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    expected = {"sessions", "messages", "model_calls"}
    if (version != SCHEMA_VERSION and (version != 0 or tables)) or (
        version == SCHEMA_VERSION and tables != expected
    ):
        raise RuntimeError("Incompatible sessions database; configure a fresh database for this rollout.")
    with conn:
        conn.execute("BEGIN")
        for ddl in (CREATE_SESSIONS_TABLE, CREATE_MESSAGES_TABLE, CREATE_MODEL_CALLS_TABLE, *CREATE_INDEXES):
            conn.execute(ddl)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


__all__ = [
    "SCHEMA_VERSION", "CREATE_SESSIONS_TABLE", "CREATE_MESSAGES_TABLE",
    "CREATE_MODEL_CALLS_TABLE", "CREATE_INDEXES", "apply_schema",
]
