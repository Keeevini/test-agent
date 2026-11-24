"""Durable tool + approval lifecycle tests (Stage 02).

Covers the four lifecycle steps implemented in ``LocalAgent.stream_turn``:

- Step A (``test_ids_*``) — stable, unique tool-call IDs normalized on the
  assistant call objects before the assistant row is persisted.
- Step B (``test_outcomes_*``) — one durable tool outcome per normal attempted
  call, persisted before the associated SSE event is emitted.
- Step C (``test_approval_*``) — durable first-wins approval decisions.
- Step D (``test_interruption_*``) — interrupted-step safety: a generator that
  is closed mid-batch leaves truthful stored outcomes and no dangling pending
  approvals.

Uses a fake model stream, a temporary workspace, a temporary database, and
disables title generation, so no real server, model, or personal data is
touched. The model client and store are closed in teardown.
"""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.agent.approvals import ApprovalManager
from src.agent.core import LocalAgent
from src.agent.session_store import SessionStore
from src.agent.tools import WorkspaceTools


class FakeModelClient:
    """Stands in for :class:`ModelClient`: serves queued ``(assistant, events)`` turns.

    ``stream`` mirrors the real client's signature: it is awaited and returns a
    finalized ``assistant`` dict plus an async iterator of stream events.
    """

    def __init__(self) -> None:
        self._turns: list[tuple[dict, list[dict]]] = []

    def queue(self, assistant: dict, events: list[dict] | None = None) -> None:
        self._turns.append((assistant, events or []))

    async def stream(self, messages: list[dict]):
        # The agent loop calls ``stream`` once per model step. When no more
        # turns are queued, the model is "done": a final answer with no tool
        # calls, which terminates the loop. This lets a test queue only the
        # interesting turns.
        if self._turns:
            assistant, events = self._turns.pop(0)
        else:
            assistant, events = (
                {"role": "assistant", "content": "done", "tool_calls": []},
                [],
            )

        assistant.setdefault("usage", {"prompt_tokens": 10, "completion_tokens": 2,
                                       "prompt_source": "provider", "completion_source": "provider"})
        async def events_iter():
            for e in events:
                yield e

        return assistant, events_iter()

    async def close(self) -> None:
        return None


def _tool_call(name: str, args, call_id: str | None = None) -> dict:
    """Build one assistant tool call in the model protocol shape.

    ``args`` is a dict (serialized to a raw JSON string) or an already-raw
    string (to exercise invalid-JSON handling). ``call_id=None`` omits the ID.
    """
    raw = args if isinstance(args, str) else json.dumps(args)
    call: dict = {
        "type": "function",
        "function": {"name": name, "arguments": raw},
    }
    if call_id is not None:
        call["id"] = call_id
    return call


