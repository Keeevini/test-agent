"""Bounded, best-effort literal text tokenization against the configured llama-server."""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from collections.abc import Iterable

import httpx


class LlamaTextTokenizer:
    MAX_CACHE = 2048
    MAX_NEW_TEXTS = 128
    MAX_TEXT_BYTES = 2_000_000
    BUDGET_SECONDS = 2.0
    COOLDOWN_SECONDS = 5.0

    def __init__(self, chat_url: str, client: httpx.AsyncClient):
        suffix = '/v1/chat/completions'
        if not chat_url.endswith(suffix):
            raise ValueError('Expected normalized llama chat URL')
        self.url = chat_url[:-len(suffix)] + '/tokenize'
        self.client = client  # ModelClient owns/ closes this client
        self._cache: OrderedDict[bytes, int] = OrderedDict()
        self._lock = asyncio.Lock()
        self._retry_after = 0.0

    async def counts_for(self, texts: Iterable[str]) -> dict[str, int]:
        """Successful counts only. Unreturned strings use the local estimator."""
        result: dict[str, int] = {}
        pending: list[tuple[str, bytes]] = []
        for text in dict.fromkeys(texts):
            if not text:
                continue
            raw = text.encode('utf-8', errors='surrogatepass')
            if len(raw) > self.MAX_TEXT_BYTES:
                continue
            key = hashlib.sha256(raw).digest()
            if key in self._cache:
                result[text] = self._cache[key]
                self._cache.move_to_end(key)
            else:
                pending.append((text, key))
        try:
            # Includes waiting for a previous request; no unbounded fan-out.
            async with asyncio.timeout(self.BUDGET_SECONDS):
                async with self._lock:
                    requests = 0
                    for text, key in pending:
                        if key in self._cache:
                            result[text] = self._cache[key]
                            self._cache.move_to_end(key)
                            continue
                        if requests >= self.MAX_NEW_TEXTS or time.monotonic() < self._retry_after:
                            continue
                        requests += 1
                        try:
                            response = await self.client.post(
                                self.url,
                                json={'content': text, 'add_special': False,
                                      'parse_special': False, 'with_pieces': False},
                                timeout=httpx.Timeout(1.0), follow_redirects=False,
                            )
                            response.raise_for_status()
                            payload = response.json()
                            tokens = payload.get('tokens') if isinstance(payload, dict) else None
                            if not isinstance(tokens, list) or any(type(t) is not int or t < 0 for t in tokens):
                                raise ValueError('Invalid tokenizer response')
                        except (httpx.HTTPError, ValueError, UnicodeError):
                            self._retry_after = time.monotonic() + self.COOLDOWN_SECONDS
                            continue
                        count = len(tokens)
                        self._cache[key] = result[text] = count
                        self._cache.move_to_end(key)
                        while len(self._cache) > self.MAX_CACHE:
                            self._cache.popitem(last=False)
        except TimeoutError:
            # Preserve the partial successful results; no failed value is cached.
            pass
        return result
