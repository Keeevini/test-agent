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
    tool_call_id TEXT,
    activity_json TEXT,
    CHECK ((role = 'tool' AND tool_call_id IS NOT NULL AND length(tool_call_id) > 0)
           OR (role != 'tool' AND tool_call_id IS NULL)),
    PRIMARY KEY (session_id, seq),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
)
"""
