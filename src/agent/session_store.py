"""Async session store wrapper.

Wraps the persistent SQLite :class:`.session.store.SessionStore` with
per-process asyncio locks and system-prompt seeding. Split from
``core.py`` so session-management behavior can be developed
independently of the agent loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .prompt import SYSTEM_PROMPT
from .session.store import SessionStore as PersistentSessionStore


class SessionStore:
    """Async session store backed by a persistent SQLite database.

    Keeps asyncio locks in memory (per-process) and delegates all
    message and metadata storage to the persistent store.
    """

    def __init__(self, db_path: str) -> None:
        self._store = PersistentSessionStore(db_path)
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        """Return the message list for a session, seeding a system prompt.

        The first call for a session creates the session row and seeds
        the system prompt. Subsequent calls read from the database.
        """
        if not self._store.get_session(session_id):
            self._store.create_session(session_id)
        msgs = self._store.get_messages(session_id)
        if not msgs:
            self._store.append_message(session_id, "system", SYSTEM_PROMPT)
            msgs = self._store.get_messages(session_id)
        return msgs

    def append_message(
        self,
        session_id: str,
        role: str,
        content: Any,
        *,
        activity: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
    ) -> int:
        """Append a message to the session in the persistent store.

        ``activity`` is optional presentation metadata, forwarded to the
        persistent store and kept separate from the model-facing content.
        """
        return self._store.append_message(
            session_id, role, content, activity=activity, tool_call_id=tool_call_id
        )

    def append_assistant_with_usage(self, session_id: str, content: dict, usage: dict, *, activity=None) -> int:
        return self._store.append_assistant_with_usage(session_id, content, usage, activity=activity)

    def update_message_content(self, session_id: str, seq: int, content: Any) -> None:
        """Update the content of an existing message by its sequence number."""
        self._store.update_message_content(session_id, seq, content)

    def update_message_activity(
        self, session_id: str, seq: int, activity: dict[str, Any]
    ) -> None:
        """Update the presentation metadata of an existing message."""
        self._store.update_message_activity(session_id, seq, activity)

    def clear(self, session_id: str) -> None:
        self._store.delete_session(session_id)
        self._locks.pop(session_id, None)

    def close(self) -> None:
        """Close the underlying database connection."""
        self._store.close()

    # ------------------------------------------------------------------
    # Proxy methods for session management endpoints
    # ------------------------------------------------------------------

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list:
        return self._store.list_sessions(limit=limit, offset=offset)

    def get(self, session_id: str):
        return self._store.get_session(session_id)

    def create(self, session_id: str, title: str = "Untitled Session"):
        return self._store.create_session(session_id, title=title)

    def search(self, query: str, limit: int = 50) -> list:
        return self._store.search_sessions(query, limit=limit)

    def update_title(self, session_id: str, title: str) -> None:
        self._store.update_title(session_id, title)

    def delete(self, session_id: str) -> None:
        self._store.delete_session(session_id)
        self._locks.pop(session_id, None)

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Read-only history projection, including presentation metadata."""
        return self._store.get_messages(session_id, include_activity=True)

    def get_context_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Read-only canonical history with durable sequence markers."""
        return self._store.get_messages(session_id, include_activity=True, include_seq=True)

    def record_model_call(
        self,
        session_id: str,
        call_key: str,
        kind: str,
        assistant_seq: int | None,
        usage: dict[str, Any],
    ) -> bool:
        """Record one completed model invocation in the durable ledger."""
        return self._store.record_model_call(
            session_id, call_key, kind, assistant_seq, usage
        )

    def list_model_calls(self, session_id: str) -> list[dict[str, Any]]:
        """Return the session's model-call ledger rows, oldest first."""
        return self._store.list_model_calls(session_id)

    def get_raw_message_page(
        self, session_id: str, offset: int = 0, limit: int = 20
    ) -> dict:
        """Return a bounded, read-only page of raw stored messages."""
        return self._store.get_raw_message_page(session_id, offset, limit)
