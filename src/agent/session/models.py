"""Data classes for session metadata and messages."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string (SQLite-compatible)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class SessionMeta:
    """Metadata for a single session, mirroring the sessions table row."""

    session_id: str
    title: str = "Untitled Session"
    model: str | None = None
    created_at: str = ""
    updated_at: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    cost: float = 0.0
    tools_used: list[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> SessionMeta:
        """Build a SessionMeta from a sqlite3 row (dict-mapped)."""
        tools_raw = row.get("tools_used", "[]")
        try:
            tools = json.loads(tools_raw) if isinstance(tools_raw, str) else []
        except (json.JSONDecodeError, TypeError):
            tools = []
        return cls(
            session_id=row["session_id"],
            title=row.get("title", "Untitled Session"),
            model=row.get("model"),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
            tokens_in=row.get("tokens_in", 0) or 0,
            tokens_out=row.get("tokens_out", 0) or 0,
            tokens_total=row.get("tokens_total", 0) or 0,
            cost=row.get("cost", 0.0) or 0.0,
            tools_used=tools if isinstance(tools, list) else [],
        )


@dataclass
class SessionMessage:
    """A single message row from the messages table."""

    session_id: str
    seq: int
    role: str
    content: str
    # For assistant messages, content is a JSON dict with keys like
    # content, reasoning_content, tool_calls.
    # For tool messages, content is the tool result string.
    # For user/system messages, content is plain text.

    def to_dict(self) -> dict[str, Any]:
        """Serialize the message to a dict suitable for the LLM API."""
        content_raw = self.content
        if self.role == "assistant":
            try:
                parsed = json.loads(content_raw)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
            return {"role": "assistant", "content": content_raw}
        if self.role == "tool":
            # Tool results are persisted as JSON carrying the durable
            # ``tool_call_id`` and ``context_meta``. Only trust a parsed value
            # that is a genuine tool object; otherwise fall back to legacy
            # raw text so old rows still replay without crashing.
            try:
                parsed = json.loads(content_raw)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if (
                isinstance(parsed, dict)
                and parsed.get("role") == "tool"
                and isinstance(parsed.get("content"), str)
                and isinstance(parsed.get("tool_call_id"), str)
            ):
                return parsed
            return {"role": "tool", "content": content_raw}
        return {"role": self.role, "content": content_raw}
