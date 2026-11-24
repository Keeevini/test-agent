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
from .tool_links import associate_tool_results


class MessagesRepository:
    """Mixin providing message CRUD on the ``messages`` table."""

    def get_messages(
        self, session_id: str, *, include_activity: bool = False,
        include_seq: bool = False,
    ) -> list[dict[str, Any]]:
        """Return chronological canonical rows; metadata only when requested."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, role, content, tool_call_id, activity_json FROM messages "
                "WHERE session_id = ? ORDER BY seq", (session_id,),
            ).fetchall()
        projected = []
        for row in rows:
            msg = self._project_row(session_id, row)
            if include_activity:
                activity = self._parse_activity(row["activity_json"])
                if activity is not None:
                    msg["activity"] = activity
            if include_seq:
                msg["seq"] = row["seq"]
            projected.append(msg)
        return projected

    @staticmethod
    def _parse_activity(raw: str | None) -> dict[str, Any] | None:
        """Parse an ``activity_json`` value into a version-1 metadata dict.

        Returns None for NULL, malformed, non-dict, or non-version-1 values.
        The original message text is never touched by this.
        """
        if raw is None:
            return None
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(parsed, dict) and parsed.get("version") == 1:
            return parsed
        return None

    @staticmethod
    def _project_row(session_id, row):
        msg = SessionMessage(session_id=session_id, seq=row["seq"],
                             role=row["role"], content=row["content"]).to_dict()
        if row["role"] == "tool":
            msg["tool_call_id"] = row["tool_call_id"]
        return msg

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str | dict[str, Any],
        *,
        activity: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
    ) -> int:
        """Append a message and return its sequence number.

        ``content`` may be a plain string (user/system) or a dict
        (assistant messages with tool_calls, etc.) which is JSON-encoded.
        ``activity`` is optional presentation metadata, stored in the
        ``activity_json`` column and kept separate from the model-facing
        ``content``. It is encoded only when non-None.
        """
        if role == "tool":
            if not isinstance(tool_call_id, str) or not tool_call_id or not isinstance(content, str):
                raise ValueError("Tool rows require literal content and a nonempty tool_call_id")
        elif tool_call_id is not None:
            raise ValueError("Only tool rows may have a tool_call_id")
        with self._lock, self._conn:
            return self._append_message_locked(session_id, role, content, activity, tool_call_id)

    def _append_message_locked(self, session_id, role, content, activity, tool_call_id=None) -> int:
        """Caller owns lock/transaction; no commit inside this helper."""
        if role == "assistant" and isinstance(content, dict) and "usage" in content:
            raise ValueError("Assistant usage belongs in model_calls")
        content_str = json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else content
        activity_str = json.dumps(activity, ensure_ascii=False) if activity is not None else None
        seq = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()["next_seq"]
        self._conn.execute(
            "INSERT INTO messages (session_id, seq, role, content, tool_call_id, activity_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, seq, role, content_str, tool_call_id, activity_str),
        )
        self._conn.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (_utcnow(), session_id))
        return seq

    def append_assistant_with_usage(self, session_id: str, content: dict, usage: dict, *, activity=None) -> int:
        """Commit one assistant and exactly one completed chat call together."""
        values = self._validate_model_usage(usage)
        with self._lock, self._conn:
            seq = self._append_message_locked(session_id, "assistant", content, activity)
            if not self._insert_model_call_locked(session_id, f"chat:{seq}", "chat", seq, values):
                raise RuntimeError("Duplicate chat ledger key")
            return seq

    def get_raw_message_page(
        self,
        session_id: str,
        offset: int,
        limit: int,
    ) -> dict:
        """Return a bounded, read-only page of raw stored messages.

        Returns a dict with ``total``, ``offset``, ``limit``, ``has_more``
        and ``messages`` (each a decoded stored message with optional activity plus
        ``seq`` and ``created_ms``).
        """
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            rows = self._conn.execute(
                "SELECT seq, role, content, tool_call_id, activity_json FROM messages "
                "WHERE session_id = ? ORDER BY seq LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
            # A page can start mid-tool-batch. Recover at most its preceding
            # non-tool row (not an unbounded history read).
            prefix = None
            if rows and rows[0]["role"] == "tool":
                prefix = self._conn.execute(
                    "SELECT seq, role, content, tool_call_id, activity_json FROM messages "
                    "WHERE session_id = ? AND seq < ? AND role != 'tool' "
                    "ORDER BY seq DESC LIMIT 1", (session_id, rows[0]["seq"]),
                ).fetchone()
            usage_by_seq = {}
            if rows:
                for usage in self._conn.execute(
                    "SELECT assistant_seq, prompt_tokens, completion_tokens, prompt_source, completion_source, "
                    "cached_prompt_tokens, reasoning_tokens FROM model_calls "
                    "WHERE session_id = ? AND kind = 'chat' AND assistant_seq BETWEEN ? AND ?",
                    (session_id, rows[0]["seq"], rows[-1]["seq"]),
                ):
                    data = dict(usage)
                    usage_by_seq[data.pop("assistant_seq")] = data
        projected = [self._project_row(session_id, r) for r in rows]
        preceding = [self._project_row(session_id, prefix)] if prefix else []
        links = associate_tool_results(preceding + projected)
        messages = []
        for index, (r, msg) in enumerate(zip(rows, projected)):
            activity = self._parse_activity(r["activity_json"])
            if activity is not None:
                msg["activity"] = activity
            if r["role"] == "assistant" and r["seq"] in usage_by_seq:
                msg["usage"] = usage_by_seq[r["seq"]]
            created_ms = None
            time_field = msg.get("time")
            if isinstance(time_field, dict):
                created_raw = time_field.get("created")
                if isinstance(created_raw, (int, float)):
                    created_ms = int(created_raw)
            linked = links.get(index + len(preceding), {})
            function = linked.get("function")
            name = function.get("name") if isinstance(function, dict) else None
            entry = {
                "seq": r["seq"],
                "role": r["role"],
                "created_ms": created_ms,
                "raw": msg,
            }
            if isinstance(name, str) and name:
                entry["tool_name"] = name
            messages.append(entry)
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
            if "usage" in content:
                raise ValueError("Assistant usage belongs in model_calls")
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

    def update_message_activity(
        self,
        session_id: str,
        seq: int,
        activity: dict[str, Any],
    ) -> None:
        """Replace the presentation metadata of an existing message.

        Only the ``activity_json`` column is touched; ``content`` and all
        other columns are left untouched.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET activity_json = ? "
                "WHERE session_id = ? AND seq = ?",
                (json.dumps(activity, ensure_ascii=False), session_id, seq),
            )
            self._conn.commit()
