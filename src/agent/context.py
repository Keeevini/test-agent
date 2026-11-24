"""Read-only occupancy and disjoint text attribution for a session."""
from __future__ import annotations

from .context_attribution import build_segments, segment_count, summarize_segments
from .prompt import SYSTEM_PROMPT


def _latest_chat_call(calls):
    return max((c for c in calls if c.get('kind') == 'chat'),
               key=lambda c: c.get('assistant_seq') or 0, default=None)


def _lifetime_source(calls):
    if not calls:
        return 'none'
    return 'provider' if all(c.get('prompt_source') == c.get('completion_source') == 'provider'
                             for c in calls) else 'estimated'


def _system_prompt_info(messages):
    for msg in messages:
        if msg.get('role') == 'system':
            return {'text': msg.get('content') or '', 'source': 'persisted'}
    return {'text': SYSTEM_PROMPT, 'source': 'configured_not_yet_sent'}


def build_context_summary(meta, messages, calls, *, limit, model, tool_specs,
                          segments=None, exact_counts=None):
    """Provider occupancy and per-text attribution are independent measurements.

    Counts of separately tokenized text/JSON do not reproduce the provider's
    templated sequence or guarantee what remains in its KV cache.
    """
    if segments is None:
        segments = build_segments(messages, tool_specs)
    exact_counts = exact_counts or {}
    attribution = summarize_segments(segments, exact_counts)
    latest = _latest_chat_call(calls)
    last_call = None
    if latest is not None:
        last_call = {key: latest.get(key) for key in (
            'prompt_tokens', 'completion_tokens', 'prompt_source', 'completion_source',
            'cached_prompt_tokens', 'reasoning_tokens')}
    anchor = latest.get('assistant_seq') if latest is not None else None
    anchor_present = anchor is not None and any(
        m.get('role') == 'assistant' and m.get('seq', i) == anchor
        for i, m in enumerate(messages))
    if anchor_present:
        trailing = [s for s in segments if s.seq is not None and s.seq > anchor]
        used = latest['prompt_tokens'] + latest['completion_tokens'] + sum(
            segment_count(s, exact_counts)[0] for s in trailing)
        used_source = ('provider_based' if not trailing and
                       latest['prompt_source'] == latest['completion_source'] == 'provider'
                       else 'mixed_estimate')
        explanation = 'Last completed chat model call; includes its output'
        if trailing:
            explanation += '; plus trailing text counts and estimated framing'
    else:
        used = attribution['attributed_tokens']
        used_source = 'estimated'
        explanation = ('No completed chat call with a stored assistant anchor; '
                       'approximated from stored message text and tool definitions')
    free = max(0, limit - used) if limit > 0 else 0
    percentage = min(100.0, 100.0 * used / limit) if limit > 0 else 0.0
    attribution['breakdown']['available'] = free
    incoming = sum(c['prompt_tokens'] for c in calls)
    outgoing = sum(c['completion_tokens'] for c in calls)
    return {
        'session_id': meta.session_id, 'session_title': meta.title,
        'created_at': meta.created_at, 'updated_at': meta.updated_at,
        'provider': 'llama.cpp (OpenAI-compatible)', 'model': model,
        'message_count': len(messages),
        'assistant_message_count': sum(m.get('role') == 'assistant' for m in messages),
        'user_message_count': sum(m.get('role') == 'user' for m in messages),
        'context_limit_tokens': limit, 'context_limit_source': 'configured',
        'used_tokens': used, 'free_tokens': free, 'percentage': round(percentage, 1),
        'used_source': used_source, 'used_explanation': explanation, 'last_call': last_call,
        'session_lifetime': {'tokens_in': incoming, 'tokens_out': outgoing,
                             'tokens_total': incoming + outgoing, 'source': _lifetime_source(calls)},
        **attribution, 'attribution_difference': used - attribution['attributed_tokens'],
        'compaction_status': 'not_implemented', 'system_prompt': _system_prompt_info(messages),
    }
