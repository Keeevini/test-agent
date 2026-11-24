"""Tests for Stage 06 read-only raw-messages paginated endpoint.

Covers the ``get_raw_message_page`` repository query, the HTTP endpoint,
and the contracts the Stage 06 handoff requires:

* pagination: 5 rows, limit=2, offsets 0/2/4/6 → [0,1],[2,3],[4],[]
* 404 on missing session (no write, no seed)
* literal tool content, explicit ID, activity, and derived tool name
* usage/time preservation on assistant rows
* query bounds: offset ge=0, limit ge=1 le=50
* large content and escape characters round-trip unchanged
* existing GET /api/sessions/{id}/messages shape preserved
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

from src.agent.session_store import SessionStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_store(directory: str) -> SessionStore:
    return SessionStore(Path(directory) / "sessions.db")


# ---------------------------------------------------------------------------
# Repository-level pagination tests
# ---------------------------------------------------------------------------

class PaginationTests(TestCase):
    """Bounded page queries return correct sequences and has_more."""

    def test_five_rows_limit_two_offsets(self):
        """5 rows, limit=2, offsets 0/2/4/6 → [0,1],[2,3],[4],[]."""
        with TemporaryDirectory() as d:
            store = _make_store(d)
            try:
                store.create("s1", title="Test")
                # Insert 5 rows with distinct seq values 0-4.
                for i in range(5):
                    store.append_message("s1", "user", f"msg {i}")

                page0 = store.get_raw_message_page("s1", 0, 2)
                page2 = store.get_raw_message_page("s1", 2, 2)
                page4 = store.get_raw_message_page("s1", 4, 2)
                page6 = store.get_raw_message_page("s1", 6, 2)

                self.assertEqual([m["seq"] for m in page0["messages"]], [0, 1])
                self.assertEqual([m["seq"] for m in page2["messages"]], [2, 3])
                self.assertEqual([m["seq"] for m in page4["messages"]], [4])
                self.assertEqual(page6["messages"], [])

                # Totals stable.
                for page in (page0, page2, page4, page6):
                    self.assertEqual(page["total"], 5)

                # has_more: offset + len < total
                self.assertTrue(page0["has_more"])
                self.assertTrue(page2["has_more"])
                self.assertFalse(page4["has_more"])
                self.assertFalse(page6["has_more"])
            finally:
                store.close()

    def test_empty_session(self):
        """A session with no messages returns empty page, total=0."""
        with TemporaryDirectory() as d:
            store = _make_store(d)
            try:
                store.create("s1", title="Test")
                page = store.get_raw_message_page("s1", 0, 20)
                self.assertEqual(page["total"], 0)
                self.assertEqual(page["messages"], [])
                self.assertFalse(page["has_more"])
            finally:
                store.close()

    def test_offset_beyond_total(self):
        """Offset beyond total returns empty messages, total unchanged."""
        with TemporaryDirectory() as d:
            store = _make_store(d)
            try:
                store.create("s1", title="Test")
                store.append_message("s1", "user", "hi")
                page = store.get_raw_message_page("s1", 100, 20)
                self.assertEqual(page["total"], 1)
                self.assertEqual(page["messages"], [])
                self.assertFalse(page["has_more"])
            finally:
                store.close()


# ---------------------------------------------------------------------------
# Raw message shape tests
# ---------------------------------------------------------------------------

class RawMessageShapeTests(TestCase):
    """Each message in a page carries seq, role, created_ms, raw."""

    def test_user_message_shape(self):
        """A user message row has role=user, created_ms=None, raw dict."""
        with TemporaryDirectory() as d:
            store = _make_store(d)
            try:
                store.create("s1", title="Test")
                store.append_message("s1", "user", "hello")
                page = store.get_raw_message_page("s1", 0, 20)
                msg = page["messages"][0]
                self.assertEqual(msg["seq"], 0)
                self.assertEqual(msg["role"], "user")
                self.assertIsNone(msg["created_ms"])
                self.assertEqual(msg["raw"], {"role": "user", "content": "hello"})
            finally:
                store.close()

    def test_assistant_json_content_preserved(self):
        """An assistant message with usage/time round-trips via to_dict."""
        with TemporaryDirectory() as d:
            store = _make_store(d)
            try:
                store.create("s1", title="Test")
                assistant = {
                    "role": "assistant",
                    "content": "Hi there",
                    "time": {"created": 1795461000000, "completed": 1795461001000},
                }
                usage = {"prompt_tokens": 10, "completion_tokens": 2, "prompt_source": "provider", "completion_source": "provider", "cached_prompt_tokens": None, "reasoning_tokens": 0}
                store.append_assistant_with_usage("s1", assistant, usage)
                page = store.get_raw_message_page("s1", 0, 20)
                msg = page["messages"][0]
                self.assertEqual(msg["role"], "assistant")
                self.assertEqual(msg["raw"]["role"], "assistant")
                self.assertEqual(msg["raw"]["content"], "Hi there")
                self.assertEqual(msg["raw"]["usage"], usage)
                self.assertEqual(msg["created_ms"], 1795461000000)
            finally:
                store.close()

    def test_tool_result_json_round_trip(self):
        with TemporaryDirectory() as d:
            store = _make_store(d)
            try:
                store.create("s1")
                call = {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"notes.md"}'}}
                store.append_message("s1", "assistant", {"role": "assistant", "content": "", "tool_calls": [call]})
                content = '{"role":"tool","content":"literal","tool_call_id":"fake"}'
                store.append_message("s1", "tool", content, tool_call_id="call_1", activity={"version": 1, "ok": True})
                # Page begins mid-batch; actual name is available without copying metadata.
                entry = store.get_raw_message_page("s1", 1, 1)["messages"][0]
                self.assertEqual(entry["tool_name"], "read_file")
                self.assertEqual(entry["raw"], {"role": "tool", "content": content, "tool_call_id": "call_1", "activity": {"version": 1, "ok": True}})
            finally:
                store.close()

    def test_large_content_with_escapes(self):
        """Content with <, ", backticks, and newlines round-trips unchanged."""
        with TemporaryDirectory() as d:
            store = _make_store(d)
            try:
                store.create("s1", title="Test")
                tricky = '<img src=x onerror=alert(1)> "quoted" `backtick`\nline2'
                store.append_message("s1", "user", tricky)
                page = store.get_raw_message_page("s1", 0, 20)
                msg = page["messages"][0]
                self.assertEqual(msg["raw"]["content"], tricky)
            finally:
                store.close()


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

