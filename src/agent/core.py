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
import copy
import json
import logging
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

logger = logging.getLogger(__name__)


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

    def _normalize_call_ids(self, tool_calls: list[dict[str, Any]]) -> None:
        """Normalize tool call IDs in place.

        Ensures every call has a non-empty ``id`` and that IDs within the
        batch are unique. Missing IDs are assigned ``call_<uuid>``. Duplicate
        IDs (beyond the first occurrence) are replaced with fresh
        ``call_<uuid>`` values so the batch can be persisted and matched
        unambiguously.
        """
        seen: set[str] = set()
        for call in tool_calls:
            cid = call.get("id")
            if not isinstance(cid, str) or not cid:
                cid = f"call_{uuid.uuid4().hex}"
            elif cid in seen:
                cid = f"call_{uuid.uuid4().hex}"
            seen.add(cid)
            call["id"] = cid

    async def stream_turn(self, session_id: str, user_text: str) -> AsyncIterator[dict[str, Any]]:
        async with self.sessions.lock(session_id):
            messages = self.sessions.messages(session_id)
            messages.append({"role": "user", "content": user_text})
            self.sessions.append_message(session_id, "user", user_text)

            tracker = UsageTracker()

            last_assistant_seq: int | None = None
            last_step_activity: dict[str, Any] | None = None
            alias_map: dict[str, str] = self.tools.aliases

            for step in range(self.max_agent_steps):
                yield {"type": "status", "text": f"Model turn {step + 1}"}
                created = _now_ms()
                assistant_message, stream_events = await self.model_client.stream(messages)
                # Track runs of consecutive deltas of the same kind.
                # Only reasoning_delta and content_delta are relevant.
                parts: list[dict[str, Any]] = []
                current_run: dict[str, Any] | None = None
                run_start_mono: float = 0.0

                async for event in stream_events:
                    kind = None
                    if event.get("type") == "reasoning_delta":
                        kind = "reasoning"
                    elif event.get("type") == "content_delta":
                        kind = "content"

                    if kind and event.get("text"):
                        chars = len(event["text"])
                        if current_run is None:
                            current_run = {"kind": kind, "chars": 0}
                            run_start_mono = time.monotonic()
                        elif current_run["kind"] != kind:
                            # Finalize previous run on kind switch.
                            end_mono = time.monotonic()
                            if current_run["kind"] == "reasoning":
                                current_run["duration_ms"] = max(
                                    0, int((end_mono - run_start_mono) * 1000)
                                )
                            parts.append(current_run)
                            # Yield model_part_end for the finished run.
                            end_event: dict[str, Any] = {
                                "type": "model_part_end",
                                "kind": current_run["kind"],
                            }
                            if current_run["kind"] == "reasoning":
                                end_event["duration_ms"] = current_run["duration_ms"]
                            yield end_event
                            # Start new run.
                            current_run = {"kind": kind, "chars": 0}
                            run_start_mono = time.monotonic()
                        current_run["chars"] += chars

                    yield event

                # Finalize last run after stream ends.
                if current_run is not None:
                    end_mono = time.monotonic()
                    if current_run["kind"] == "reasoning":
                        current_run["duration_ms"] = max(
                            0, int((end_mono - run_start_mono) * 1000)
                        )
                    parts.append(current_run)
                    current_run = None
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
                # Step A: Normalize missing/duplicate IDs on the call objects
                # themselves before the assistant row is persisted.
                tool_calls = assistant_message.get("tool_calls") or []
                self._normalize_call_ids(tool_calls)
                # Per-tool timing: each tool call's ``created`` is when the model
                # emitted it (end of streaming); ``ran``/``completed`` are stamped
                # in the loop below as the tool executes.
                for call in tool_calls:
                    call["time"] = {"created": streamed}

                # Step A: Initialize step activity and persist the assistant row.
                step_activity = {"version": 1, "calls": {}, "parts": parts}
                assistant_seq = self.sessions.append_message(
                    session_id,
                    "assistant",
                    assistant_message,
                    activity=step_activity,
                )
                last_assistant_seq = assistant_seq
                last_step_activity = step_activity
                # The completed model call is durable before any tool or SSE yield.
                self.sessions.record_model_call(
                    session_id, f"chat:{assistant_seq}", "chat",
                    assistant_seq, assistant_message["usage"],
                )

                # Yield the last model_part_end after persistence so the
                # frontend can finalize the final segment.
                if parts:
                    last = parts[-1]
                    end_event: dict[str, Any] = {
                        "type": "model_part_end",
                        "kind": last["kind"],
                    }
                    if last["kind"] == "reasoning":
                        end_event["duration_ms"] = last["duration_ms"]
                    yield end_event

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

                # Step B: Settlement helper — one durable outcome per call.
                # All normal branches call it exactly once before yielding result.
                settled_ids: set[str] = set()

                def persist_outcome(
                    name: str, call_id: str, result: str, ok: bool, *,
                    interrupted: bool = False,
                ) -> dict[str, Any]:
                    """The single durable outcome writer, including synthetic results."""
                    meta = {} if interrupted else _result_meta(name, result)
                    activity = {"version": 1, "ok": ok, "meta": meta}
                    if interrupted:
                        activity["interrupted"] = True
                    self.sessions.append_message(
                        session_id, "tool", result, tool_call_id=call_id, activity=activity,
                    )
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
                    settled_ids.add(call_id)
                    return meta

                def settle(
                    name: str, args: Any, call: dict[str, Any], call_id: str,
                    result: str, ok: bool,
                ) -> dict[str, Any]:
                    """Persist a normal result before emitting its unchanged SSE payload."""
                    call["time"]["completed"] = _now_ms()
                    meta = persist_outcome(name, call_id, result, ok)
                    return {
                        "type": "tool_result",
                        "tool_call_id": call_id,
                        "name": name,
                        "arguments": args,
                        "ok": ok,
                        "result": result,
                        "meta": meta,
                        "alias": alias_map.get(name),
                    }

                # Step D: try/finally around the tool batch for interruption safety.
                try:
                    for call in tool_calls:
                        function = call.get("function", {})
                        name = function.get("name", "")
                        raw_args = function.get("arguments", "{}")
                        call_id = call["id"]
                        call_time = call.get("time")

                        # Step A: Mark requested and persist before request emission.
                        step_activity["calls"][call_id] = {"requested": True}
                        self.sessions.update_message_activity(
                            session_id, assistant_seq, step_activity
                        )

                        # Parse args; invalid args keep the raw string as the
                        # request payload so the renderer (which accepts
                        # strings) can show what was attempted.
                        try:
                            args = json.loads(raw_args or "{}")
                            if not isinstance(args, dict):
                                raise ValueError("tool arguments must be a JSON object")
                            request_args = args
                            invalid_result = None
                        except Exception as exc:
                            request_args = raw_args
                            invalid_result = f"Tool arguments were invalid JSON: {exc}"

                        yield {
                            "type": "tool_call",
                            "tool_call_id": call_id,
                            "name": name,
                            "arguments": request_args,
                            "alias": alias_map.get(name),
                        }

                        if invalid_result is not None:
                            yield settle(name, raw_args, call, call_id, invalid_result, False)
                            continue

                        if self.tools.is_mutating(name):
                            # Step C: Durable first-wins approval.
                            def _on_decision(
                                pending, approved, _call_id=call_id
                            ) -> None:
                                step_copy = copy.deepcopy(step_activity)
                                step_copy["calls"].setdefault(_call_id, {})[
                                    "approval"
                                ] = {
                                    "approval_id": pending.approval_id,
                                    "status": "approved" if approved else "denied",
                                }
                                self.sessions.update_message_activity(
                                    session_id, assistant_seq, step_copy
                                )
                                # Update in-memory after successful commit.
                                step_activity.clear()
                                step_activity.update(step_copy)

                            pending = await self.approvals.create(
                                session_id, name, args, on_decision=_on_decision
                            )
                            # Persist pending status before yielding approval_required.
                            step_activity["calls"][call_id]["approval"] = {
                                "approval_id": pending.approval_id,
                                "status": "pending",
                            }
                            self.sessions.update_message_activity(
                                session_id, assistant_seq, step_activity
                            )

                            try:
                                yield {
                                    "type": "approval_required",
                                    "approval_id": pending.approval_id,
                                    "tool_call_id": call_id,
                                    "name": name,
                                    "arguments": args,
                                    "alias": alias_map.get(name),
                                }
                                approved = await self.approvals.wait(pending)
                                # Emit approval_decision with stored terminal status.
                                yield {
                                    "type": "approval_decision",
                                    "approval_id": pending.approval_id,
                                    "tool_call_id": call_id,
                                    "name": name,
                                    "status": "approved" if approved else "denied",
                                    "alias": alias_map.get(name),
                                }
                            finally:
                                self.approvals.discard(pending)
                                # If still pending, mark cancelled.
                                if pending.approved is None:
                                    step_activity["calls"][call_id]["approval"][
                                        "status"
                                    ] = "cancelled"
                                    self.sessions.update_message_activity(
                                        session_id, assistant_seq, step_activity
                                    )

                            if not approved:
                                result = "User denied this mutating operation."
                                yield settle(name, args, call, call_id, result, False)
                                continue

                        call_time["ran"] = _now_ms()
                        try:
                            result = self.tools.execute(name, args)
                            ok = True
                        except ToolError as exc:
                            result = f"Tool error: {exc}"
                            ok = False

                        tracker.record_tool(name)

                        yield settle(name, args, call, call_id, result, ok)
                finally:
                    # Step D: Interrupted step safety.
                    # Persist synthetic outcomes for unsettled calls.
                    for call in tool_calls:
                        call_id = call["id"]
                        if call_id in settled_ids:
                            continue
                        result = "Tool call interrupted; no result recorded."
                        try:
                            persist_outcome((call.get("function") or {}).get("name", ""),
                                            call_id, result, False, interrupted=True)
                        except Exception:
                            logger.exception(
                                "Failed to persist interrupted tool outcome for %s",
                                call_id,
                            )

                # Stamp the turn's completion once all of its tool calls have
                # settled, then persist the updated timing.
                assistant_message["time"]["completed"] = _now_ms()
                self.sessions.update_message_content(
                    session_id, assistant_seq, assistant_message
                )

                yield {"type": "context", **self._context_usage(messages)}

            self._flush_usage(tracker, session_id)

            # Persist terminal_error on the last assistant row (if any).
            if last_step_activity is not None:
                error_msg = (
                    f"Agent stopped after {self.max_agent_steps} model/tool iterations."
                )
                last_step_activity["terminal_error"] = error_msg
                self.sessions.update_message_activity(
                    session_id, last_assistant_seq, last_step_activity
                )

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
