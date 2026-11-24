"""Streaming client for the local llama-server (OpenAI-compatible API).

Encapsulates the HTTP/SSE conversation with ``llama-server``. Split from
``core.py`` so the model-provider integration (URL handling, request
payload, SSE parsing) can be developed independently of the agent loop.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

import httpx

from .tools import WorkspaceTools
from .token_counter import count_message_tokens, estimate_request_tokens


def _wire_message(message: dict[str, Any]) -> dict[str, Any]:
    """Normalize a stored message to the minimal OpenAI wire shape.

    Stored messages carry UI-only metadata (``time``, ``usage``,
    ``context_meta``) that must never reach the provider. This is an
    explicit allowlist per role, not a copy of the persisted dict, and
    the input is never mutated.
    """
    role = message.get("role")
    if role in ("system", "user"):
        return {"role": role, "content": message.get("content") or ""}
    if role == "tool":
        wire: dict[str, Any] = {"role": "tool"}
        if message.get("tool_call_id"):
            wire["tool_call_id"] = message["tool_call_id"]
        wire["content"] = message.get("content") or ""
        return wire
    # Assistant (and any other role): keep the provider-relevant fields.
    wire = {"role": role, "content": message.get("content") or ""}
    reasoning = message.get("reasoning_content")
    if reasoning:
        wire["reasoning_content"] = reasoning
    tool_calls = message.get("tool_calls")
    if tool_calls:
        wire["tool_calls"] = [
            {
                "id": tc.get("id") or "",
                "type": "function",
                "function": {
                    "name": (tc.get("function") or {}).get("name", ""),
                    "arguments": (tc.get("function") or {}).get("arguments", ""),
                },
            }
            for tc in tool_calls
        ]
    return wire


def _token_count(value: Any) -> int | None:
    """Return ``value`` if it is a valid nonnegative provider token count.

    Provider counts are integer counts and ``0`` is exact zero, not
    missing. ``bool`` is an ``int`` subclass and is rejected.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