class RawMessagesEndpointTests(TestCase):
    """Integration tests for GET /api/sessions/{id}/context/messages."""

    def _store(self, directory: str) -> SessionStore:
        return _make_store(directory)

    def test_404_missing_session(self):
        """GET raw messages on a non-existent session returns 404."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.sessions import create_sessions_router

        with TemporaryDirectory() as d:
            store = self._store(d)
            try:
                app = FastAPI()
                app.include_router(create_sessions_router(store))
                client = TestClient(app)
                resp = client.get("/api/sessions/ghost/context/messages")
                self.assertEqual(resp.status_code, 404)
            finally:
                store.close()

    def test_no_writes_on_get(self):
        """GET raw messages does not create or modify any rows."""
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
                resp = client.get("/api/sessions/s1/context/messages")
                self.assertEqual(resp.status_code, 200)
                # No new messages were created (messages() would seed system).
                msgs = store.get_messages("s1")
                self.assertEqual(len(msgs), 0)
            finally:
                store.close()

    def test_endpoint_returns_page_shape(self):
        """GET raw messages returns session_id, offset, limit, total, has_more, messages."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.sessions import create_sessions_router

        with TemporaryDirectory() as d:
            store = self._store(d)
            try:
                store.create("s1", title="Test")
                store.append_message("s1", "user", "hello")
                store.append_message("s1", "assistant", "hi")

                app = FastAPI()
                app.include_router(create_sessions_router(store))
                client = TestClient(app)
                resp = client.get("/api/sessions/s1/context/messages")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["session_id"], "s1")
                self.assertEqual(data["offset"], 0)
                self.assertEqual(data["limit"], 20)
                self.assertEqual(data["total"], 2)
                self.assertFalse(data["has_more"])
                self.assertEqual(len(data["messages"]), 2)
            finally:
                store.close()

    def test_query_bounds_offset_negative(self):
        """offset=-1 is rejected by FastAPI validation (422)."""
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
                resp = client.get("/api/sessions/s1/context/messages", params={"offset": -1})
                self.assertEqual(resp.status_code, 422)
            finally:
                store.close()

    def test_query_bounds_limit_zero(self):
        """limit=0 is rejected by FastAPI validation (422)."""
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
                resp = client.get("/api/sessions/s1/context/messages", params={"limit": 0})
                self.assertEqual(resp.status_code, 422)
            finally:
                store.close()

    def test_query_bounds_limit_51(self):
        """limit=51 is rejected by FastAPI validation (422)."""
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
                resp = client.get("/api/sessions/s1/context/messages", params={"limit": 51})
                self.assertEqual(resp.status_code, 422)
            finally:
                store.close()

    def test_existing_messages_endpoint_shape_preserved(self):
        """GET /api/sessions/{id}/messages still returns its original shape."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.sessions import create_sessions_router

        with TemporaryDirectory() as d:
            store = self._store(d)
            try:
                store.create("s1", title="Test")
                store.append_message("s1", "user", "hello")
                app = FastAPI()
                app.include_router(create_sessions_router(store))
                client = TestClient(app)
                resp = client.get("/api/sessions/s1/messages")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertIn("messages", data)
                self.assertEqual(len(data["messages"]), 1)
                # Original shape: list of dicts with role/content.
                self.assertEqual(data["messages"][0]["role"], "user")
                self.assertEqual(data["messages"][0]["content"], "hello")
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
