"""Approval gate for mutating tools.

Holds the in-process approval manager that gates mutating filesystem
tools behind browser approval. Split from ``core.py`` so the approval
flow can be developed independently of the agent loop and model client.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingApproval:
    approval_id: str
    session_id: str
    tool_name: str
    arguments: dict[str, Any]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: bool | None = None


class ApprovalManager:
    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._lock = asyncio.Lock()

    async def create(self, session_id: str, tool_name: str, arguments: dict[str, Any]) -> PendingApproval:
        item = PendingApproval(
            approval_id=str(uuid.uuid4()),
            session_id=session_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        async with self._lock:
            self._pending[item.approval_id] = item
        return item

    async def resolve(self, approval_id: str, approved: bool) -> bool:
        async with self._lock:
            item = self._pending.get(approval_id)
            if item is None:
                return False
            item.approved = approved
            item.event.set()
            return True

    async def wait(self, item: PendingApproval) -> bool:
        await item.event.wait()
        async with self._lock:
            self._pending.pop(item.approval_id, None)
        return bool(item.approved)
