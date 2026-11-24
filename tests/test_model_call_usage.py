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
