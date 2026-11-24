"""Per-turn tracking of executed tool names (not model token usage)."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class UsageTracker:
    tools_used: list[str] = field(default_factory=list)

    def record_tool(self, tool_name: str) -> None:
        if tool_name not in self.tools_used:
            self.tools_used.append(tool_name)

    def flush(self, store, session_id: str) -> None:
        if self.tools_used:
            store.update_usage(session_id, tools_used=self.tools_used)
            self.tools_used = []