class ModelClient:
    """Streaming chat client for a llama-server endpoint."""

    def __init__(
        self,
        llama_base_url: str,
        model: str,
        tools: WorkspaceTools,
    ) -> None:
        # Accept either a server origin (http://host:8080), an OpenAI API
        # base (http://host:8080/v1), or the full chat endpoint.
        base = llama_base_url.strip().rstrip("/")
        if base.endswith("/v1/chat/completions"):
            self.llama_url = base
        elif base.endswith("/v1"):
            self.llama_url = base + "/chat/completions"
        else:
            self.llama_url = base + "/v1/chat/completions"
        self.model = model
        self.tools = tools
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))

    async def close(self) -> None:
        await self.client.aclose()

    async def _post_stream(self, payload: dict[str, Any]) -> httpx.Response:
        """POST one chat request and return the open stream response.

        Older llama-server builds reject the OpenAI ``stream_options``
        field with an explicit HTTP 400 before any generation begins. In
        that case retry exactly once without the field (usage counts then
        fall back to estimates). Unrelated 400s and network failures
        propagate as before.
        """
        request_payload = payload
        while True:
            try:
                response = await self.client.send(
                    self.client.build_request(
                        "POST", self.llama_url, json=request_payload
                    ),
                    stream=True,
                )
            except httpx.ConnectError as exc:
                raise RuntimeError(
                    f"Cannot connect to llama.cpp at {self.llama_url}. "
                    "Check the URL, the llama.cpp listening interface, and the host firewall."
                ) from exc
            if response.status_code < 400:
                return response
            body = await response.aread()
            await response.aclose()
            text = body.decode("utf-8", errors="replace")
            if response.status_code == 400 and "stream_options" in text:
                # Explicit rejection of the usage-request field: drop it
                # and retry once before opening any generation stream.
                request_payload = {
                    key: value
                    for key, value in request_payload.items()
                    if key != "stream_options"
                }
                continue
            raise RuntimeError(
                f"llama-server returned HTTP {response.status_code}: {text}"
            )

    async def stream(
        self, messages: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], AsyncIterator[dict[str, Any]]]:
        """Stream one model turn.

        Returns a finalized ``assistant`` message dict and an async
        iterator of streaming events (``reasoning_delta`` /
        ``content_delta``). The caller must consume the iterator before
        inspecting ``assistant``; the dict is finalized in the iterator's
        ``finally`` block so it is populated even if the client
        disconnects.

        The finalized ``assistant`` carries a transient ``usage`` dict
        with prompt/completion token counts and per-field provenance
        (``"provider"`` or ``"estimated"``). Provider counts are
        preferred; a missing count falls back to the app's explicit
        estimator. ``usage`` is stripped from later requests.
        """
        wire_messages = [_wire_message(message) for message in messages]
        tools_payload = [spec.as_openai() for spec in self.tools.specs]
        payload = {
            "model": self.model,
            "messages": wire_messages,
            "tools": tools_payload,
            "tool_choice": "auto",
            "stream": True,
            "temperature": 0.2,
            # Current llama-server can extract model-emitted thoughts to
            # delta.reasoning_content when the template supports it.
            "reasoning_format": "deepseek",
            # Ask the provider for streaming usage; if it rejects this
            # field, _post_stream retries once without it.
            "stream_options": {"include_usage": True},
        }

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        # Provider usage arrives in the final SSE frame, which may have no
        # choices. Retain the latest valid value per field independently;
        # a partial usage object is not an all-or-nothing failure.
        provider_usage: dict[str, int | None] = {
            "prompt_tokens": None,
            "completion_tokens": None,
            "cached_prompt_tokens": None,
            "reasoning_tokens": None,
        }
        # Fallback prompt estimate over the exact wire request (messages +
        # tool specs), used only when the provider reports no count.
        estimated_prompt = estimate_request_tokens(wire_messages, tools_payload)

        response = await self._post_stream(payload)

        async def events() -> AsyncIterator[dict[str, Any]]:
            try:
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        packet = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # Read provider usage before the choices check:
                    # llama.cpp sends the final usage frame with empty
                    # choices, and the two are independent.
                    usage = packet.get("usage")
                    if isinstance(usage, dict):
                        prompt = _token_count(usage.get("prompt_tokens"))
                        if prompt is not None:
                            provider_usage["prompt_tokens"] = prompt
                        completion = _token_count(usage.get("completion_tokens"))
                        if completion is not None:
                            provider_usage["completion_tokens"] = completion
                        prompt_details = usage.get("prompt_tokens_details")
                        if isinstance(prompt_details, dict):
                            cached = _token_count(
                                prompt_details.get("cached_tokens")
                            )
                            if cached is not None:
                                provider_usage["cached_prompt_tokens"] = cached
                        completion_details = usage.get(
                            "completion_tokens_details"
                        )
                        if isinstance(completion_details, dict):
                            reasoning_count = _token_count(
                                completion_details.get("reasoning_tokens")
                            )
                            if reasoning_count is not None:
                                provider_usage["reasoning_tokens"] = reasoning_count

                    choices = packet.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}

                    reasoning = delta.get("reasoning_content")
                    if reasoning:
                        reasoning_parts.append(reasoning)
                        yield {"type": "reasoning_delta", "text": reasoning}

                    content = delta.get("content")
                    if content:
                        content_parts.append(content)
                        yield {"type": "content_delta", "text": content}

                    for tc in delta.get("tool_calls") or []:
                        idx = int(tc.get("index", 0))
                        entry = calls.setdefault(
                            idx,
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            },
                        )
                        if tc.get("id"):
                            entry["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            entry["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            entry["function"]["arguments"] += fn["arguments"]
            finally:
                await response.aclose()

        async def finalized_events() -> AsyncIterator[dict[str, Any]]:
            async for item in events():
                yield item

        assistant: dict[str, Any] = {"role": "assistant", "content": ""}

        async def wrapper() -> AsyncIterator[dict[str, Any]]:
            try:
                async for item in finalized_events():
                    yield item
            finally:
                assistant["content"] = "".join(content_parts)
                if reasoning_parts:
                    assistant["reasoning_content"] = "".join(reasoning_parts)
                if calls:
                    normalized = []
                    for idx in sorted(calls):
                        call = calls[idx]
                        if not call["id"]:
                            call["id"] = f"call_{uuid.uuid4().hex}"
                        normalized.append(call)
                    assistant["tool_calls"] = normalized
                # Completion estimate runs after the text/tool-call
                # normalization above so it sees the finalized assistant.
                # Provider counts win per field; ``0`` from the provider
                # is exact zero, never a fallback trigger.
                provider_prompt = provider_usage["prompt_tokens"]
                provider_completion = provider_usage["completion_tokens"]
                assistant["usage"] = {
                    "prompt_tokens": (
                        provider_prompt
                        if provider_prompt is not None
                        else estimated_prompt
                    ),
                    "completion_tokens": (
                        provider_completion
                        if provider_completion is not None
                        else count_message_tokens(
                            [_wire_message(assistant)]
                        )
                    ),
                    "prompt_source": "provider"
                    if provider_prompt is not None
                    else "estimated",
                    "completion_source": "provider"
                    if provider_completion is not None
                    else "estimated",
                    "cached_prompt_tokens": provider_usage[
                        "cached_prompt_tokens"
                    ],
                    "reasoning_tokens": provider_usage["reasoning_tokens"],
                }

        return assistant, wrapper()
