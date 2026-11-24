"""DDL for the ``messages`` table — conversation content.

Carries the conversation content as rows with a ``seq`` ordering
column. Mirrors OpenCode V2's ``session_message`` table.
"""

CREATE_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    activity_json TEXT,
    PRIMARY KEY (session_id, seq),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
)
"""
