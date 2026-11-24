"""Tests for Stage 01 provider-usage capture in ``ModelClient.stream``.

These inject SSE frames via ``httpx.MockTransport`` (no live llama-server)
and verify the finalized assistant dict carries per-field usage provenance:

* provider counts win and ``0`` is exact zero, not missing;
* a missing count falls back to the app's explicit estimator, independently
  per field (prompt vs. completion);
* ``prompt_tokens_details.cached_tokens`` / ``completion_tokens_details.
  reasoning_tokens`` are stored as subsets, never re-added to the aggregate;
* requests ask for streaming usage (``stream_options.include_usage``);
* stored-only metadata (``time`` / ``usage`` / ``context_meta`` and nested
  tool-call timing) is stripped from outbound messages;
* an explicit HTTP 400 rejecting ``stream_options`` is retried exactly once
  without the field, and the resulting counts are then marked ``estimated``;
* text / reasoning / tool-call deltas are unchanged.
"""

from __future__ import annotations

import json
from typing import Any, Callable
from unittest import IsolatedAsyncioTestCase

import httpx

from src.agent.model import ModelClient


class _EmptyTools:
    """``ModelClient`` only reads ``tools.specs`` to build the request payload."""

    specs: list = []


def _sse(*frames: dict[str, Any]) -> bytes:
    """Join SSE ``data:`` frames into a byte stream terminated by ``[DONE]``."""
    chunks = [f"data: {json.dumps(frame)}\n" for frame in frames]
    chunks.append("data: [DONE]\n")
    return "".join(chunks).encode("utf-8")


def _build_client(handler: Callable[[httpx.Request], httpx.Response]) -> ModelClient:
    """Build a ``ModelClient`` whose HTTP client is backed by a mock transport."""
    client = ModelClient("http://llama.local/v1", "local", _EmptyTools())
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def _run(
    handler: Callable[[httpx.Request], httpx.Response],
    messages: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[httpx.Request]]:
    """Drive one ``stream()`` turn and return the finalized state.

    Returns the finalized ``assistant`` dict, the streamed events, and the
    captured requests (so tests can inspect the outbound payload).
    """
    captured: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    client = _build_client(capture)
    try:
        assistant, events = await client.stream(
            messages if messages is not None else [{"role": "user", "content": "hello"}]
        )
        seen: list[dict[str, Any]] = []
        async for item in events:
            seen.append(item)
        return assistant, seen, captured
    finally:
        await client.client.aclose()


def _payload(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode("utf-8"))


