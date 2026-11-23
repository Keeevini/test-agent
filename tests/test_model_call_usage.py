"""Tests for Stage 02 durable per-model-call usage ledger and tool-result identity.

Verifies the ``model_calls`` ledger and the tool-result round-trip:

* ``record_model_call`` is idempotent per ``(session_id, call_key)`` — a
  duplicate key inserts one row and increments the lifetime ``sessions``
  counters exactly once;
* the ledger survives reopening the database (the schema is applied
  idempotently and rows persist);
* deleting a session cascades to its ledger rows;
* two chat calls accumulate to the correct lifetime total with no double
  count;
* a ``title`` call increments lifetime usage without creating a chat row;
* tool results persisted in the ``save_tool_result`` shape (success /
  ToolError / denied / invalid-JSON) round-trip with their ``tool_call_id``
  and ``context_meta`` intact;
* legacy plain-text tool rows and malformed-JSON tool content fall back to
  raw text and are never treated as metadata;
* ``record_model_call`` validates usage before touching the store.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

from src.agent.session.store import SessionStore


def _usage(prompt: int, completion: int) -> dict[str, Any]:
    """Build a finalized provider-usage dict (the shape ``stream`` finalizes)."""
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "prompt_source": "provider",
        "completion_source": "provider",
    }


def _tool_result(call_id: str, name: str, content: str, ok: bool, path=None) -> dict[str, Any]:
    """Mirror the ``save_tool_result`` closure shape in ``core.stream_turn``."""
    msg: dict[str, Any] = {
        "role": "tool",
        "tool_call_id": call_id,
        "content": content,
        "context_meta": {"name": name, "ok": ok},
    }
    if name == "read_file" and ok and isinstance(path, str):
        msg["context_meta"]["path"] = path
    return msg


class ModelCallLedgerTests(TestCase):
    """Durable ``model_calls`` ledger behavior on the persistent store."""

    def _store(self, directory: str) -> SessionStore:
        return SessionStore(Path(directory) / "sessions.db")

    def test_duplicate_key_is_idempotent(self):
        """Recording the same ``call_key`` twice yields one row, one increment."""
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s1")
                first = store.record_model_call("s1", "chat:2", "chat", 2, _usage(120, 30))
                second = store.record_model_call("s1", "chat:2", "chat", 2, _usage(120, 30))

                self.assertTrue(first)
                self.assertFalse(second)
                self.assertEqual(len(store.list_model_calls("s1")), 1)
                # Counters incremented exactly once.
                session = store.get_session("s1")
                self.assertEqual(session.tokens_in, 120)
                self.assertEqual(session.tokens_out, 30)
                self.assertEqual(session.tokens_total, 150)
            finally:
                store.close()

    def test_ledger_survives_reopen(self):
        """Reopening the database reapplies the schema and keeps the rows."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.db"
            store = SessionStore(path)
            try:
                store.create_session("s1")
                store.record_model_call("s1", "chat:2", "chat", 2, _usage(120, 30))
            finally:
                store.close()

            # Reopen on the same file: schema must apply idempotently.
            store = SessionStore(path)
            try:
                rows = store.list_model_calls("s1")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["prompt_tokens"], 120)
                self.assertEqual(rows[0]["completion_tokens"], 30)
                self.assertEqual(rows[0]["kind"], "chat")
                session = store.get_session("s1")
                self.assertEqual(session.tokens_total, 150)
            finally:
                store.close()

    def test_delete_session_cascades_to_ledger(self):
        """Deleting a session removes its model_calls rows."""
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s1")
                store.record_model_call("s1", "chat:2", "chat", 2, _usage(120, 30))
                self.assertEqual(len(store.list_model_calls("s1")), 1)

                store.delete_session("s1")
                self.assertEqual(store.list_model_calls("s1"), [])
            finally:
                store.close()

    def test_two_chat_calls_accumulate_without_double_count(self):
        """Two distinct chat calls sum to the lifetime total; no double count."""
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s1")
                store.record_model_call("s1", "chat:2", "chat", 2, _usage(120, 30))
                store.record_model_call("s1", "chat:3", "chat", 3, _usage(200, 40))

                rows = store.list_model_calls("s1")
                self.assertEqual(len(rows), 2)
                # Same-second timestamps fall back to assistant_seq ordering.
                self.assertEqual([r["assistant_seq"] for r in rows], [2, 3])

                session = store.get_session("s1")
                self.assertEqual(session.tokens_in, 320)
                self.assertEqual(session.tokens_out, 70)
                self.assertEqual(session.tokens_total, 390)
            finally:
                store.close()

    def test_title_call_increments_lifetime_without_chat_row(self):
        """A title call adds lifetime usage but never creates a chat row."""
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s1")
                store.record_model_call("s1", "title", "title", None, _usage(50, 10))

                rows = store.list_model_calls("s1")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["kind"], "title")
                self.assertIsNone(rows[0]["assistant_seq"])

                session = store.get_session("s1")
                self.assertEqual(session.tokens_in, 50)
                self.assertEqual(session.tokens_out, 10)
                self.assertEqual(session.tokens_total, 60)
            finally:
                store.close()

    def test_missing_session_is_noop_not_recreate(self):
        """Recording against a missing session is a graceful no-op (FK)."""
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                inserted = store.record_model_call("ghost", "chat:2", "chat", 2, _usage(120, 30))
                self.assertFalse(inserted)
                # The session is never recreated.
                self.assertIsNone(store.get_session("ghost"))
                self.assertEqual(store.list_model_calls("ghost"), [])
            finally:
                store.close()

    def test_record_model_call_validates_usage(self):
        """Usage counts and sources are validated before touching the store."""
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s1")

                # Non-int token count.
                with self.assertRaises(ValueError):
                    store.record_model_call("s1", "chat:2", "chat", 2, {
                        "prompt_tokens": "12", "completion_tokens": 30,
                        "prompt_source": "provider", "completion_source": "provider",
                    })
                # Negative token count.
                with self.assertRaises(ValueError):
                    store.record_model_call("s1", "chat:2", "chat", 2, {
                        "prompt_tokens": -1, "completion_tokens": 30,
                        "prompt_source": "provider", "completion_source": "provider",
                    })
                # Bool is an int subclass and must be rejected.
                with self.assertRaises(ValueError):
                    store.record_model_call("s1", "chat:2", "chat", 2, {
                        "prompt_tokens": True, "completion_tokens": 30,
                        "prompt_source": "provider", "completion_source": "provider",
                    })
                # Non-string source.
                with self.assertRaises(ValueError):
                    store.record_model_call("s1", "chat:2", "chat", 2, {
                        "prompt_tokens": 12, "completion_tokens": 30,
                        "prompt_source": None, "completion_source": "provider",
                    })

                # No row or counter change happened.
                self.assertEqual(store.list_model_calls("s1"), [])
                session = store.get_session("s1")
                self.assertEqual(session.tokens_total, 0)
            finally:
                store.close()


