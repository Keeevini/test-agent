"""Index DDL for the session store.

Indexes support the list/search/ordered-message queries used by the
data access layer.
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_messages_seq ON messages(session_id, seq)",
]