class ToolActivityTests(TestCase):
    """Shared fixture: temp workspace, temp DB, fake model, titles disabled."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        base = Path(self._tmp.name)
        self._db_path = base / "sessions.db"
        self._workspace = base / "workspace"
        self._tools = WorkspaceTools(self._workspace)
        self._approvals = ApprovalManager()
        self._sessions = SessionStore(str(self._db_path))
        self._agent = LocalAgent(
            llama_base_url="http://localhost:8080",
            model="local",
            tools=self._tools,
            approvals=self._approvals,
            sessions=self._sessions,
        )
        # Replace the real model client with a fake; keep the real one to close.
        self._real_model_client = self._agent.model_client
        self._agent.model_client = FakeModelClient()
        # Disable title generation (no real server in tests).
        self._agent._schedule_title = lambda *a, **k: None

    def tearDown(self) -> None:
        self._sessions.close()
        self._tmp.cleanup()
        # Close the real httpx client that LocalAgent.__init__ created.
        asyncio.run(self._real_model_client.close())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_history(self, session_id: str) -> list[dict]:
        """Read the history projection from a fresh store over the same DB."""
        store = SessionStore(str(self._db_path))
        try:
            return store.get_messages(session_id)
        finally:
            store.close()

    async def _consume(
        self,
        session_id: str,
        user_text: str,
        approve=None,
    ) -> list[dict]:
        """Consume ``stream_turn``, resolving approvals as they appear.

        ``approve`` is a dict mapping ``tool_call_id`` -> bool, a single bool
        applied to every approval, or ``None`` (deny by default).
        """
        events: list[dict] = []
        agen = self._agent.stream_turn(session_id, user_text)
        async for event in agen:
            events.append(event)
            if event.get("type") == "approval_required":
                decision = approve
                if isinstance(approve, dict):
                    decision = approve.get(event.get("tool_call_id"), False)
                await self._approvals.resolve(
                    event["approval_id"], bool(decision)
                )
        return events


class IdsTests(ToolActivityTests):
    """Step A: stable, unique tool-call IDs (identity and request emission)."""

    def test_ids_missing_id_normalized(self):
        async def scenario():
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("list_directory", {"path": "."}, call_id=None)
                    ],
                }
            )
            events = await self._consume("s1", "list the directory")
            tc = next(e for e in events if e["type"] == "tool_call")
            self.assertTrue(tc["tool_call_id"].startswith("call_"))
            # The stored assistant row must carry the same canonical ID.
            history = self._read_history("s1")
            assistant = next(
                m
                for m in history
                if m["role"] == "assistant" and m.get("tool_calls")
            )
            self.assertEqual(tc["tool_call_id"], assistant["tool_calls"][0]["id"])
        asyncio.run(scenario())

    def test_ids_duplicate_ids_replaced(self):
        async def scenario():
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("list_directory", {"path": "."}, call_id="dup"),
                        _tool_call("list_directory", {"path": "."}, call_id="dup"),
                    ],
                }
            )
            events = await self._consume("s2", "list twice")
            ids = [
                e["tool_call_id"] for e in events if e["type"] == "tool_call"
            ]
            self.assertEqual(len(ids), 2)
            self.assertNotEqual(
                ids[0], ids[1], "duplicate IDs within a batch must be replaced"
            )
        asyncio.run(scenario())

    def test_ids_invalid_args_request_emitted(self):
        async def scenario():
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("list_directory", "not-json", call_id="c1")
                    ],
                }
            )
            events = await self._consume("s3", "bad args")
            tc = next(e for e in events if e["type"] == "tool_call")
            self.assertEqual(tc["tool_call_id"], "c1")
            # The request is emitted with the original raw argument string.
            self.assertEqual(tc["arguments"], "not-json")
        asyncio.run(scenario())


class OutcomesTests(ToolActivityTests):
    """Step B: one durable tool outcome per normal attempted call."""

    def test_outcomes_success(self):
        async def scenario():
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("list_directory", {"path": "."}, call_id="c1")
                    ],
                }
            )
            events = await self._consume("s1", "list it")
            tr = next(e for e in events if e["type"] == "tool_result")
            self.assertTrue(tr["ok"])
            self.assertEqual(tr["tool_call_id"], "c1")
            # Reopen the DB: the tool row persists with ok=True and the ID.
            history = self._read_history("s1")
            tool = next(m for m in history if m["role"] == "tool")
            self.assertEqual(tool["activity"]["ok"], True)
            self.assertEqual(tool["activity"]["tool_call_id"], "c1")
        asyncio.run(scenario())

    def test_outcomes_tool_failure(self):
        async def scenario():
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call(
                            "read_file", {"path": "missing.txt"}, call_id="c1"
                        )
                    ],
                }
            )
            events = await self._consume("s2", "read it")
            tr = next(e for e in events if e["type"] == "tool_result")
            self.assertFalse(tr["ok"])
            self.assertIn("Tool error", tr["result"])
            history = self._read_history("s2")
            tool = next(m for m in history if m["role"] == "tool")
            self.assertEqual(tool["activity"]["ok"], False)
        asyncio.run(scenario())

    def test_outcomes_invalid_args(self):
        async def scenario():
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("list_directory", "not-json", call_id="c1")
                    ],
                }
            )
            events = await self._consume("s3", "bad args")
            tr = next(e for e in events if e["type"] == "tool_result")
            self.assertFalse(tr["ok"])
            self.assertIn("invalid JSON", tr["result"])
            history = self._read_history("s3")
            tool = next(m for m in history if m["role"] == "tool")
            self.assertEqual(tool["activity"]["ok"], False)
            self.assertEqual(tool["activity"]["tool_call_id"], "c1")
        asyncio.run(scenario())

    def test_outcomes_mixed_batch_reopen(self):
        async def scenario():
            # success, failure, invalid, denied — in this order.
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("list_directory", {"path": "."}, call_id="ok1"),
                        _tool_call(
                            "read_file", {"path": "missing.txt"}, call_id="fail1"
                        ),
                        _tool_call("list_directory", "not-json", call_id="bad1"),
                        _tool_call(
                            "write_file",
                            {"path": "x.txt", "content": "x"},
                            call_id="deny1",
                        ),
                    ],
                }
            )
            events = await self._consume(
                "s4", "mixed", approve={"deny1": False}
            )
            results = [e for e in events if e["type"] == "tool_result"]
            self.assertEqual(len(results), 4)
            self.assertTrue(results[0]["ok"])
            self.assertFalse(results[1]["ok"])
            self.assertFalse(results[2]["ok"])
            self.assertFalse(results[3]["ok"])
            # Reopen: tool rows in order with matching IDs and ok flags.
            history = self._read_history("s4")
            tools = [m for m in history if m["role"] == "tool"]
            self.assertEqual(len(tools), 4)
            self.assertEqual(
                [t["activity"]["tool_call_id"] for t in tools],
                ["ok1", "fail1", "bad1", "deny1"],
            )
            self.assertEqual(
                [t["activity"]["ok"] for t in tools], [True, False, False, False]
            )
        asyncio.run(scenario())

    def test_outcomes_reopen_continuation(self):
        async def scenario():
            # First turn: one successful tool call.
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("list_directory", {"path": "."}, call_id="c1")
                    ],
                }
            )
            await self._consume("s5", "first turn")
            # Reopen the DB: the stored protocol must be structurally valid.
            history = self._read_history("s5")
            assistant = next(
                m
                for m in history
                if m["role"] == "assistant" and m.get("tool_calls")
            )
            tool_calls = assistant["tool_calls"]
            self.assertEqual(len(tool_calls), 1)
            self.assertTrue(tool_calls[0]["id"])
            # Start a second turn against the reopened DB using the fake model.
            self._agent.model_client.queue(
                {"role": "assistant", "content": "done", "tool_calls": []}
            )
            events = await self._consume("s5", "second turn")
            self.assertTrue(any(e["type"] == "done" for e in events))
        asyncio.run(scenario())


class ApprovalTests(ToolActivityTests):
    """Step C: durable first-wins approval decisions."""

    def test_approval_first_wins(self):
        async def scenario():
            item = await self._approvals.create(
                "s1", "write_file", {"path": "x.txt", "content": "x"}
            )
            first = await self._approvals.resolve(item.approval_id, True)
            second = await self._approvals.resolve(item.approval_id, False)
            # Exactly one is accepted; the first wins.
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertTrue(item.approved)
        asyncio.run(scenario())

    def test_approval_double_resolve_rejected(self):
        async def scenario():
            item = await self._approvals.create("s1", "write_file", {})
            self.assertTrue(await self._approvals.resolve(item.approval_id, True))
            self.assertFalse(await self._approvals.resolve(item.approval_id, True))
            self.assertFalse(await self._approvals.resolve(item.approval_id, False))
            # The decision is unchanged.
            self.assertTrue(item.approved)
        asyncio.run(scenario())

    def test_approval_denied_no_mutation(self):
        async def scenario():
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call(
                            "write_file",
                            {"path": "secret.txt", "content": "x"},
                            call_id="c1",
                        )
                    ],
                }
            )
            events = await self._consume("s1", "write it", approve=False)
            results = [e for e in events if e["type"] == "tool_result"]
            self.assertFalse(results[0]["ok"])
            self.assertIn("denied", results[0]["result"])
            # No file was written.
            self.assertFalse((self._workspace / "secret.txt").exists())
        asyncio.run(scenario())

    def test_approval_callback_exception_blocks_decision(self):
        async def scenario():
            def bad_cb(pending, approved):
                raise RuntimeError("persistence failed")

            item = await self._approvals.create(
                "s1", "write_file", {}, on_decision=bad_cb
            )
            # The callback exception propagates; the decision is not written.
            with self.assertRaises(RuntimeError):
                await self._approvals.resolve(item.approval_id, True)
            self.assertIsNone(item.approved)
            self.assertFalse(item.event.is_set())
        asyncio.run(scenario())

    def test_approval_pending_cleanup(self):
        async def scenario():
            item = await self._approvals.create("s1", "write_file", {})
            self.assertIn(item.approval_id, self._approvals._pending)
            self._approvals.discard(item)
            self.assertNotIn(item.approval_id, self._approvals._pending)
            # discard is idempotent.
            self._approvals.discard(item)
            # resolve on a discarded item returns False.
            self.assertFalse(await self._approvals.resolve(item.approval_id, True))
        asyncio.run(scenario())


class InterruptionTests(ToolActivityTests):
    """Step D: interrupted-step safety."""

    def test_interruption_pending(self):
        async def scenario():
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call(
                            "write_file",
                            {"path": "x.txt", "content": "x"},
                            call_id="c1",
                        )
                    ],
                }
            )
            agen = self._agent.stream_turn("s1", "write it")
            # Consume until approval_required, then close the generator.
            async for event in agen:
                if event["type"] == "approval_required":
                    break
            await agen.aclose()
            # The pending item must be removed.
            self.assertEqual(len(self._approvals._pending), 0)
            # Stored status is cancelled; a synthetic interrupted outcome.
            history = self._read_history("s1")
            assistant = next(
                m
                for m in history
                if m["role"] == "assistant" and m.get("tool_calls")
            )
            calls = assistant["activity"]["calls"]
            self.assertEqual(calls["c1"]["approval"]["status"], "cancelled")
            tools = [m for m in history if m["role"] == "tool"]
            self.assertEqual(len(tools), 1)
            self.assertTrue(tools[0]["activity"].get("interrupted"))
            self.assertFalse(tools[0]["activity"]["ok"])
            # No file was written.
            self.assertFalse((self._workspace / "x.txt").exists())
        asyncio.run(scenario())

    def test_interruption_confirmed(self):
        async def scenario():
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call(
                            "write_file",
                            {"path": "y.txt", "content": "y"},
                            call_id="c1",
                        )
                    ],
                }
            )
            agen = self._agent.stream_turn("s2", "write it")
            events = []
            async for event in agen:
                events.append(event)
                if event["type"] == "approval_required":
                    await self._approvals.resolve(event["approval_id"], True)
                if event["type"] == "approval_decision":
                    # Close after the decision, before the result.
                    break
            await agen.aclose()
            # The decision remains approved (not cancelled).
            history = self._read_history("s2")
            assistant = next(
                m
                for m in history
                if m["role"] == "assistant" and m.get("tool_calls")
            )
            calls = assistant["activity"]["calls"]
            self.assertEqual(calls["c1"]["approval"]["status"], "approved")
            # The tool never executed; a synthetic interrupted outcome.
            self.assertFalse((self._workspace / "y.txt").exists())
            tools = [m for m in history if m["role"] == "tool"]
            self.assertEqual(len(tools), 1)
            self.assertTrue(tools[0]["activity"].get("interrupted"))
            self.assertFalse(tools[0]["activity"]["ok"])
        asyncio.run(scenario())


class PartsTests(ToolActivityTests):
    """Stage 05: ordered part metadata and authoritative end events.

    Verifies ``stream_turn`` records contiguous reasoning/content runs in the
    assistant step activity, emits ``model_part_end`` events in order, and
    persists a ``terminal_error`` on the last assistant row at the step cap.
    """

    def test_parts_interleaved_runs(self):
        async def scenario():
            # Interleaved deltas: reasoning a😀, reasoning b, text x,
            # reasoning z, text y. Aggregates: reasoning 'a😀bz', content 'xy'.
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "xy",
                    "reasoning_content": "a\U0001F600bz",
                    "tool_calls": [],
                },
                [
                    {"type": "reasoning_delta", "text": "a\U0001F600"},
                    {"type": "reasoning_delta", "text": "b"},
                    {"type": "content_delta", "text": "x"},
                    {"type": "reasoning_delta", "text": "z"},
                    {"type": "content_delta", "text": "y"},
                ],
            )
            events = await self._consume("s1", "interleaved")
            # Four model_part_end events in run order: reasoning, content,
            # reasoning, content.
            ends = [e for e in events if e["type"] == "model_part_end"]
            self.assertEqual(
                [e["kind"] for e in ends],
                ["reasoning", "content", "reasoning", "content"],
            )
            # Reasoning runs carry a nonnegative duration; content runs omit it.
            self.assertGreaterEqual(ends[0]["duration_ms"], 0)
            self.assertGreaterEqual(ends[2]["duration_ms"], 0)
            self.assertNotIn("duration_ms", ends[1])
            self.assertNotIn("duration_ms", ends[3])
            # Stored parts match the code-point run lengths (a,😀,b | x | z | y).
            history = self._read_history("s1")
            assistant = next(m for m in history if m["role"] == "assistant")
            parts = assistant["activity"]["parts"]
            self.assertEqual([p["chars"] for p in parts], [3, 1, 1, 1])
            self.assertEqual(
                [p["kind"] for p in parts],
                ["reasoning", "content", "reasoning", "content"],
            )
            # Parts are history-only metadata, never a model payload field.
            self.assertNotIn("parts", assistant)
            # Aggregates are stored verbatim.
            self.assertEqual(assistant["reasoning_content"], "a\U0001F600bz")
            self.assertEqual(assistant["content"], "xy")
        asyncio.run(scenario())

    def test_parts_no_text_only_tools(self):
        async def scenario():
            # No reasoning/content deltas: no runs, no end events.
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("list_directory", {"path": "."}, call_id="c1")
                    ],
                }
            )
            events = await self._consume("s1", "only tools")
            ends = [e for e in events if e["type"] == "model_part_end"]
            self.assertEqual(len(ends), 0)
            history = self._read_history("s1")
            assistant = next(
                m
                for m in history
                if m["role"] == "assistant" and m.get("tool_calls")
            )
            self.assertEqual(assistant["activity"]["parts"], [])
        asyncio.run(scenario())

    def test_parts_only_content(self):
        async def scenario():
            # A single content run: one end event, one stored part.
            self._agent.model_client.queue(
                {
                    "role": "assistant",
                    "content": "hello",
                    "tool_calls": [],
                },
                [
                    {"type": "content_delta", "text": "he"},
                    {"type": "content_delta", "text": "llo"},
                ],
            )
            events = await self._consume("s1", "only content")
            ends = [e for e in events if e["type"] == "model_part_end"]
            self.assertEqual(len(ends), 1)
            self.assertEqual(ends[0]["kind"], "content")
            self.assertNotIn("duration_ms", ends[0])
            history = self._read_history("s1")
            assistant = next(m for m in history if m["role"] == "assistant")
            parts = assistant["activity"]["parts"]
            self.assertEqual([p["chars"] for p in parts], [5])
            self.assertEqual(parts[0]["kind"], "content")
            self.assertNotIn("duration_ms", parts[0])
        asyncio.run(scenario())

    def test_parts_cap_error(self):
        async def scenario():
            # Exhaust a small step cap; the terminal error is persisted on the
            # last assistant row before the error event is emitted.
            agent = LocalAgent(
                llama_base_url="http://localhost:8080",
                model="local",
                tools=self._tools,
                approvals=self._approvals,
                sessions=self._sessions,
                max_agent_steps=2,
            )
            real_client = agent.model_client
            fake = FakeModelClient()
            agent.model_client = fake
            agent._schedule_title = lambda *a, **k: None
            for i in range(2):
                fake.queue(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            _tool_call(
                                "list_directory", {"path": "."}, call_id=f"c{i}"
                            )
                        ],
                    }
                )
            events = []
            agen = agent.stream_turn("s1", "loop")
            async for event in agen:
                events.append(event)
            self.assertTrue(any(e["type"] == "error" for e in events))
            history = self._read_history("s1")
            assistants = [
                m
                for m in history
                if m["role"] == "assistant" and m.get("tool_calls")
            ]
            last = assistants[-1]
            self.assertIn("terminal_error", last["activity"])
            self.assertEqual(
                last["activity"]["terminal_error"],
                "Agent stopped after 2 model/tool iterations.",
            )
            await real_client.close()
        asyncio.run(scenario())

    def test_parts_zero_steps_no_assistant_row(self):
        async def scenario():
            # max_agent_steps=0: no assistant row is created, the terminal
            # error is skipped, and the original error is still emitted.
            agent = LocalAgent(
                llama_base_url="http://localhost:8080",
                model="local",
                tools=self._tools,
                approvals=self._approvals,
                sessions=self._sessions,
                max_agent_steps=0,
            )
            real_client = agent.model_client
            fake = FakeModelClient()
            agent.model_client = fake
            agent._schedule_title = lambda *a, **k: None
            events = []
            agen = agent.stream_turn("s1", "zero steps")
            async for event in agen:
                events.append(event)
            self.assertTrue(any(e["type"] == "error" for e in events))
            history = self._read_history("s1")
            # Only the seeded user message; no assistant row.
            self.assertEqual(
                [m for m in history if m["role"] == "assistant"], []
            )
            await real_client.close()
        asyncio.run(scenario())