class ModelUsageTests(IsolatedAsyncioTestCase):
    # ---------------------------------------------------------------- usage
    def _empty_choices_usage(self):
        """A handler whose final frame carries usage with no choices."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = _sse(
                {"choices": [{"delta": {"content": "hi"}}]},
                {"choices": [], "usage": {"prompt_tokens": 43, "completion_tokens": 2}},
            )
            return httpx.Response(200, content=body)

        return handler

    async def test_empty_choices_packet_finalizes_provider_usage(self):
        """The final usage frame arrives with empty ``choices``; it must win."""
        assistant, _, _ = await _run(self._empty_choices_usage())
        usage = assistant["usage"]
        self.assertEqual(usage["prompt_tokens"], 43)
        self.assertEqual(usage["completion_tokens"], 2)
        self.assertEqual(usage["prompt_source"], "provider")
        self.assertEqual(usage["completion_source"], "provider")

    async def test_finish_reason_frame_captures_usage(self):
        """Usage may share the frame that carries the final ``finish_reason``."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = _sse(
                {
                    "choices": [
                        {"delta": {"content": "hi"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                }
            )
            return httpx.Response(200, content=body)

        assistant, _, _ = await _run(handler)
        usage = assistant["usage"]
        self.assertEqual(usage["prompt_tokens"], 5)
        self.assertEqual(usage["completion_tokens"], 3)
        self.assertEqual(usage["prompt_source"], "provider")
        self.assertEqual(usage["completion_source"], "provider")

    async def test_provider_zero_is_exact_not_missing(self):
        """A provider ``0`` is exact zero, not a trigger for the fallback."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = _sse(
                {"choices": [{"delta": {"content": "x"}}]},
                {"choices": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0}},
            )
            return httpx.Response(200, content=body)

        assistant, _, _ = await _run(handler)
        usage = assistant["usage"]
        self.assertEqual(usage["prompt_tokens"], 0)
        self.assertEqual(usage["completion_tokens"], 0)
        self.assertEqual(usage["prompt_source"], "provider")
        self.assertEqual(usage["completion_source"], "provider")

    async def test_missing_completion_falls_back_independently(self):
        """Only prompt reported: completion must fall back on its own."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = _sse(
                {"choices": [{"delta": {"content": "hello world"}}]},
                {"choices": [], "usage": {"prompt_tokens": 10}},
            )
            return httpx.Response(200, content=body)

        assistant, _, _ = await _run(handler)
        usage = assistant["usage"]
        self.assertEqual(usage["prompt_tokens"], 10)
        self.assertEqual(usage["prompt_source"], "provider")
        self.assertEqual(usage["completion_source"], "estimated")
        self.assertGreater(usage["completion_tokens"], 0)

    async def test_missing_prompt_falls_back_independently(self):
        """Only completion reported: prompt must fall back on its own."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = _sse(
                {"choices": [{"delta": {"content": "hello world"}}]},
                {"choices": [], "usage": {"completion_tokens": 7}},
            )
            return httpx.Response(200, content=body)

        assistant, _, _ = await _run(handler)
        usage = assistant["usage"]
        self.assertEqual(usage["completion_tokens"], 7)
        self.assertEqual(usage["completion_source"], "provider")
        self.assertEqual(usage["prompt_source"], "estimated")
        self.assertGreater(usage["prompt_tokens"], 0)

    async def test_no_usage_falls_back_to_estimates(self):
        """No usage frame at all: both fields are explicit estimates."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = _sse({"choices": [{"delta": {"content": "hello world"}}]})
            return httpx.Response(200, content=body)

        assistant, _, _ = await _run(handler)
        usage = assistant["usage"]
        self.assertEqual(usage["prompt_source"], "estimated")
        self.assertEqual(usage["completion_source"], "estimated")
        self.assertGreater(usage["prompt_tokens"], 0)
        self.assertGreater(usage["completion_tokens"], 0)
        self.assertIsNone(usage["cached_prompt_tokens"])
        self.assertIsNone(usage["reasoning_tokens"])

    async def test_details_not_double_added(self):
        """Cache/reasoning counters are subsets of the aggregate, not added."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = _sse(
                {"choices": [{"delta": {"content": "hi"}}]},
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 40,
                        "completion_tokens": 10,
                        "prompt_tokens_details": {"cached_tokens": 20},
                        "completion_tokens_details": {"reasoning_tokens": 4},
                    },
                },
            )
            return httpx.Response(200, content=body)

        assistant, _, _ = await _run(handler)
        usage = assistant["usage"]
        # The aggregate must equal the provider value, not value + detail.
        self.assertEqual(usage["prompt_tokens"], 40)
        self.assertEqual(usage["completion_tokens"], 10)
        self.assertNotEqual(usage["prompt_tokens"], 40 + 20)
        self.assertNotEqual(usage["completion_tokens"], 10 + 4)
        self.assertEqual(usage["cached_prompt_tokens"], 20)
        self.assertEqual(usage["reasoning_tokens"], 4)

    # ---------------------------------------------------------------- request
    async def test_request_includes_stream_options(self):
        """The outbound request asks the provider for streaming usage."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_sse({"choices": []}))

        _, _, captured = await _run(handler)
        self.assertEqual(
            _payload(captured[0])["stream_options"],
            {"include_usage": True},
        )

    async def test_stored_metadata_not_forwarded(self):
        """``time`` / ``usage`` / ``context_meta`` and nested tool timing are
        stripped from outbound messages, never forwarded to the provider."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_sse({"choices": []}))

        messages = [
            {"role": "user", "content": "hi", "time": "2026-01-01T00:00:00Z"},
            {
                "role": "assistant",
                "content": "hi",
                "time": "2026-01-01T00:00:01Z",
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                "context_meta": {"foo": "bar"},
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "read_file", "arguments": "{}"},
                        "time": "2026-01-01T00:00:02Z",
                    }
                ],
            },
        ]
        _, _, captured = await _run(handler, messages)
        payload = _payload(captured[0])
        for msg in payload["messages"]:
            self.assertNotIn("time", msg)
            self.assertNotIn("usage", msg)
            self.assertNotIn("context_meta", msg)
        for tc in payload["messages"][1]["tool_calls"]:
            self.assertNotIn("time", tc)
        # The provider-relevant fields survive normalization.
        self.assertEqual(payload["messages"][1]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(
            payload["messages"][1]["tool_calls"][0]["function"]["name"],
            "read_file",
        )

    async def test_deltas_unchanged(self):
        """Text / reasoning / tool-call deltas keep their current behavior."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = _sse(
                {"choices": [{"delta": {"reasoning_content": "thinking"}}]},
                {"choices": [{"delta": {"content": "hi"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}},
            )
            return httpx.Response(200, content=body)

        assistant, seen, _ = await _run(handler)
        types_texts = [(e["type"], e["text"]) for e in seen]
        self.assertIn(("reasoning_delta", "thinking"), types_texts)
        self.assertIn(("content_delta", "hi"), types_texts)
        self.assertEqual(assistant["content"], "hi")
        self.assertEqual(assistant["reasoning_content"], "thinking")
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "read_file")

    # ---------------------------------------------------------------- retry
    async def test_retries_without_stream_options_on_rejection(self):
        """An explicit 400 rejecting ``stream_options`` is retried once,
        without the field, and the resulting counts are marked estimated."""
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if "stream_options" in _payload(request):
                return httpx.Response(
                    400, content=b'{"error": "stream_options is not supported"}'
                )
            return httpx.Response(200, content=_sse({"choices": []}))

        assistant, _, captured = await _run(handler)
        # First request carried the field; the retry dropped it.
        self.assertEqual(len(captured), 2)
        self.assertIn("stream_options", _payload(captured[0]))
        self.assertNotIn("stream_options", _payload(captured[1]))
        # No usage was ever received, so the fallback labels both as estimated.
        self.assertEqual(assistant["usage"]["prompt_source"], "estimated")
        self.assertEqual(assistant["usage"]["completion_source"], "estimated")

    async def test_unrelated_400_is_not_retried(self):
        """A 400 that does not mention ``stream_options`` propagates, not retry."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400, content=b'{"error": "bad model"}'
            )

        with self.assertRaises(RuntimeError):
            await _run(handler)
