"""Message CRUD operations for the persistent store.

Operates on the ``messages`` table only — the conversation content rows
with a ``seq`` ordering column. Split from
:class:`.store.SessionStore` so message-storage changes can be made
independently of session-property storage.
"""

from __future__ import annotations

import json
from typing import Any

from .models import SessionMessage, _utcnow


class MessagesRepository:
    """Mixin providing message CRUD on the ``messages`` table."""

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

    def get_raw_message_page(
        self,
        session_id: str,
        offset: int,
        limit: int,
    ) -> dict:
        """Return a bounded, read-only page of raw stored messages.

        Returns a dict with ``total``, ``offset``, ``limit``, ``has_more``
        and ``messages`` (each a ``SessionMessage.to_dict()`` payload plus
        ``seq`` and ``created_ms``).
        """
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            rows = self._conn.execute(
                "SELECT seq, role, content FROM messages "
                "WHERE session_id = ? ORDER BY seq LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
        messages = []
        for r in rows:
            msg = SessionMessage(
                session_id=session_id,
                seq=r["seq"],
                role=r["role"],
                content=r["content"],
            ).to_dict()
            created_ms = None
            time_field = msg.get("time")
            if isinstance(time_field, dict):
                created_raw = time_field.get("created")
                if isinstance(created_raw, (int, float)):
                    created_ms = int(created_raw)
            messages.append({
                "seq": r["seq"],
                "role": r["role"],
                "created_ms": created_ms,
                "raw": msg,
            })
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(messages) < total,
            "messages": messages,
        }

    def update_message_content(
        self,
        session_id: str,
        seq: int,
        content: str | dict[str, Any],
    ) -> None:
        """Replace the content of an existing message (identified by ``seq``).

        Used to stamp timing metadata (e.g. ``time.completed``) onto an
        assistant message that was appended earlier in the turn.
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
