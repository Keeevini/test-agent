"""SQLite-backed persistent session store.

Wraps the built-in ``sqlite3`` module with a thread-safe connection and
exposes CRUD operations for sessions and their messages.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import SessionMeta, SessionMessage
from .schema import SCHEMA_VERSION, apply_schema


class SessionStore:
    """Persistent session store backed by a single SQLite database file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_db()

    def _init_db(self) -> None:
        apply_schema(self._conn)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def create_session(
        self,
        session_id: str,
        title: str = "New Chat",
        model: str | None = None,
    ) -> SessionMeta:
        """Create a new session row. Idempotent — overwrites if it exists."""
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions "
                "(session_id, title, model, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, title, model, now, now),
            )
            self._conn.commit()
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionMeta | None:
        """Return session metadata or None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionMeta.from_row(dict(row))

    def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionMeta]:
        """Return sessions ordered by most recently updated first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC "
                "LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [SessionMeta.from_row(dict(r)) for r in rows]

    def search_sessions(
        self,
        query: str,
        limit: int = 50,
    ) -> list[SessionMeta]:
        """Search sessions by title, session_id, or model.

        Matches case-insensitively using SQL LIKE.
        """
        like = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions "
                "WHERE title LIKE ? COLLATE nocase "
                "OR session_id LIKE ? COLLATE nocase "
                "OR model LIKE ? COLLATE nocase "
                "ORDER BY updated_at DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
        return [SessionMeta.from_row(dict(r)) for r in rows]

    def update_title(self, session_id: str, title: str) -> None:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
                (title, now, session_id),
            )
            self._conn.commit()

    def delete_session(self, session_id: str) -> None:
        """Delete a session and its messages (CASCADE)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Message CRUD
    # ------------------------------------------------------------------

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Return all messages for a session, ordered by seq.

        Assistant messages stored as JSON are deserialized back to dicts
        so the LLM context can be rebuilt directly.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, role, content FROM messages "
                "WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        return [
            SessionMessage(
                session_id=session_id,
                seq=r["seq"],
                role=r["role"],
                content=r["content"],
            ).to_dict()
            for r in rows
        ]

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str | dict[str, Any],
    ) -> int:
        """Append a message and return its sequence number.

        ``content`` may be a plain string (user/system) or a dict
        (assistant messages with tool_calls, etc.) which is JSON-encoded.
        """
        if isinstance(content, dict):
            content_str = json.dumps(content, ensure_ascii=False)
        else:
            content_str = content
        now = _utcnow()
        with self._lock:
            # Get the next seq number
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq "
                "FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = row["next_seq"]
            self._conn.execute(
                "INSERT INTO messages (session_id, seq, role, content) "
                "VALUES (?, ?, ?, ?)",
                (session_id, seq, role, content_str),
            )
            # Touch the session's updated_at
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            self._conn.commit()
        return seq

    def update_message_content(
        self,
        session_id: str,
        seq: int,
        content: str | dict[str, Any],
    ) -> None:
        """Replace the content of an existing message (identified by ``seq``).

        Used to stamp timing metadata (e.g. ``time.completed``) onto an
        assistant message that was appended earlier in the same turn.
        """
        if isinstance(content, dict):
            content_str = json.dumps(content, ensure_ascii=False)
        else:
            content_str = content
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET content = ? "
                "WHERE session_id = ? AND seq = ?",
                (content_str, session_id, seq),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    def update_usage(
        self,
        session_id: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost: float = 0.0,
        tools_used: list[str] | None = None,
    ) -> None:
        """Atomically accumulate usage counters for a session.

        All parameters are additive. If ``tools_used`` is provided,
        the tool names are merged into the existing set (no duplicates).
        """
        now = _utcnow()
        with self._lock:
            # Read current tools_used
            row = self._conn.execute(
                "SELECT tools_used FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            tools_json = "[]"
            if row is not None and row["tools_used"]:
                tools_json = row["tools_used"]

            new_tools = tools_json
            if tools_used:
                try:
                    existing = json.loads(tools_json)
                    if not isinstance(existing, list):
                        existing = []
                    for t in tools_used:
                        if t not in existing:
                            existing.append(t)
                    new_tools = json.dumps(existing, ensure_ascii=False)
                except (json.JSONDecodeError, TypeError):
                    new_tools = json.dumps(tools_used, ensure_ascii=False)

            self._conn.execute(
                "UPDATE sessions SET "
                "tokens_in = tokens_in + ?, "
                "tokens_out = tokens_out + ?, "
                "tokens_total = tokens_total + ?, "
                "cost = cost + ?, "
                "tools_used = ?, "
                "updated_at = ? "
                "WHERE session_id = ?",
                (tokens_in, tokens_out, tokens_in + tokens_out, cost,
                 new_tools, now, session_id),
            )
            self._conn.commit()

    def set_model(self, session_id: str, model: str) -> None:
        """Set the model name on a session."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET model = ? WHERE session_id = ?",
                (model, session_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying database connection."""
        with self._lock:
            self._conn.close()


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string (SQLite-compatible)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
