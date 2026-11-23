"""DDL for the ``model_calls`` table — durable per-model-call usage ledger.

Carries one row per completed model invocation (a chat assistant turn or a
title request) with prompt/completion token counts and per-field provenance.
This keeps **lifetime** usage durable and idempotent, separate from the
**active chat context** (which is derived from the latest chat call in
Stage 03).

No prompt text or file content is duplicated here — only the token counts
and their provenance.
"""

CREATE_MODEL_CALLS_TABLE = """
CREATE TABLE IF NOT EXISTS model_calls (
    session_id TEXT NOT NULL,
    call_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('chat', 'title')),
    assistant_seq INTEGER,
    prompt_tokens INTEGER NOT NULL CHECK (prompt_tokens >= 0),
    completion_tokens INTEGER NOT NULL CHECK (completion_tokens >= 0),
    prompt_source TEXT NOT NULL,
    completion_source TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, call_key),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
)
"""
