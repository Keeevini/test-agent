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
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": [spec.as_openai() for spec in self.tools.specs],
            "tool_choice": "auto",
            "stream": True,
            "temperature": 0.2,
            # Current llama-server can extract model-emitted thoughts to
            # delta.reasoning_content when the template supports it.
            "reasoning_format": "deepseek",
        }

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        try:
            response = await self.client.send(
                self.client.build_request("POST", self.llama_url, json=payload),
                stream=True,
            )
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Cannot connect to llama.cpp at {self.llama_url}. "
                "Check the URL, the llama.cpp listening interface, and the host firewall."
            ) from exc

        if response.status_code >= 400:
            body = await response.aread()
            await response.aclose()
            text = body.decode("utf-8", errors="replace")
            raise RuntimeError(f"llama-server returned HTTP {response.status_code}: {text}")

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

        return assistant, wrapper()
