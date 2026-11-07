from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a string.

    Uses a character-based heuristic (~4 chars per token for mixed text).
    This is a budgeting estimate, not an exact tokenizer count.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def count_message_tokens(messages: list[dict]) -> int:
    """Estimate total tokens across a list of chat messages.

    Accounts for content, tool calls, reasoning, and per-message overhead.
    """
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        total += estimate_tokens(msg.get("reasoning_content") or "")
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            total += estimate_tokens(fn.get("name", ""))
            total += estimate_tokens(fn.get("arguments", ""))
        total += 4  # per-message overhead (role, separators)
    return total
