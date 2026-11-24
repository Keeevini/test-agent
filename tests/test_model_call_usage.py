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
* literal tool results round-trip with explicit IDs and outcome activity;
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
                first = store.record_model_call("s1", "title", "title", None, _usage(120, 30))
                second = store.record_model_call("s1", "title", "title", None, _usage(120, 30))

                self.assertTrue(first)
                self.assertFalse(second)
                self.assertEqual(len(store.list_model_calls("s1")), 1)
                # Ledger aggregate includes the call exactly once.
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
                store.append_assistant_with_usage("s1", {"role": "assistant", "content": "first"}, _usage(120, 30))
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
                store.append_assistant_with_usage("s1", {"role": "assistant", "content": "first"}, _usage(120, 30))
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
                store.append_assistant_with_usage("s1", {"role": "assistant", "content": "first"}, _usage(120, 30))
                store.append_assistant_with_usage("s1", {"role": "assistant", "content": "second"}, _usage(200, 40))

                rows = store.list_model_calls("s1")
                self.assertEqual(len(rows), 2)
                # Same-second timestamps fall back to assistant_seq ordering.
                self.assertEqual([r["assistant_seq"] for r in rows], [0, 1])

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
                inserted = store.record_model_call("ghost", "title", "title", None, _usage(120, 30))
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
                    store.record_model_call("s1", "title", "title", None, {
                        "prompt_tokens": "12", "completion_tokens": 30,
                        "prompt_source": "provider", "completion_source": "provider",
                    })
                # Negative token count.
                with self.assertRaises(ValueError):
                    store.record_model_call("s1", "title", "title", None, {
                        "prompt_tokens": -1, "completion_tokens": 30,
                        "prompt_source": "provider", "completion_source": "provider",
                    })
                # Bool is an int subclass and must be rejected.
                with self.assertRaises(ValueError):
                    store.record_model_call("s1", "title", "title", None, {
                        "prompt_tokens": True, "completion_tokens": 30,
                        "prompt_source": "provider", "completion_source": "provider",
                    })
                # Non-string source.
                with self.assertRaises(ValueError):
                    store.record_model_call("s1", "title", "title", None, {
                        "prompt_tokens": 12, "completion_tokens": 30,
                        "prompt_source": None, "completion_source": "provider",
                    })

                # No row or counter change happened.
                self.assertEqual(store.list_model_calls("s1"), [])
                session = store.get_session("s1")
                self.assertEqual(session.tokens_total, 0)
            finally:
                store.close()


    def test_chat_append_and_ledger_are_atomic(self):
        from unittest.mock import patch
        import sqlite3
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                assistant = {"role": "assistant", "content": "hello", "reasoning_content": "reasoning"}
                usage = {**_usage(120, 30), "cached_prompt_tokens": 42, "reasoning_tokens": 0}
                with patch.object(store, "_insert_model_call_locked", side_effect=sqlite3.IntegrityError("synthetic")):
                    with self.assertRaises(sqlite3.IntegrityError):
                        store.append_assistant_with_usage("s", assistant, usage, activity={"version": 1})
                self.assertEqual(store.get_messages("s"), [])
                self.assertEqual(store.list_model_calls("s"), [])
                self.assertEqual(store.get_session("s").tokens_total, 0)
                seq = store.append_assistant_with_usage("s", assistant, usage, activity={"version": 1})
                self.assertEqual(seq, 0)
                self.assertNotIn("usage", store.get_messages("s")[0])
                raw = store.get_raw_message_page("s", 0, 1)["messages"][0]["raw"]
                self.assertEqual(raw["usage"], usage)
                self.assertEqual(raw["reasoning_content"], "reasoning")
                self.assertEqual(store.get_session("s").tokens_total, 150)
                with self.assertRaises(ValueError):
                    store.record_model_call("s", "chat:0", "chat", 0, usage)
            finally:
                store.close()

    def test_list_search_and_get_derive_same_totals(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                for name, counts in [("a", (8, 2)), ("b", (13, 3)), ("c", (0, 0))]:
                    store.create_session(name, title=f"Find {name}")
                    if name != "c":
                        store.append_assistant_with_usage(name, {"role": "assistant", "content": "ok"}, _usage(*counts))
                expected = {"a": 10, "b": 16, "c": 0}
                self.assertEqual({m.session_id: m.tokens_total for m in store.list_sessions()}, expected)
                self.assertEqual({m.session_id: m.tokens_total for m in store.search_sessions("Find")}, expected)
                self.assertEqual({k: store.get_session(k).tokens_total for k in expected}, expected)
                page = store.list_sessions(limit=1, offset=1)
                self.assertEqual(len(page), 1)
                self.assertEqual(page[0].tokens_total, expected[page[0].session_id])
            finally:
                store.close()


class ToolResultRoundTripTests(TestCase):
    def test_literal_and_outcome_round_trip(self):
        with TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            try:
                store.create_session("s")
                literal = '{"role":"tool","content":"not an envelope","tool_call_id":"fake"}'
                for i, (text, ok) in enumerate([(literal, True), ("Tool error", False), ("Denied", False), ("{malformed", True)]):
                    store.append_message("s", "tool", text, tool_call_id=f"c{i}", activity={"version": 1, "ok": ok})
                history = store.get_messages("s", include_activity=True)
                self.assertEqual([r["content"] for r in history], [literal, "Tool error", "Denied", "{malformed"])
                self.assertEqual([r["tool_call_id"] for r in history], ["c0", "c1", "c2", "c3"])
                self.assertEqual([r["activity"]["ok"] for r in history], [True, False, False, True])
                self.assertTrue(all("context_meta" not in r for r in history))
            finally:
                store.close()
