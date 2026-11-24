"""Session CRUD operations for the persistent store.

Operates on the ``sessions`` table only — session-level properties such
as title, model, token/cost counters, tools used, and timestamps. Split
from :class:`.store.SessionStore` so session-property changes can be made
independently of message storage.
"""

from __future__ import annotations

import json
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

    def _select_metadata(self, selection: str, params: tuple) -> list[SessionMeta]:
        """Aggregate only the selected session page; ledger is the token authority."""
        with self._lock:
            rows = self._conn.execute(
                f"WITH selected AS ({selection}) "
                "SELECT s.*, COALESCE(SUM(c.prompt_tokens), 0) AS tokens_in, "
                "COALESCE(SUM(c.completion_tokens), 0) AS tokens_out, "
                "COALESCE(SUM(c.prompt_tokens + c.completion_tokens), 0) AS tokens_total "
                "FROM selected s LEFT JOIN model_calls c ON c.session_id = s.session_id "
                "GROUP BY s.session_id ORDER BY s.updated_at DESC, s.session_id", params,
            ).fetchall()
        return [SessionMeta.from_row(dict(row)) for row in rows]

    def get_session(self, session_id: str) -> SessionMeta | None:
        rows = self._select_metadata("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        return rows[0] if rows else None

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[SessionMeta]:
        return self._select_metadata(
            "SELECT * FROM sessions ORDER BY updated_at DESC, session_id LIMIT ? OFFSET ?", (limit, offset),
        )

    def search_sessions(self, query: str, limit: int = 50) -> list[SessionMeta]:
        like = f"%{query}%"
        return self._select_metadata(
            "SELECT * FROM sessions WHERE title LIKE ? COLLATE nocase "
            "OR session_id LIKE ? COLLATE nocase OR model LIKE ? COLLATE nocase "
            "ORDER BY updated_at DESC, session_id LIMIT ?", (like, like, like, limit),
        )

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
        cost: float = 0.0,
        tools_used: list[str] | None = None,
    ) -> None:
        """Atomically accumulate usage counters for a session.

        Token totals are not writable here. If ``tools_used`` is provided,
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
                "cost = cost + ?, "
                "tools_used = ?, "
                "updated_at = ? "
                "WHERE session_id = ?",
                (cost, new_tools, now, session_id),
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

    @staticmethod
    def _validate_model_usage(usage: dict[str, Any]) -> tuple:
        required = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
        sources = (usage.get("prompt_source"), usage.get("completion_source"))
        details = (usage.get("cached_prompt_tokens"), usage.get("reasoning_tokens"))
        if any(type(v) is not int or v < 0 for v in required):
            raise ValueError("usage token counts must be non-negative ints")
        if any(v not in ("provider", "estimated") for v in sources):
            raise ValueError("invalid usage provenance")
        if any(v is not None and (type(v) is not int or v < 0) for v in details):
            raise ValueError("invalid optional usage token count")
        return (*required, *sources, *details)

    def _insert_model_call_locked(self, session_id, call_key, kind, assistant_seq, values) -> bool:
        """Caller owns lock and transaction. Only duplicate call_key is ignored."""
        now = _utcnow()
        cur = self._conn.execute(
            "INSERT INTO model_calls (session_id, call_key, kind, assistant_seq, "
            "prompt_tokens, completion_tokens, prompt_source, completion_source, "
            "cached_prompt_tokens, reasoning_tokens, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(session_id, call_key) DO NOTHING",
            (session_id, call_key, kind, assistant_seq, *values, now),
        )
        if cur.rowcount:
            self._conn.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))
        return bool(cur.rowcount)

    def record_model_call(self, session_id: str, call_key: str, kind: str,
                          assistant_seq: int | None, usage: dict[str, Any]) -> bool:
        """Title-only write. Chat usage is atomic with append_assistant_with_usage."""
        if kind != "title" or assistant_seq is not None or call_key != "title":
            raise ValueError("Chat calls must be recorded atomically with their assistant message")
        values = self._validate_model_usage(usage)
        with self._lock, self._conn:
            if not self._conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone():
                return False
            return self._insert_model_call_locked(session_id, call_key, kind, assistant_seq, values)

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
