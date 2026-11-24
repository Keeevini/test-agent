"""Process-level configuration; no file I/O or per-session model selection."""

def parse_context_limit(value: str | None) -> int:
    if value is None:
        return 16384
    try:
        limit = int(value)
    except ValueError:
        raise ValueError("MAX_CONTEXT_TOKENS must be a positive integer") from None
    if limit <= 0:
        raise ValueError("MAX_CONTEXT_TOKENS must be a positive integer")
    return limit
