import json
from unittest import TestCase
from src.agent.context import build_context_summary
from src.agent.context_attribution import build_segments, summarize_segments
from src.agent.tools.filesystem import ToolSpec
from test_context_summary import _meta


class AttributionTests(TestCase):
    def test_disjoint_file_reasoning_and_tool_counts(self):
        calls = [{'id': str(i), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps({'path': 'AGENTS.md'})}} for i, name in enumerate(['read_file', 'read_file', 'read_file', 'search_text', 'list_directory'])]
        messages = [{'seq': 0, 'role': 'system', 'content': 'system'}, {'seq': 1, 'role': 'user', 'content': 'prompt'}, {'seq': 2, 'role': 'assistant', 'content': 'answer', 'reasoning_content': 'think', 'tool_calls': calls}]
        for i, text in enumerate(['file', 'file', 'denied', 'matches', 'listing']):
            messages.append({'seq': i+3, 'role': 'tool', 'tool_call_id': str(i), 'content': text, 'activity': {'ok': i != 2}})
        specs = [ToolSpec('read_file', 'Read', {'type': 'object'})]
        segments = build_segments(messages, specs)
        exact = {s.text: 7 for s in segments if s.text}
        exact['file'] = 30
        exact['think'] = 11
        exact.pop('matches')  # partial tokenizer failure
        ledger = [{'kind': 'chat', 'assistant_seq': 2, 'prompt_tokens': 1, 'completion_tokens': 0, 'prompt_source': 'provider', 'completion_source': 'provider', 'reasoning_tokens': 0}]
        result = build_context_summary(_meta(), messages, ledger, limit=1000, model='local', tool_specs=specs, segments=segments, exact_counts=exact)
        b = result['breakdown']; tools = {r['name']: r for r in result['tool_usage_details']}
        self.assertEqual(b['loaded_files'], 60)
        self.assertEqual(result['loaded_files'][0]['occurrences'], 2)
        self.assertEqual(b['model_reasoning'], 11)
        self.assertEqual(b['other'], 14 + 4*len(messages))
        self.assertEqual(tools['read_file']['call_tokens'], 21)
        self.assertEqual(tools['read_file']['definition_tokens'], 7)
        self.assertEqual(tools['read_file']['result_tokens'], 7)
        self.assertEqual(tools['search_text']['source'], 'mixed')
        self.assertEqual(b['tool_usage'], sum(t['tokens'] for t in tools.values()))
        self.assertEqual(result['last_call']['reasoning_tokens'], 0)
        self.assertEqual(result['assistant_message_count'], 1)
        self.assertEqual(result['user_message_count'], 1)
        self.assertEqual(result['attributed_tokens'], sum(v for k,v in b.items() if k != 'available'))
        self.assertEqual(result['attribution_difference'], result['used_tokens'] - result['attributed_tokens'])
        self.assertEqual(result['session_lifetime']['tokens_total'], 1)
        fallback = summarize_segments(segments)
        self.assertGreater(fallback['breakdown']['loaded_files'], 0)
        self.assertEqual(fallback['breakdown_source'], 'estimated')
