"""Read-only context analytics for a single session.

``build_context_summary`` is a pure function: it takes the session metadata,
its message rows, its ``model_calls`` ledger rows, and configured constants,
then returns a deterministic dict describing the active context occupancy,
a mutually exclusive estimated token breakdown, loaded-file rows, and
provenance labels.  No I/O, no filesystem access, no mutation.
"""

from __future__ import annotations

import json
from typing import Any

from .prompt import SYSTEM_PROMPT
from .session.tool_links import associate_tool_results
from .token_counter import count_message_tokens, estimate_tokens


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _latest_chat_call(calls: list[dict]) -> dict | None:
    """Return the durable ``kind='chat'`` call with the max ``assistant_seq``.

    Title calls are excluded.  Returns ``None`` when no chat call exists.
    """
    chat_calls = [c for c in calls if c.get("kind") == "chat"]
    if not chat_calls:
        return None
    return max(chat_calls, key=lambda c: c.get("assistant_seq") or 0)


def _last_assistant_index(messages: list[dict]) -> int | None:
    """Index of the last assistant row, scanning backward.

    ``get_messages`` strips the DB ``seq``, so we scan the ordered list
    backward for the last ``role == 'assistant'`` row.
    """
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            return i
    return None


def _file_paths(messages: list[dict]) -> dict[int, str]:
    result = {}
    for index, call in associate_tool_results(messages).items():
        activity = messages[index].get("activity")
        function = call.get("function") or {}
        if not isinstance(activity, dict) or activity.get("ok") is not True or function.get("name") != "read_file":
            continue
        try:
            args = json.loads(function.get("arguments", ""))
        except (ValueError, TypeError):
            continue
        path = args.get("path") if isinstance(args, dict) else None
        if isinstance(path, str) and path:
            result[index] = path
    return result


def _extract_loaded_files(messages: list[dict]) -> list[dict]:
    files: dict[str, dict] = {}
    for index, path in _file_paths(messages).items():
        content = messages[index].get("content")
        row = files.setdefault(path, {"path": path, "tokens": 0, "occurrences": 0, "source": "estimated"})
        row["tokens"] += estimate_tokens(content if isinstance(content, str) else "")
        row["occurrences"] += 1
    return list(files.values())


