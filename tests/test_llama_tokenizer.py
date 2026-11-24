from unittest import IsolatedAsyncioTestCase
import httpx
from src.agent.llama_tokenizer import LlamaTextTokenizer

class TokenizerTests(IsolatedAsyncioTestCase):
    async def test_literal_payload_cache_and_zero(self):
        import json
        requests = []
        def handler(req):
            requests.append(req)
            payload = json.loads(req.content)
            self.assertEqual(str(req.url), 'http://host:8080/tokenize')
            self.assertEqual(payload, {'content': payload['content'], 'add_special': False, 'parse_special': False, 'with_pieces': False})
            return httpx.Response(200, json={'tokens': [] if payload['content'] == 'zero' else [1,2,3]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tok = LlamaTextTokenizer('http://host:8080/v1/chat/completions', client)
            self.assertEqual(await tok.counts_for(['text','zero','text','']), {'text':3,'zero':0})
            self.assertEqual(await tok.counts_for(['zero','text']), {'zero':0,'text':3})
            self.assertEqual(len(requests), 2)
    async def test_failure_cooldown_and_no_poisoned_cache(self):
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(200, json={'tokens':[True]}) if len(requests) == 1 else httpx.Response(200,json={'tokens':[1]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tok = LlamaTextTokenizer('http://host/v1/chat/completions', client)
            self.assertEqual(await tok.counts_for(['a','b']), {})
            self.assertEqual(len(requests), 1)
            tok._retry_after = 0
            self.assertEqual(await tok.counts_for(['a']), {'a':1})
    async def test_bounded_misses_and_oversize(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200,json={'tokens':[1]}))) as client:
            tok = LlamaTextTokenizer('http://host/v1/chat/completions', client)
            tok.MAX_NEW_TEXTS = 1; tok.MAX_TEXT_BYTES = 5
            self.assertEqual(await tok.counts_for(['oversize','one','two']), {'one':1})
            self.assertEqual(await tok.counts_for(['one','two']), {'one':1,'two':1})
