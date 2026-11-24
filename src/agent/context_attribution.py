"""Pure, disjoint text attribution; no network or database access."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from .model import _wire_message
from .session.tool_links import associate_tool_results
from .token_counter import estimate_tokens

CATEGORIES = ('previous_compaction', 'user_input', 'loaded_files',
              'model_reasoning', 'tool_usage', 'other')
UNKNOWN = '(unmatched tool)'


@dataclass(frozen=True)
class Segment:
    category: str
    text: str = ''
    seq: int | None = None
    group: str | None = None
    component: str | None = None
    fixed: int = 0


def _compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _text(value) -> str:
    return value if isinstance(value, str) else ''


def _name(call: dict) -> str:
    fn = call.get('function')
    return (_text(fn.get('name')) or UNKNOWN) if isinstance(fn, dict) else UNKNOWN


def _file_path(call: dict, message: dict) -> str | None:
    activity = message.get('activity')
    if _name(call) != 'read_file' or not isinstance(activity, dict) or activity.get('ok') is not True:
        return None
    try:
        args = json.loads(call['function']['arguments'])
    except (KeyError, TypeError, ValueError):
        return None
    path = args.get('path') if isinstance(args, dict) else None
    return path if isinstance(path, str) and path else None


def build_segments(messages: list[dict], tool_specs: list) -> list[Segment]:
    if not messages:
        return []  # configured, unsent prompt/specs are not active usage
    links = associate_tool_results(messages)
    segments: list[Segment] = []
    for i, msg in enumerate(messages):
        seq = msg.get('seq', i)
        role = msg.get('role')
        text = _text(msg.get('content'))
        segments.append(Segment('other', seq=seq, fixed=4))  # framing estimate
        if role == 'user':
            segments.append(Segment('user_input', text, seq))
        elif role == 'assistant':
            segments.append(Segment('other', text, seq))
            segments.append(Segment('model_reasoning', _text(msg.get('reasoning_content')), seq))
            for call in msg.get('tool_calls') or []:
                if not isinstance(call, dict):
                    continue
                # Reuse the exact provider allowlist; no timing/approval metadata.
                try:
                    wire = _wire_message({'role': 'assistant', 'tool_calls': [call]})['tool_calls'][0]
                except (AttributeError, KeyError, TypeError):
                    continue
                segments.append(Segment('tool_usage', _compact(wire), seq, _name(call), 'call'))
        elif role == 'tool':
            call = links.get(i, {})
            path = _file_path(call, msg)
            if path is not None:
                segments.append(Segment('loaded_files', text, seq, path, 'file'))
            else:
                segments.append(Segment('tool_usage', text, seq, _name(call), 'result'))
        else:
            segments.append(Segment('other', text, seq))
    for spec in tool_specs:
        wire = spec.as_openai()
        segments.append(Segment('tool_usage', _compact(wire), None, _name(wire), 'definition'))
    return segments


def segment_count(segment: Segment, exact: Mapping[str, int]) -> tuple[int, str | None]:
    if segment.fixed:
        return segment.fixed, 'estimated'
    if not segment.text:
        return 0, None
    if segment.text in exact:
        return exact[segment.text], 'tokenized'
    return estimate_tokens(segment.text), 'estimated'


def _source(sources: set[str]) -> str:
    if not sources:
        return 'none'
    if len(sources) == 1:
        return next(iter(sources))
    return 'mixed'


def summarize_segments(segments: list[Segment], exact: Mapping[str, int] | None = None) -> dict:
    exact = exact or {}
    breakdown = dict.fromkeys(CATEGORIES, 0)
    category_sources = {key: set() for key in CATEGORIES}
    files: dict[str, dict] = {}
    tools: dict[str, dict] = {}
    file_sources: dict[str, set[str]] = {}
    tool_sources: dict[str, set[str]] = {}
    for segment in segments:
        count, source = segment_count(segment, exact)
        breakdown[segment.category] += count
        if source:
            category_sources[segment.category].add(source)
        if segment.category == 'loaded_files':
            path = segment.group
            row = files.setdefault(path, {'path': path, 'tokens': 0, 'occurrences': 0})
            row['tokens'] += count
            row['occurrences'] += 1
            sources = file_sources.setdefault(path, set())
            if source:
                sources.add(source)
        elif segment.category == 'tool_usage':
            name = segment.group or UNKNOWN
            row = tools.setdefault(name, {'name': name, 'tokens': 0, 'call_tokens': 0,
                                           'result_tokens': 0, 'definition_tokens': 0, 'calls': 0})
            row['tokens'] += count
            row[f'{segment.component}_tokens'] += count
            if segment.component == 'call':
                row['calls'] += 1
            sources = tool_sources.setdefault(name, set())
            if source:
                sources.add(source)
    for key, row in files.items():
        row['source'] = _source(file_sources[key])
    for key, row in tools.items():
        row['source'] = _source(tool_sources[key])
    return {
        'breakdown': breakdown,
        'breakdown_sources': {key: _source(value) for key, value in category_sources.items()},
        'loaded_files': list(files.values()),
        'tool_usage_details': list(tools.values()),
        'attributed_tokens': sum(breakdown.values()),
        'breakdown_source': ('tokenizer_assisted' if any('tokenized' in s for s in category_sources.values()) else 'estimated'),
    }
