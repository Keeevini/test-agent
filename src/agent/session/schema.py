"""SQLite DDL and migration logic for the session store."""

SCHEMA_VERSION: int = 1

CREATE_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New Chat',
    model TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    tokens_total INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0.0,
    tools_used TEXT NOT NULL DEFAULT '[]'
)
"""

CREATE_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (session_id, seq),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
)
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_messages_seq ON messages(session_id, seq)",
]

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
