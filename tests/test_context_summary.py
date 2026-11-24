"""Tests for Stage 03 read-only context summary analytics.

Covers the ``build_context_summary`` pure function and the HTTP endpoint:

* new session (no messages, no calls) → estimated, zero occupancy
* system-only pre-response → configured_not_yet_sent
* two completed calls + title call → latest chat only, no lifetime leak
* trailing tool result after last assistant → mixed_estimate
* provider-based source flags
* estimated breakdown reconciliation invariants
* loaded_files: repeated read_file by path, search_text excluded,
  malformed context_meta, legacy unlinked rows
* over-capacity (>100%)
* 404 on missing session
* route ordering (search not captured by parameterized route)
* no writes on GET
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

from src.agent.context import build_context_summary
from src.agent.session_store import SessionStore
from src.agent.token_counter import count_message_tokens, estimate_tokens


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _meta(session_id="s1", title="Test", tokens_in=0, tokens_out=0,
          tokens_total=0, model=None):
    """Build a minimal SessionMeta-like object."""
    from src.agent.session.models import SessionMeta
    m = SessionMeta(
        session_id=session_id,
        title=title,
        model=model,
        created_at="2025-01-01 00:00:00",
        updated_at="2025-01-01 00:00:00",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_total=tokens_total,
        cost=0.0,
        tools_used="[]",
    )
    return m


def _tool_result(call_id, name, content, ok, path=None):
    """Build the decoded tool message projection returned by the store."""
    msg = {"role": "tool", "tool_call_id": call_id, "content": content,
           "context_meta": {"name": name, "ok": ok}}
    if name == "read_file" and ok and isinstance(path, str):
        msg["context_meta"]["path"] = path
    return msg


def _make_tool_specs():
    """Build a list of ToolSpec objects for testing."""
    from src.agent.tools.filesystem import ToolSpec
    return [
        ToolSpec("read_file", "Read a file", {"type": "object", "properties": {"path": {"type": "string"}}}),
        ToolSpec("write_file", "Write a file", {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}),
    ]


# ---------------------------------------------------------------------------
# Pure analytics tests
# ---------------------------------------------------------------------------

class BuildContextSummaryTests(TestCase):
    """Unit tests for build_context_summary (no I/O)."""

    def test_new_session_no_messages(self):
        """A fresh empty session shows 0 used, estimated source."""
        meta = _meta()
        result = build_context_summary(
            meta, [], [],
            limit=16384, model="local", tool_specs=_make_tool_specs(),
        )
        self.assertEqual(result["used_tokens"], 0)
        self.assertEqual(result["used_source"], "estimated")
        self.assertIsNone(result["last_call"])
        self.assertEqual(result["message_count"], 0)
        self.assertEqual(result["breakdown"]["previous_compaction"], 0)
        self.assertEqual(result["compaction_status"], "not_implemented")
        # System prompt is configured but not yet sent.
        self.assertEqual(result["system_prompt"]["source"], "configured_not_yet_sent")
        # Invariant: category sum == used_tokens.
        b = result["breakdown"]
        cat_sum = sum(b[k] for k in ("previous_compaction", "user_input",
                                       "loaded_files", "model_reasoning",
                                       "tool_usage", "other"))
        self.assertEqual(cat_sum, result["used_tokens"])

    def test_system_only_pre_response(self):
        """A session with only a system row shows configured prompt."""
        meta = _meta()
        msgs = [{"role": "system", "content": "You are a helper."}]
        result = build_context_summary(
            meta, msgs, [],
            limit=16384, model="local", tool_specs=[],
        )
        self.assertEqual(result["system_prompt"]["source"], "persisted")
        self.assertEqual(result["system_prompt"]["text"], "You are a helper.")
        self.assertEqual(result["used_source"], "estimated")

    def test_two_chat_calls_plus_title_uses_latest_chat_only(self):
        """Two calls 120+30 then 200+40: used=240, not 390 (no title leak)."""
        meta = _meta(tokens_in=320, tokens_out=70, tokens_total=390)
        calls = [
            {"session_id": "s1", "call_key": "chat:2", "kind": "chat",
             "assistant_seq": 2, "prompt_tokens": 120, "completion_tokens": 30,
             "prompt_source": "provider", "completion_source": "provider",
             "recorded_at": "2025-01-01 00:00:01"},
            {"session_id": "s1", "call_key": "chat:3", "kind": "chat",
             "assistant_seq": 3, "prompt_tokens": 200, "completion_tokens": 40,
             "prompt_source": "provider", "completion_source": "provider",
             "recorded_at": "2025-01-01 00:00:02"},
            {"session_id": "s1", "call_key": "title", "kind": "title",
             "assistant_seq": None, "prompt_tokens": 50, "completion_tokens": 10,
             "prompt_source": "provider", "completion_source": "provider",
             "recorded_at": "2025-01-01 00:00:03"},
        ]
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "more"},
            {"role": "assistant", "content": "more reply"},
        ]
        result = build_context_summary(
            meta, msgs, calls,
            limit=16384, model="local", tool_specs=[],
        )
        # Latest chat is 200+40 = 240, not the 390 lifetime total.
        self.assertEqual(result["used_tokens"], 240)
        self.assertEqual(result["used_source"], "provider_based")
        self.assertEqual(result["last_call"]["prompt_tokens"], 200)
        self.assertEqual(result["last_call"]["completion_tokens"], 40)
        # Invariant.
        b = result["breakdown"]
        cat_sum = sum(b[k] for k in ("previous_compaction", "user_input",
                                       "loaded_files", "model_reasoning",
                                       "tool_usage", "other"))
        self.assertEqual(cat_sum, result["used_tokens"])

    def test_trailing_tool_result_makes_mixed_estimate(self):
        """A trailing read_file result after the last assistant → mixed."""
        meta = _meta(tokens_in=120, tokens_out=30, tokens_total=150)
        calls = [
            {"session_id": "s1", "call_key": "chat:2", "kind": "chat",
             "assistant_seq": 2, "prompt_tokens": 120, "completion_tokens": 30,
             "prompt_source": "provider", "completion_source": "provider",
             "recorded_at": "2025-01-01 00:00:01"},
        ]
        msgs = [
            {"role": "user", "content": "read notes.md"},
            {"role": "assistant", "content": "ok",
             "tool_calls": [{"function": {"name": "read_file", "arguments": '{"path":"notes.md"}'}}]},
            _tool_result("call_1", "read_file", "file content here", True, path="notes.md"),
        ]
        result = build_context_summary(
            meta, msgs, calls,
            limit=16384, model="local", tool_specs=[],
        )
        # Trailing message (tool result) after last assistant → mixed.
        self.assertEqual(result["used_source"], "mixed_estimate")
        self.assertIn("trailing", result["used_explanation"].lower())
        # loaded_files should include notes.md.
        self.assertEqual(len(result["loaded_files"]), 1)
        self.assertEqual(result["loaded_files"][0]["path"], "notes.md")
        # Invariant.
        b = result["breakdown"]
        cat_sum = sum(b[k] for k in ("previous_compaction", "user_input",
                                       "loaded_files", "model_reasoning",
                                       "tool_usage", "other"))
        self.assertEqual(cat_sum, result["used_tokens"])

    def test_provider_based_source(self):
        """Both prompt and completion from provider, no trailing → provider_based."""
        meta = _meta(tokens_in=100, tokens_out=20, tokens_total=120)
        calls = [
            {"session_id": "s1", "call_key": "chat:1", "kind": "chat",
             "assistant_seq": 1, "prompt_tokens": 100, "completion_tokens": 20,
             "prompt_source": "provider", "completion_source": "provider",
             "recorded_at": "2025-01-01 00:00:01"},
        ]
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = build_context_summary(
            meta, msgs, calls,
            limit=16384, model="local", tool_specs=[],
        )
        self.assertEqual(result["used_source"], "provider_based")
        self.assertEqual(result["used_tokens"], 120)

    def test_mixed_source_when_estimated(self):
        """One component estimated → mixed_estimate."""
        meta = _meta(tokens_in=100, tokens_out=20, tokens_total=120)
        calls = [
            {"session_id": "s1", "call_key": "chat:1", "kind": "chat",
             "assistant_seq": 1, "prompt_tokens": 100, "completion_tokens": 20,
             "prompt_source": "provider", "completion_source": "estimated",
             "recorded_at": "2025-01-01 00:00:01"},
        ]
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = build_context_summary(
            meta, msgs, calls,
            limit=16384, model="local", tool_specs=[],
        )
        self.assertEqual(result["used_source"], "mixed_estimate")

    def test_loaded_files_repeated_path_counts_twice(self):
        """Reading the same path twice counts twice in loaded_files."""
        meta = _meta()
        msgs = [
            {"role": "user", "content": "read a"},
            {"role": "assistant", "content": "ok"},
            _tool_result("c1", "read_file", "content A", True, path="a.md"),
            {"role": "user", "content": "read a again"},
            {"role": "assistant", "content": "ok"},
            _tool_result("c2", "read_file", "content A again longer", True, path="a.md"),
        ]
        result = build_context_summary(
            meta, msgs, [],
            limit=16384, model="local", tool_specs=[],
        )
        self.assertEqual(len(result["loaded_files"]), 1)
        self.assertEqual(result["loaded_files"][0]["occurrences"], 2)
        # Both reads counted; tokens are positive and scaled to the breakdown.
        self.assertGreater(result["loaded_files"][0]["tokens"], 0)
        # Invariant: loaded_files sum matches breakdown.
        self.assertEqual(
            sum(f["tokens"] for f in result["loaded_files"]),
            result["breakdown"]["loaded_files"],
        )

    def test_search_text_excluded_from_loaded_files(self):
        """search_text results are not treated as loaded files."""
        meta = _meta()
        msgs = [
            {"role": "user", "content": "search for x"},
            {"role": "assistant", "content": "ok"},
            _tool_result("c1", "search_text", "result lines", True),
        ]
        result = build_context_summary(
            meta, msgs, [],
            limit=16384, model="local", tool_specs=[],
        )
        self.assertEqual(result["loaded_files"], [])

    def test_malformed_context_meta_not_a_file(self):
        """A tool row with malformed context_meta is not a loaded file."""
        meta = _meta()
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "tool", "tool_call_id": "x", "content": "data",
             "context_meta": {"name": "read_file", "ok": True}},
        ]
        result = build_context_summary(
            meta, msgs, [],
            limit=16384, model="local", tool_specs=[],
        )
        # No path in context_meta → not a file row.
        self.assertEqual(result["loaded_files"], [])

    def test_legacy_unlinked_tool_row(self):
        """A legacy plain-text tool row is not a file, no exception."""
        meta = _meta()
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "tool", "content": "raw legacy text"},
        ]
        result = build_context_summary(
            meta, msgs, [],
            limit=16384, model="local", tool_specs=[],
        )
        self.assertEqual(result["loaded_files"], [])
        # No exception raised.

    def test_over_capacity(self):
        """used > limit → percentage clamped to 100, free=0."""
        meta = _meta(tokens_in=20000, tokens_out=5000, tokens_total=25000)
        calls = [
            {"session_id": "s1", "call_key": "chat:1", "kind": "chat",
             "assistant_seq": 1, "prompt_tokens": 20000, "completion_tokens": 5000,
             "prompt_source": "provider", "completion_source": "provider",
             "recorded_at": "2025-01-01 00:00:01"},
        ]
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        result = build_context_summary(
            meta, msgs, calls,
            limit=10000, model="local", tool_specs=[],
        )
        self.assertEqual(result["used_tokens"], 25000)
        self.assertEqual(result["free_tokens"], 0)
        self.assertEqual(result["percentage"], 100.0)

    def test_zero_token_report(self):
        """A call with 0 tokens is valid (exact zero)."""
        meta = _meta(tokens_in=0, tokens_out=0, tokens_total=0)
        calls = [
            {"session_id": "s1", "call_key": "chat:1", "kind": "chat",
             "assistant_seq": 1, "prompt_tokens": 0, "completion_tokens": 0,
             "prompt_source": "provider", "completion_source": "provider",
             "recorded_at": "2025-01-01 00:00:01"},
        ]
        msgs = []
        result = build_context_summary(
            meta, msgs, calls,
            limit=16384, model="local", tool_specs=[],
        )
        self.assertEqual(result["used_tokens"], 0)
        self.assertEqual(result["used_source"], "provider_based")

    def test_lifetime_source_none(self):
        """No calls → lifetime source 'none'."""
        meta = _meta()
        result = build_context_summary(
            meta, [], [],
            limit=16384, model="local", tool_specs=[],
        )
        self.assertEqual(result["session_lifetime"]["source"], "none")

    def test_lifetime_source_mixed_legacy(self):
        """meta.tokens_total != sum(calls) → mixed_legacy."""
        meta = _meta(tokens_in=999, tokens_out=999, tokens_total=1998)
        calls = [
            {"session_id": "s1", "call_key": "chat:1", "kind": "chat",
             "assistant_seq": 1, "prompt_tokens": 100, "completion_tokens": 50,
             "prompt_source": "provider", "completion_source": "provider",
             "recorded_at": "2025-01-01 00:00:01"},
        ]
        result = build_context_summary(
            meta, [], calls,
            limit=16384, model="local", tool_specs=[],
        )
        self.assertEqual(result["session_lifetime"]["source"], "mixed_legacy")

    def test_breakdown_reconciliation_invariant(self):
        """Category sums always equal used_tokens."""
        meta = _meta(tokens_in=100, tokens_out=50, tokens_total=150)
        calls = [
            {"session_id": "s1", "call_key": "chat:1", "kind": "chat",
             "assistant_seq": 1, "prompt_tokens": 100, "completion_tokens": 50,
             "prompt_source": "provider", "completion_source": "provider",
             "recorded_at": "2025-01-01 00:00:01"},
        ]
        msgs = [
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi there",
             "reasoning_content": "thinking..."},
            _tool_result("c1", "read_file", "file data", True, path="f.md"),
        ]
        result = build_context_summary(
            meta, msgs, calls,
            limit=16384, model="local", tool_specs=_make_tool_specs(),
        )
        b = result["breakdown"]
        cat_sum = sum(b[k] for k in ("previous_compaction", "user_input",
                                       "loaded_files", "model_reasoning",
                                       "tool_usage", "other"))
        self.assertEqual(cat_sum, result["used_tokens"])
        self.assertEqual(sum(f["tokens"] for f in result["loaded_files"]),
                          b["loaded_files"])
        self.assertEqual(result["free_tokens"],
                          max(0, result["context_limit_tokens"] - result["used_tokens"]))


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

class ContextEndpointTests(TestCase):
    """Integration tests for the GET /api/sessions/{id}/context route."""

    def _store(self, directory: str) -> SessionStore:
        return SessionStore(Path(directory) / "sessions.db")

    def test_404_missing_session(self):
        """GET context on a non-existent session returns 404."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.sessions import create_sessions_router

        with TemporaryDirectory() as d:
            store = self._store(d)
            try:
                app = FastAPI()
                app.include_router(create_sessions_router(store))
                client = TestClient(app)
                resp = client.get("/api/sessions/ghost/context")
                self.assertEqual(resp.status_code, 404)
            finally:
                store.close()

    def test_context_endpoint_returns_summary(self):
        """GET context returns a valid summary for an existing session."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.sessions import create_sessions_router

        with TemporaryDirectory() as d:
            store = self._store(d)
            try:
                store.create("s1", title="Test")
                store.append_message("s1", "user", "hello")
                store.append_message("s1", "assistant", "hi")
                store.record_model_call("s1", "chat:1", "chat", 1,
                                        {"prompt_tokens": 100, "completion_tokens": 20,
                                         "prompt_source": "provider",
                                         "completion_source": "provider"})

                app = FastAPI()
                app.include_router(create_sessions_router(store))
                client = TestClient(app)
                resp = client.get("/api/sessions/s1/context")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["session_id"], "s1")
                self.assertEqual(data["session_title"], "Test")
                self.assertEqual(data["context_limit_tokens"], 16384)
                self.assertEqual(data["used_tokens"], 120)
                self.assertEqual(data["used_source"], "provider_based")
                # Invariant.
                b = data["breakdown"]
                cat_sum = sum(b[k] for k in ("previous_compaction", "user_input",
                                               "loaded_files", "model_reasoning",
                                               "tool_usage", "other"))
                self.assertEqual(cat_sum, data["used_tokens"])
            finally:
                store.close()

    def test_no_writes_on_get(self):
        """GET context does not create or modify any rows."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.sessions import create_sessions_router

        with TemporaryDirectory() as d:
            store = self._store(d)
            try:
                store.create("s1", title="Test")
                app = FastAPI()
                app.include_router(create_sessions_router(store))
                client = TestClient(app)
                resp = client.get("/api/sessions/s1/context")
                self.assertEqual(resp.status_code, 200)
                # No new messages were created (messages() would seed system).
                msgs = store.get_messages("s1")
                self.assertEqual(len(msgs), 0)
            finally:
                store.close()

    def test_route_ordering_search_not_captured(self):
        """/api/sessions/search must not be captured by {session_id}."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.sessions import create_sessions_router

        with TemporaryDirectory() as d:
            store = self._store(d)
            try:
                store.create("s1", title="Find me")
                app = FastAPI()
                app.include_router(create_sessions_router(store))
                client = TestClient(app)
                resp = client.get("/api/sessions/search", params={"q": "Find"})
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertIn("sessions", data)
                self.assertEqual(len(data["sessions"]), 1)
            finally:
                store.close()
