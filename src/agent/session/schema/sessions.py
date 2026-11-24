"""DDL for the ``sessions`` table — session-level properties.

Carries session-level metadata: title, model, cost,
tools used, and timestamps. Mirrors OpenCode V2's ``session_v2`` table.
"""

CREATE_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'Untitled Session',
    model TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    cost REAL NOT NULL DEFAULT 0.0,
    tools_used TEXT NOT NULL DEFAULT '[]'
)
"""
