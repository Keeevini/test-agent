"""Session CRUD operations for the persistent store.

Operates on the ``sessions`` table only — session-level properties such
as title, model, token/cost counters, tools used, and timestamps. Split
from :class:`.store.SessionStore` so session-property changes can be made
independently of message storage.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .models import SessionMeta, _utcnow


class SessionsRepository:
    """Mixin providing session CRUD on the ``sessions`` table."""

    def create_session(
        self,
        session_id: str,
        title: str = "Untitled Session",
        model: str | None = None,
    ) -> SessionMeta:
        """Create a missing session without modifying an existing one.

        Repeated creates must preserve messages, title, usage, and timestamps.
        """
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions "
                "(session_id, title, model, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO NOTHING",
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
    # Per-model-call usage ledger
    # ------------------------------------------------------------------

    def record_model_call(
        self,
        session_id: str,
        call_key: str,
        kind: str,
        assistant_seq: int | None,
        usage: dict[str, Any],
    ) -> bool:
        """Record one completed model invocation in the ``model_calls`` ledger.

        Idempotent per ``(session_id, call_key)``: a duplicate key is ignored
        and the lifetime ``sessions`` counters are incremented **only** when a
        row is actually inserted, so repeated recordings never double count.
        A missing session (foreign-key) is a graceful no-op returning ``False``
        — it never recreates the session.

        ``usage`` is the finalized provider-usage dict from
        ``ModelClient.stream`` carrying ``prompt_tokens`` / ``completion_tokens``
        (ints >= 0) and ``prompt_source`` / ``completion_source`` (strings).
        """
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        prompt_source = usage.get("prompt_source")
        completion_source = usage.get("completion_source")
        # Validate before touching the connection.
        for value in (prompt_tokens, completion_tokens):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("usage token counts must be non-negative ints")
        if not isinstance(prompt_source, str) or not isinstance(completion_source, str):
            raise ValueError("usage sources must be strings")

        now = _utcnow()
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO model_calls "
                    "(session_id, call_key, kind, assistant_seq, "
                    " prompt_tokens, completion_tokens, "
                    " prompt_source, completion_source, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        call_key,
                        kind,
                        assistant_seq,
                        prompt_tokens,
                        completion_tokens,
                        prompt_source,
                        completion_source,
                        now,
                    ),
                )
                inserted = cur.rowcount > 0
            except sqlite3.IntegrityError:
                # FK violation: the session no longer exists.
                inserted = False
            if inserted:
                # Increment lifetime counters exactly once, only on insert.
                self._conn.execute(
                    "UPDATE sessions SET "
                    "tokens_in = tokens_in + ?, "
                    "tokens_out = tokens_out + ?, "
                    "tokens_total = tokens_total + ?, "
                    "updated_at = ? "
                    "WHERE session_id = ?",
                    (
                        prompt_tokens,
                        completion_tokens,
                        prompt_tokens + completion_tokens,
                        now,
                        session_id,
                    ),
                )
            self._conn.commit()
            return inserted

    def list_model_calls(self, session_id: str) -> list[dict[str, Any]]:
        """Return the session's ``model_calls`` ledger rows, oldest first.

        Rows are ordered by ``recorded_at`` then ``assistant_seq``. Callers that
        need "the latest chat call" should select by ``kind == 'chat'`` and the
        maximum ``assistant_seq`` — never by same-second timestamps.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM model_calls "
                "WHERE session_id = ? "
                "ORDER BY recorded_at, assistant_seq",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]