class ToolResultRoundTripTests(TestCase):
    """Tool-result rows round-trip their ``tool_call_id`` and ``context_meta``."""

    def test_read_file_success_round_trips_with_path(self):
        """A successful ``read_file`` result keeps its sandbox-relative path."""
        with TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            try:
                store.create_session("s1")
                msg = _tool_result("call_1", "read_file", "line1\nline2", True, path="notes.md")
                store.append_message("s1", "tool", msg)

                rows = store.get_messages("s1")
                tool_rows = [r for r in rows if r.get("role") == "tool"]
                self.assertEqual(len(tool_rows), 1)
                self.assertEqual(tool_rows[0]["tool_call_id"], "call_1")
                self.assertEqual(tool_rows[0]["content"], "line1\nline2")
                self.assertEqual(
                    tool_rows[0]["context_meta"],
                    {"name": "read_file", "ok": True, "path": "notes.md"},
                )
            finally:
                store.close()

    def test_tool_error_round_trips_without_path(self):
        """A ToolError result keeps ``ok=False`` and carries no path."""
        with TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            try:
                store.create_session("s1")
                msg = _tool_result("call_2", "write_file", "Tool error: blocked", False)
                store.append_message("s1", "tool", msg)

                rows = store.get_messages("s1")
                tool_rows = [r for r in rows if r.get("role") == "tool"]
                self.assertEqual(len(tool_rows), 1)
                self.assertEqual(tool_rows[0]["tool_call_id"], "call_2")
                self.assertFalse(tool_rows[0]["context_meta"]["ok"])
                self.assertNotIn("path", tool_rows[0]["context_meta"])
            finally:
                store.close()

    def test_denied_result_round_trips(self):
        """A denied-approval result keeps ``ok=False`` and the tool name."""
        with TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            try:
                store.create_session("s1")
                msg = _tool_result(
                    "call_3", "write_file", "User denied this mutating operation.", False
                )
                store.append_message("s1", "tool", msg)

                rows = store.get_messages("s1")
                tool_rows = [r for r in rows if r.get("role") == "tool"]
                self.assertEqual(len(tool_rows), 1)
                self.assertEqual(tool_rows[0]["tool_call_id"], "call_3")
                self.assertEqual(tool_rows[0]["context_meta"]["name"], "write_file")
                self.assertFalse(tool_rows[0]["context_meta"]["ok"])
            finally:
                store.close()

    def test_legacy_plain_text_tool_row_round_trips(self):
        """A legacy plain-text tool row replays as raw text, not metadata."""
        with TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            try:
                store.create_session("s1")
                store.append_message("s1", "tool", "raw legacy result text")

                rows = store.get_messages("s1")
                tool_rows = [r for r in rows if r.get("role") == "tool"]
                self.assertEqual(len(tool_rows), 1)
                self.assertEqual(
                    tool_rows[0],
                    {"role": "tool", "content": "raw legacy result text"},
                )
            finally:
                store.close()

    def test_malformed_json_tool_content_is_not_metadata(self):
        """Tool content that is not a genuine tool object falls back to raw text."""
        with TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            try:
                store.create_session("s1")
                # Stored content is always a string; these are valid JSON
                # that parses to a non-tool-object, plus one invalid-JSON case.
                store.append_message("s1", "tool", '{"foo": "bar"}')
                store.append_message("s1", "tool", '[1, 2, 3]')
                store.append_message("s1", "tool", '{"incomplete": ')

                rows = store.get_messages("s1")
                tool_rows = [r for r in rows if r.get("role") == "tool"]
                self.assertEqual(len(tool_rows), 3)
                # Each falls back to the legacy raw-text shape.
                for row in tool_rows:
                    self.assertEqual(set(row.keys()), {"role", "content"})
                    self.assertEqual(row["role"], "tool")
            finally:
                store.close()
