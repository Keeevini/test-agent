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

    def get_messages(
        self,
        session_id: str,
        *,
        include_activity: bool = False,
    ) -> list[dict[str, Any]]:
        """Return all messages for a session, ordered by seq.

        Assistant messages stored as JSON are deserialized back to dicts
        so the LLM context can be rebuilt directly.

        ``include_activity`` controls whether the optional ``activity``
        presentation metadata is attached to each row. Tool rows can
        also carry a top-level ``tool_call_id`` (projected from version-1
        activity metadata, or inferred for legacy batches). The model-facing
        projection (``include_activity=False``) keeps the same
        ``tool_call_id`` but omits the ``activity`` field.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, role, content, activity_json FROM messages "
                "WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        activities = [self._parse_activity(r["activity_json"]) for r in rows]
        projected = [self._project_row(session_id, r, activity)
                     for r, activity in zip(rows, activities)]
        # Project explicit tool_call_id for tool rows, then run the read-only
        # legacy inference before attaching optional activity.
        self._attach_tool_call_ids(projected, activities)
        if include_activity:
            for msg, activity in zip(projected, activities):
                if activity is not None:
                    msg["activity"] = activity
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
    def _project_row(session_id, row, activity):
        # Activity-bearing tool rows store literal output, not a JSON envelope.
        # Rows from the context-only branch have no activity and retain the
        # legacy SessionMessage decoder. Never parse an activity row's output
        # as metadata (a read_file can contain arbitrary JSON).
        if row["role"] == "tool" and activity is not None:
            msg = {"role": "tool", "content": row["content"]}
            tcid = activity.get("tool_call_id")
            if isinstance(tcid, str) and tcid:
                msg["tool_call_id"] = tcid
            context_meta = activity.get("context_meta")
            if isinstance(context_meta, dict):
                msg["context_meta"] = context_meta
            return msg
        return SessionMessage(session_id=session_id, seq=row["seq"],
                              role=row["role"], content=row["content"]).to_dict()

    def _attach_tool_call_ids(
        self,
        projected: list[dict[str, Any]],
        activities: list[dict[str, Any] | None],
    ) -> None:
        """Project ``tool_call_id`` onto tool rows (explicit + legacy).

        Explicit IDs come from version-1 activity metadata and are never
        overwritten. Legacy batches (assistant rows without version-1
        activity whose contiguous tool rows lack IDs) are paired by position
        using only safe, unambiguous associations.
        """
        for msg, activity in zip(projected, activities):
            if msg["role"] == "tool" and activity is not None:
                tcid = activity.get("tool_call_id")
                if isinstance(tcid, str) and tcid:
                    msg["tool_call_id"] = tcid
        self._infer_legacy_tool_call_ids(projected, activities)

    def _infer_legacy_tool_call_ids(
        self,
        projected: list[dict[str, Any]],
        activities: list[dict[str, Any] | None],
    ) -> None:
        """Read-only inference of ``tool_call_id`` for legacy tool batches.

        For each assistant row and its immediately following contiguous
        run of tool rows, a legacy batch is one where the assistant has no
        version-1 activity metadata and all contiguous result rows lack
        explicit IDs. IDs are assigned in call order only when the
        association is unambiguous; otherwise rows are left intact.
        """
        n = len(projected)
        i = 0
        while i < n:
            if projected[i]["role"] != "assistant":
                i += 1
                continue
            # Find the contiguous run of tool rows immediately after this
            # assistant row (never crossing a user/assistant boundary).
            j = i + 1
            while j < n and projected[j]["role"] == "tool":
                j += 1
            if j > i + 1:
                batch = projected[i + 1 : j]
                self._pair_legacy_batch(projected[i], batch, activities[i])
            i = j

    def _pair_legacy_batch(
        self,
        assistant_msg: dict[str, Any],
        batch: list[dict[str, Any]],
        assistant_activity: dict[str, Any] | None,
    ) -> None:
        """Pair a legacy assistant row with its contiguous tool batch.

        Only invoked for batches where the assistant has no version-1
        activity metadata and all contiguous result rows lack IDs.
        """
        if assistant_activity is not None:
            return
        if any("tool_call_id" in m for m in batch):
            return
        calls = assistant_msg.get("tool_calls") or []
        call_ids = [
            c["id"]
            for c in calls
            if isinstance(c, dict)
            and isinstance(c.get("id"), str)
            and c["id"]
        ]
        # Safe inference 1: equal-size batch with unique nonempty call IDs.
        if (
            len(call_ids) == len(batch)
            and len(set(call_ids)) == len(call_ids)
        ):
            for m, cid in zip(batch, call_ids):
                m["tool_call_id"] = cid
            return
        # Safe inference 2 (narrower): all calls completed, and the number
        # of calls stamped `ran` equals the result count. Assign the ran
        # calls to the result rows in order.
        if batch and all(
            isinstance(c, dict)
            and isinstance(c.get("time"), dict)
            and "completed" in c["time"]
            for c in calls
        ):
            ran_calls = [
                c
                for c in calls
                if isinstance(c, dict)
                and isinstance(c.get("time"), dict)
                and "ran" in c["time"]
            ]
            if len(ran_calls) == len(batch):
                for m, c in zip(batch, ran_calls):
                    cid = c.get("id")
                    if isinstance(cid, str) and cid:
                        m["tool_call_id"] = cid

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str | dict[str, Any],
        *,
        activity: dict[str, Any] | None = None,
    ) -> int:
        """Append a message and return its sequence number.

        ``content`` may be a plain string (user/system) or a dict
        (assistant messages with tool_calls, etc.) which is JSON-encoded.
        ``activity`` is optional presentation metadata, stored in the
        ``activity_json`` column and kept separate from the model-facing
        ``content``. It is encoded only when non-None.
        """
        if isinstance(content, dict):
            content_str = json.dumps(content, ensure_ascii=False)
        else:
            content_str = content
        activity_str = None
        if activity is not None:
            activity_str = json.dumps(activity, ensure_ascii=False)
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
                "INSERT INTO messages "
                "(session_id, seq, role, content, activity_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, seq, role, content_str, activity_str),
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
        and ``messages`` (each a decoded stored message with optional activity plus
        ``seq`` and ``created_ms``).
        """
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            rows = self._conn.execute(
                "SELECT seq, role, content, activity_json FROM messages "
                "WHERE session_id = ? ORDER BY seq LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
        messages = []
        for r in rows:
            activity = self._parse_activity(r["activity_json"])
            msg = self._project_row(session_id, r, activity)
            if activity is not None:
                msg["activity"] = activity
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
