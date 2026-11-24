"""Associate persisted result rows with the immediately preceding assistant batch."""
from __future__ import annotations


def associate_tool_results(messages: list[dict]) -> dict[int, dict]:
    """Return result-list-index -> unique assistant call; never infer by position."""
    batch: dict[str, dict] = {}
    linked: dict[int, dict] = {}
    for index, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            batch = {}
            duplicates: set[str] = set()
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                cid = call.get("id")
                if not isinstance(cid, str) or not cid:
                    continue
                if cid in batch or cid in duplicates:
                    batch.pop(cid, None)
                    duplicates.add(cid)
                else:
                    batch[cid] = call
        elif role == "tool":
            cid = msg.get("tool_call_id")
            if isinstance(cid, str) and cid in batch:
                linked[index] = batch[cid]
        else:
            batch = {}
    return linked
