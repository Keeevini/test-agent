"""Agent loop orchestrator.

``LocalAgent`` ties together the model client, tool layer, approval gate,
and session store to run one user turn: stream a model response, execute
any tool calls (with approval for mutating tools), and persist the
conversation. The individual concerns live in their own modules:

- :mod:`.prompt` — the system prompt.
- :mod:`.approvals` — the approval gate for mutating tools.
- :mod:`.model` — the streaming llama-server client.
- :mod:`.session_store` — the async session store wrapper.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator

from .approvals import ApprovalManager
from .model import ModelClient
from .session_store import SessionStore
from .session.usage import UsageTracker
from .title import SessionTitleGenerator
from .token_counter import count_message_tokens
from .tools import ToolError, WorkspaceTools


def _now_ms() -> int:
    """Current time as epoch milliseconds (matches OpenCode's DateTimeUtcFromMillis)."""
    return int(time.time() * 1000)


def _result_meta(name: str, result: str) -> dict[str, Any]:
    """Extract a small metadata dict from a tool result string for concise UI display."""
    meta: dict[str, Any] = {}
    lines = result.splitlines()
    if name == "read_file":
        meta["lines"] = len(lines)
        meta["bytes"] = len(result.encode("utf-8"))
    elif name == "search_text":
        meta["matches"] = len(lines)
        files: set[str] = set()
        for line in lines:
            if ":" in line:
                files.add(line.split(":")[0])
        meta["files"] = len(files)
    elif name == "list_directory":
        meta["entries"] = len(lines)
    return meta


class LocalAgent:
    def __init__(
        self,
        llama_base_url: str,
        model: str,
        tools: WorkspaceTools,
        approvals: ApprovalManager,
        sessions: SessionStore,
        max_agent_steps: int = 12,
        max_context_tokens: int = 16384,
    ) -> None:
        self.tools = tools
        self.approvals = approvals
        self.sessions = sessions
        self.max_agent_steps = max_agent_steps
        self.max_context_tokens = max_context_tokens
        self.model_client = ModelClient(llama_base_url, model, tools)
        self._title = SessionTitleGenerator(self.model_client)
        # Sessions for which a title has been scheduled (or generated).
        # Guards the "only once" requirement: a title is generated at most
        # once per session while it is untitled.
        self._titled: set[str] = set()
        # Retain a reference to each in-flight title task so the event loop
        # does not garbage-collect it mid-run (the loop only keeps weak
        # references to tasks).
        self._title_tasks: set[asyncio.Task] = set()

    async def close(self) -> None:
        await self.model_client.close()

    def _context_usage(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        tokens = count_message_tokens(messages)
        max_tokens = self.max_context_tokens
        pct = 0.0
        if max_tokens > 0:
            pct = min(100.0, (100.0 * tokens) / max_tokens)
        return {
            "tokens": tokens,
            "max_tokens": max_tokens,
            "percentage": round(pct, 1),
        }

    async def stream_turn(self, session_id: str, user_text: str) -> AsyncIterator[dict[str, Any]]:
        async with self.sessions.lock(session_id):
            messages = self.sessions.messages(session_id)
            messages.append({"role": "user", "content": user_text})
            self.sessions.append_message(session_id, "user", user_text)

            tracker = UsageTracker()

            def save_tool_result(call_id, name, result, ok, path=None):
                """Persist a final tool result to both the in-memory context and DB.

                Every branch (invalid args, denied approval, ToolError, success)
                funnels through here so the result row always carries its
                ``tool_call_id`` and ``context_meta`` (name/ok, plus the sandbox-
                relative ``path`` for a successful ``read_file``). The ``path`` is
                the already-submitted sandbox-relative argument — no new vault
                read is performed.
                """
                msg = {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result,
                    "context_meta": {"name": name, "ok": ok},
                }
                if name == "read_file" and ok and isinstance(path, str):
                    msg["context_meta"]["path"] = path
                messages.append(msg)
                self.sessions.append_message(session_id, "tool", msg)

            for step in range(self.max_agent_steps):
                yield {"type": "status", "text": f"Model turn {step + 1}"}
                created = _now_ms()
                assistant_message, stream_events = await self.model_client.stream(messages)
                async for event in stream_events:
                    yield event
                # Per-activity timing (mirrors OpenCode's `time` section).
                #   ``created``  — when this model turn started.
                #   ``streamed`` — when the provider response finished streaming,
                #     before any tool for this turn executed.
                #   ``completed`` — stamped below once the turn's tool calls have
                #     run (settled). The history UI uses ``streamed - created``
                #     for the thought duration.
                streamed = _now_ms()
                assistant_message["time"] = {
                    "created": created,
                    "streamed": streamed,
                }
                # Per-tool timing: each tool call's ``created`` is when the model
                # emitted it (end of streaming); ``ran``/``completed`` are stamped
                # in the loop below as the tool executes.
                for call in assistant_message.get("tool_calls") or []:
                    call["time"] = {"created": streamed}
                messages.append(assistant_message)
                # Schedule a one-shot, non-blocking title for this session now
                # that the first model turn has finished streaming. Scheduling
                # it *after* the turn (rather than concurrently) means the
                # title's request is sent only once the main model request has
                # finished, so the two never compete for the single
                # llama-server and the turn is never delayed. The task is not
                # awaited here, so it never blocks the turn. The ``_titled``
                # guard makes later iterations no-ops.
                self._schedule_title(session_id, messages)
                assistant_seq = self.sessions.append_message(
                    session_id,
                    "assistant",
                    assistant_message,
                )
                # Record this completed model call in the durable ledger
                # immediately (before any tool step can fail), so lifetime
                # usage survives crashes and is idempotent per assistant row.
                self.sessions.record_model_call(
                    session_id,
                    f"chat:{assistant_seq}",
                    "chat",
                    assistant_seq,
                    assistant_message["usage"],
                )

                tool_calls = assistant_message.get("tool_calls") or []
                if not tool_calls:
                    # No tools to run: the turn is complete at the end of streaming.
                    assistant_message["time"]["completed"] = _now_ms()
                    self.sessions.update_message_content(
                        session_id, assistant_seq, assistant_message
                    )
                    yield {"type": "context", **self._context_usage(messages)}
                    self._flush_usage(tracker, session_id)
                    yield {"type": "done"}
                    return

                for call in tool_calls:
                    function = call.get("function", {})
                    name = function.get("name", "")
                    raw_args = function.get("arguments", "{}")
                    call_id = call.get("id") or f"call_{uuid.uuid4().hex}"
                    # Stamp the id back onto the persisted assistant call so it
                    # links to its (possibly generated) result id.
                    call["id"] = call_id
                    call_time = call.get("time")

                    try:
                        args = json.loads(raw_args or "{}")
                        if not isinstance(args, dict):
                            raise ValueError("tool arguments must be a JSON object")
                    except Exception as exc:
                        result = f"Tool arguments were invalid JSON: {exc}"
                        yield {
                            "type": "tool_result",
                            "name": name,
                            "arguments": raw_args,
                            "ok": False,
                            "result": result,
                            "meta": _result_meta(name, result),
                        }
                        save_tool_result(call_id, name, result, False)
                        call_time["completed"] = _now_ms()
                        continue

                    yield {"type": "tool_call", "name": name, "arguments": args}

                    if self.tools.is_mutating(name):
                        pending = await self.approvals.create(session_id, name, args)
                        yield {
                            "type": "approval_required",
                            "approval_id": pending.approval_id,
                            "name": name,
                            "arguments": args,
                        }
                        approved = await self.approvals.wait(pending)
                        if not approved:
                            result = "User denied this mutating operation."
                            yield {
                                "type": "tool_result",
                                "name": name,
                                "arguments": args,
                                "ok": False,
                                "result": result,
                                "meta": _result_meta(name, result),
                            }
                            save_tool_result(call_id, name, result, False)
                            call_time["completed"] = _now_ms()
                            continue

                    call_time["ran"] = _now_ms()
                    try:
                        result = self.tools.execute(name, args)
                        ok = True
                    except ToolError as exc:
                        result = f"Tool error: {exc}"
                        ok = False
                    call_time["completed"] = _now_ms()

                    tracker.record_tool(name)

                    yield {
                        "type": "tool_result",
                        "name": name,
                        "arguments": args,
                        "ok": ok,
                        "result": result,
                        "meta": _result_meta(name, result),
                    }
                    save_tool_result(call_id, name, result, ok, path=args.get("path"))

                # Stamp the turn's completion once all of its tool calls have
                # settled, then persist the updated timing.
                assistant_message["time"]["completed"] = _now_ms()
                self.sessions.update_message_content(
                    session_id, assistant_seq, assistant_message
                )

                yield {"type": "context", **self._context_usage(messages)}

            self._flush_usage(tracker, session_id)

            yield {
                "type": "error",
                "message": f"Agent stopped after {self.max_agent_steps} model/tool iterations.",
            }

    def _schedule_title(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Schedule a one-shot, non-blocking title generation for a session.

        Called after the first model turn has finished streaming, so the
        title's request is sent only once the main model request is done and
        the two never compete for the single llama-server. The title is
        generated at most once per session while it is untitled ("only once"):
        the first call for a session wins, subsequent calls are no-ops. The
        task is retained in ``self._title_tasks`` so the event loop does not
        garbage-collect it mid-run.
        """
        if session_id in self._titled:
            return
        self._titled.add(session_id)
        task = asyncio.create_task(self._generate_title(session_id, messages))
        self._title_tasks.add(task)
        # Drop the reference once the task settles so the set does not grow
        # without bound for long-lived agent processes.
        task.add_done_callback(self._title_tasks.discard)

    async def _generate_title(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Generate a title for ``session_id`` and persist it.

        Runs on the event loop (never awaited in the turn path), so it is
        scheduled after the first model turn and never delays the model.
        Failures are swallowed: title generation is best-effort and must never
        surface as an error to the turn. The title is written via the store's
        ``update_title`` path.
        """
        try:
            title, usage = await self._title.generate(messages)
        except Exception:
            title, usage = None, None
        # Record the title model call's usage in the durable ledger even when
        # the title is empty (a completed model call still spent tokens).
        # Lifetime-only: does not create a chat row or move the latest-chat marker.
        if usage:
            self.sessions.record_model_call(
                session_id, "title", "title", None, usage
            )
        if title:
            self.sessions.update_title(session_id, title)

    def _flush_usage(self, tracker: UsageTracker, session_id: str) -> None:
        """Flush accumulated usage to the persistent store."""
        tracker.flush(self.sessions._store, session_id)
