"""Approval gate for mutating tools.

Holds the in-process approval manager that gates mutating filesystem
tools behind browser approval. Split from ``core.py`` so the approval
flow can be developed independently of the agent loop and model client.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PendingApproval:
    approval_id: str
    session_id: str
    tool_name: str
    arguments: dict[str, Any]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: bool | None = None
    # Optional synchronous persistence callback, invoked by ``resolve``
    # before the decision is written. Must be synchronous, small, and
    # must not await. Exceptions propagate so the HTTP POST is not
    # acknowledged if persistence fails.
    on_decision: Callable[[PendingApproval, bool], None] | None = None


class ApprovalManager:
    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        on_decision: Callable[[PendingApproval, bool], None] | None = None,
    ) -> PendingApproval:
        item = PendingApproval(
            approval_id=str(uuid.uuid4()),
            session_id=session_id,
            tool_name=tool_name,
            arguments=arguments,
            on_decision=on_decision,
        )
        async with self._lock:
            self._pending[item.approval_id] = item
        return item

    async def resolve(self, approval_id: str, approved: bool) -> bool:
        """Record a decision for ``approval_id``.

        First-wins: returns False if the item is absent **or** already
        decided. The persistence callback (if any) is invoked before the
        decision is written, so a failed persistence does not acknowledge
        the POST. The decision is then written and the event set.
        """
        async with self._lock:
            item = self._pending.get(approval_id)
            if item is None or item.approved is not None:
                return False
            if item.on_decision is not None:
                item.on_decision(item, approved)
            item.approved = approved
            item.event.set()
            return True

    async def wait(self, item: PendingApproval) -> bool:
        """Await the decision event and return the approved bool.

        Core owns removal via :meth:`discard` (in a ``finally`` block), so
        this method no longer pops the pending item.
        """
        await item.event.wait()
        return bool(item.approved)

    def discard(self, item: PendingApproval) -> None:
        """Remove the pending item if it is still the identical item.

        Synchronous (single asyncio loop; the dict operation is atomic
        because there is no internal await). Removes only the identical
        item and is idempotent. Core calls this in a ``finally`` block so
        the pending item is cleaned up even if the generator is closed.
        """
        current = self._pending.get(item.approval_id)
        if current is item:
            del self._pending[item.approval_id]
