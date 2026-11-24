"""Regression tests for idempotent session creation.

A duplicate ``create_session`` call must never destroy an existing session's
messages, title, usage, or timestamps (the previous ``INSERT OR REPLACE``
deleted and reinserted the row, cascading into ``messages``).
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.agent.session.store import SessionStore


class CreateSessionTests(TestCase):
    def test_duplicate_create_preserves_existing_session_and_messages(self):
        with TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            try:
                before = store.create_session("same-id", title="Original")
                store.append_message("same-id", "user", "keep this")
                store.record_model_call("same-id", "title", "title", None, {"prompt_tokens": 9, "completion_tokens": 0, "prompt_source": "provider", "completion_source": "provider"})

                returned = store.create_session("same-id", title="Replacement")
                self.assertEqual(returned.title, "Original")
                self.assertEqual(returned.created_at, before.created_at)
                self.assertEqual(returned.tokens_in, 9)
                self.assertEqual(
                    store.get_messages("same-id"),
                    [{"role": "user", "content": "keep this"}],
                )
            finally:
                store.close()
