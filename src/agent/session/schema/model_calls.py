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
    cached_prompt_tokens INTEGER CHECK (cached_prompt_tokens >= 0),
    reasoning_tokens INTEGER CHECK (reasoning_tokens >= 0),

    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, call_key),
    UNIQUE (session_id, assistant_seq),
    CHECK ((kind = 'chat' AND assistant_seq IS NOT NULL) OR (kind = 'title' AND assistant_seq IS NULL)),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id, assistant_seq) REFERENCES messages(session_id, seq) ON DELETE CASCADE
)
"""