def _category_weights(
    messages: list[dict],
    tool_specs: list,
    loaded_files: list[dict],
) -> dict[str, int]:
    """Provisional per-category token estimates (all estimated).

    Returns a dict with keys: previous_compaction, user_input,
    loaded_files, model_reasoning, tool_usage, other.
    """
    file_token_sum = sum(f["tokens"] for f in loaded_files)

    user_input = 0
    model_reasoning = 0
    tool_usage = 0
    other = 0

    file_paths = _file_paths(messages)
    for index, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")
        content_str = content if isinstance(content, str) else ""

        if role == "system":
            other += estimate_tokens(content_str)
        elif role == "user":
            user_input += estimate_tokens(content_str)
        elif role == "assistant":
            other += estimate_tokens(content_str)
            other += estimate_tokens(msg.get("reasoning_content") or "")
            model_reasoning += estimate_tokens(msg.get("reasoning_content") or "")
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                tool_usage += estimate_tokens(fn.get("name", ""))
                tool_usage += estimate_tokens(fn.get("arguments", ""))
        elif role == "tool":
            # Skip file content (counted in loaded_files)
            is_file = index in file_paths
            if is_file:
                pass  # already in file_token_sum
            else:
                tool_usage += estimate_tokens(content_str)

    # One set of tool definitions (serialized specs).
    if tool_specs:
        specs_json = json.dumps(
            [s.as_openai() for s in tool_specs],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        tool_usage += estimate_tokens(specs_json)

    # Per-message overhead.
    other += 4 * len(messages)

    return {
        "previous_compaction": 0,
        "user_input": user_input,
        "loaded_files": file_token_sum,
        "model_reasoning": model_reasoning,
        "tool_usage": tool_usage,
        "other": other,
    }


def _reconcile(
    weights: dict[str, int],
    used_tokens: int,
) -> dict[str, int]:
    """Force category weights to sum exactly to ``used_tokens``.

    If sum < used: remainder goes to ``other``.
    If sum > used: scale proportionally with largest-remainder rounding.
    """
    keys = [
        "previous_compaction", "user_input", "loaded_files",
        "model_reasoning", "tool_usage", "other",
    ]
    total = sum(weights[k] for k in keys)

    if total <= used_tokens:
        result = dict(weights)
        result["other"] += used_tokens - total
        return result

    # Scale proportionally.
    if total == 0:
        result = {k: 0 for k in keys}
        result["other"] = used_tokens
        return result

    scaled: dict[str, float] = {}
    for k in keys:
        scaled[k] = weights[k] * used_tokens / total

    # Integer floor.
    floors = {k: int(v) for k, v in scaled.items()}
    remainder = used_tokens - sum(floors.values())

    # Largest fractional remainders for the leftover slots.
    fracs = sorted(
        keys,
        key=lambda k: (scaled[k] - floors[k]),
        reverse=True,
    )
    result = dict(floors)
    for i in range(remainder):
        result[fracs[i % len(fracs)]] += 1

    return result


def _reconcile_loaded_files(
    loaded_files: list[dict],
    breakdown_loaded: int,
) -> list[dict]:
    """Scale loaded_files tokens to sum exactly to ``breakdown_loaded``."""
    if not loaded_files:
        return loaded_files
    total = sum(f["tokens"] for f in loaded_files)
    if total == 0:
        return loaded_files
    if total == breakdown_loaded:
        return loaded_files

    scaled: list[dict] = []
    for f in loaded_files:
        new_tokens = f["tokens"] * breakdown_loaded / total
        scaled.append({
            **f,
            "tokens": new_tokens,
            "_frac": new_tokens - int(new_tokens),
        })

    floors = [{**f, "tokens": int(f["tokens"]) if f["tokens"] > 0 else 0,
               "_frac": f["_frac"]} for f in scaled]
    # Re-compute from raw scaled values
    raw_scaled = [f["tokens"] * breakdown_loaded / total for f in loaded_files]
    floor_vals = [int(v) for v in raw_scaled]
    remainder = breakdown_loaded - sum(floor_vals)

    order = sorted(range(len(loaded_files)),
                   key=lambda i: (raw_scaled[i] - floor_vals[i]),
                   reverse=True)
    for i in range(remainder):
        floor_vals[order[i % len(order)]] += 1

    result = []
    for i, f in enumerate(loaded_files):
        result.append({**f, "tokens": floor_vals[i]})
    return result


def _lifetime_source(
    meta,
    calls: list[dict],
) -> str:
    """Determine lifetime provenance label."""
    if not calls:
        return "none"
    sum_calls = sum(
        (c.get("prompt_tokens", 0) + c.get("completion_tokens", 0))
        for c in calls
    )
    if meta.tokens_total != sum_calls:
        return "mixed_legacy"
    all_provider = all(
        c.get("prompt_source") == "provider"
        and c.get("completion_source") == "provider"
        for c in calls
    )
    return "provider" if all_provider else "estimated"


def _system_prompt_info(messages: list[dict]) -> dict:
    """Return the system prompt info for the summary."""
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content")
            text = content if isinstance(content, str) else ""
            return {"text": text, "source": "persisted"}
    return {"text": SYSTEM_PROMPT, "source": "configured_not_yet_sent"}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_context_summary(
    meta,
    messages: list[dict],
    calls: list[dict],
    *,
    limit: int,
    model: str,
    tool_specs: list,
) -> dict:
    """Build the read-only context summary for one session.

    Parameters
    ----------
    meta: SessionMeta — session row (id, title, tokens_*, etc.)
    messages: list[dict] — chronologically ordered message dicts.
    calls: list[dict] — model_calls ledger rows.
    limit: int — configured max context tokens (positive).
    model: str — configured model name.
    tool_specs: list[ToolSpec] — workspace tool specifications.

    Returns a dict matching the ``GET /api/sessions/{id}/context`` schema.
    """
    message_count = len(messages)

    # --- Occupancy from the latest chat call -------------------------
    latest = _latest_chat_call(calls)
    last_assistant_idx = _last_assistant_index(messages)

    if latest is not None:
        base = latest.get("prompt_tokens", 0) + latest.get("completion_tokens", 0)
        prompt_src = latest.get("prompt_source", "estimated")
        completion_src = latest.get("completion_source", "estimated")

        # Trailing messages after the last assistant row.
        if last_assistant_idx is not None:
            trailing = messages[last_assistant_idx + 1:]
        else:
            trailing = messages

        trailing_tokens = count_message_tokens(trailing) if trailing else 0
        used_tokens = base + trailing_tokens

        # Determine used_source.
        if trailing_tokens > 0:
            used_source = "mixed_estimate"
        else:
            both_provider = (
                prompt_src == "provider" and completion_src == "provider"
            )
            used_source = "provider_based" if both_provider else "mixed_estimate"

        used_explanation = "Last completed chat model call; includes its output"
        if trailing_tokens > 0:
            used_explanation += "; plus estimated trailing messages"

        last_call = {
            "prompt_tokens": latest.get("prompt_tokens"),
            "completion_tokens": latest.get("completion_tokens"),
            "prompt_source": prompt_src,
            "completion_source": completion_src,
            "cached_prompt_tokens": latest.get("cached_prompt_tokens"),
            "reasoning_tokens": latest.get("reasoning_tokens"),
        }
    else:
        # No completed chat call: estimate from persisted messages.
        if messages:
            used_tokens = count_message_tokens(messages)
            if tool_specs:
                specs_json = json.dumps(
                    [s.as_openai() for s in tool_specs],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                used_tokens += estimate_tokens(specs_json)
        else:
            used_tokens = 0
        used_source = "estimated"
        used_explanation = "No completed chat call; estimated from persisted messages"
        last_call = None

    # Clamp rendered percentage to [0, 100].
    if limit > 0:
        percentage = max(0.0, min(100.0, 100.0 * used_tokens / limit))
        free_tokens = max(0, limit - used_tokens)
    else:
        percentage = 0.0
        free_tokens = 0

    # --- Breakdown categories ----------------------------------------
    loaded_files = _extract_loaded_files(messages)
    weights = _category_weights(messages, tool_specs, loaded_files)
    reconciled = _reconcile(weights, used_tokens)
    reconciled_files = _reconcile_loaded_files(
        loaded_files, reconciled["loaded_files"]
    )

    breakdown = {
        "previous_compaction": 0,
        "user_input": reconciled["user_input"],
        "loaded_files": reconciled["loaded_files"],
        "model_reasoning": reconciled["model_reasoning"],
        "tool_usage": reconciled["tool_usage"],
        "other": reconciled["other"],
        "available": free_tokens,
    }

    # --- Lifetime provenance ------------------------------------------
    lifetime_source = _lifetime_source(meta, calls)
    session_lifetime = {
        "tokens_in": meta.tokens_in,
        "tokens_out": meta.tokens_out,
        "tokens_total": meta.tokens_total,
        "source": lifetime_source,
    }

    # --- System prompt ------------------------------------------------
    system_prompt = _system_prompt_info(messages)

    # --- Assemble response -------------------------------------------
    return {
        "session_id": meta.session_id,
        "session_title": meta.title,
        "provider": "llama.cpp (OpenAI-compatible)",
        "model": model,
        "message_count": message_count,
        "context_limit_tokens": limit,
        "context_limit_source": "configured",
        "used_tokens": used_tokens,
        "free_tokens": free_tokens,
        "percentage": round(percentage, 1),
        "used_source": used_source,
        "used_explanation": used_explanation,
        "last_call": last_call,
        "session_lifetime": session_lifetime,
        "breakdown_source": "estimated",
        "breakdown": breakdown,
        "loaded_files": reconciled_files,
        "compaction_status": "not_implemented",
        "system_prompt": system_prompt,
    }
