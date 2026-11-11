"""Per-session usage tracking: tokens, cost, and tools used.

Accumulates usage counters for a session during a single agent turn.
The accumulated values are flushed to the store at the end of the turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UsageTracker:
    """Accumulates usage counters for a single agent turn.

    Call :meth:`record_tokens` after each model response and
    :meth:`record_tool` after each tool execution. Then call
    :meth:`flush` to persist the totals to the session store.
    """

    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    tools_used: list[str] = field(default_factory=list)

    def record_tokens(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Record token usage from a single model response."""
        self.tokens_in += prompt_tokens
        self.tokens_out += completion_tokens

    def record_tool(self, tool_name: str) -> None:
        """Record that a tool was used (deduplicated)."""
        if tool_name not in self.tools_used:
            self.tools_used.append(tool_name)

    def flush(
        self,
        store,
        session_id: str,
    ) -> None:
        """Persist accumulated usage to the session store.

        Resets the tracker after flushing.
        """
        store.update_usage(
            session_id,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            cost=self.cost,
            tools_used=self.tools_used,
        )
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost = 0.0
        self.tools_used = []
