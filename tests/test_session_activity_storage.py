"""Session activity storage tests.

Covers the additive v1 -> v2 migration (the ``activity_json`` column), the
history/model projections, legacy tool ``tool_call_id`` inference, and the
wrapper projections. Uses a temporary database and a synthetic v1 schema so
no real data is touched.
"""

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.agent.session.store import SessionStore

# ---------------------------------------------------------------------------
# Original schema-v1 DDL (before the activity_json column was added).
# Used to build a realistic legacy DB so the migration can be exercised.
# ---------------------------------------------------------------------------

_V1_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'Untitled Session',
    model TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    tokens_total INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0.0,
    tools_used TEXT NOT NULL DEFAULT '[]'
)
"""

_V1_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (session_id, seq),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
)
"""


def _create_v1_db(db_path: Path) -> None:
    """Create a schema-v1 database with the original DDL (no activity_json)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(_V1_SESSIONS_DDL)
        conn.execute(_V1_MESSAGES_DDL)
        conn.commit()
    finally:
        conn.close()


def _v1_columns(db_path: Path) -> set:
    """Return the column names of the messages table in a v1/v2 DB."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("PRAGMA table_info(messages)").fetchall()
    finally:
        conn.close()
    return {row[1] for row in rows}


class MigrationTests(TestCase):
    """Additive v1 -> v2 migration of the activity_json column."""

    def test_migration_adds_column_to_v1_db(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "sessions.db"
            _create_v1_db(db_path)
            self.assertFalse("activity_json" in _v1_columns(db_path))

            # Opening the store applies the schema, adding the column.
            store = SessionStore(db_path)
            try:
                self.assertTrue("activity_json" in _v1_columns(db_path))
            finally:
                store.close()

    def test_migration_preserves_existing_rows(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "sessions.db"
            _create_v1_db(db_path)
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "INSERT INTO sessions (session_id, title) VALUES (?, ?)",
                    ("legacy", "Legacy Session"),
                )
                conn.execute(
                    "INSERT INTO messages "
                    "(session_id, seq, role, content) VALUES (?, ?, ?, ?)",
                    ("legacy", 0, "user", "hello"),
                )
                # A JSON-looking raw tool result must stay raw.
                conn.execute(
                    "INSERT INTO messages "
                    "(session_id, seq, role, content) VALUES (?, ?, ?, ?)",
                    ("legacy", 1, "tool", '{"a": 1}'),
                )
                conn.commit()
            finally:
                conn.close()

            store = SessionStore(db_path)
            try:
                msgs = store.get_messages("legacy")
                self.assertEqual(
                    msgs,
                    [
                        {"role": "user", "content": "hello"},
                        {"role": "tool", "content": '{"a": 1}'},
                    ],
                )
                # Existing rows have NULL activity after migration.
                conn = sqlite3.connect(str(db_path))
                try:
                    row = conn.execute(
                        "SELECT activity_json FROM messages "
                        "WHERE session_id = ? AND seq = 0",
                        ("legacy",),
                    ).fetchone()
                    self.assertIsNone(row[0])
                finally:
                    conn.close()
            finally:
                store.close()

    def test_migration_is_idempotent_on_reopen(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "sessions.db"
            _create_v1_db(db_path)
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "INSERT INTO sessions (session_id, title) VALUES (?, ?)",
                    ("legacy", "Legacy Session"),
                )
                conn.execute(
                    "INSERT INTO messages "
                    "(session_id, seq, role, content) VALUES (?, ?, ?, ?)",
                    ("legacy", 0, "user", "keep"),
                )
                conn.commit()
            finally:
                conn.close()

            # First open migrates; close; second open must be a no-op.
            store = SessionStore(db_path)
            store.close()
            store = SessionStore(db_path)
            try:
                self.assertTrue("activity_json" in _v1_columns(db_path))
                msgs = store.get_messages("legacy")
                self.assertEqual(msgs, [{"role": "user", "content": "keep"}])
            finally:
                store.close()

    def test_migration_cascade_deletes_metadata_with_rows(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "sessions.db"
            _create_v1_db(db_path)
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "INSERT INTO sessions (session_id, title) VALUES (?, ?)",
                    ("legacy", "Legacy Session"),
                )
                conn.execute(
                    "INSERT INTO messages "
                    "(session_id, seq, role, content) VALUES (?, ?, ?, ?)",
                    ("legacy", 0, "user", "bye"),
                )
                conn.commit()
            finally:
                conn.close()

            store = SessionStore(db_path)
            try:
                store.delete_session("legacy")
                self.assertIsNone(store.get_session("legacy"))
                self.assertEqual(store.get_messages("legacy"), [])
            finally:
                store.close()


class ProjectionTests(TestCase):
    """History/model projections and update isolation."""

    def _store(self, directory: str) -> SessionStore:
        return SessionStore(Path(directory) / "sessions.db")

    def test_projection_raw_content_preserved(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                # A JSON-looking raw tool result must stay a raw string.
                store.append_message("s", "tool", '{"raw": true}')
                msgs = store.get_messages("s", include_activity=True)
                self.assertEqual(msgs[0]["content"], '{"raw": true}')
            finally:
                store.close()

    def test_projection_invalid_metadata_ignored(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                # Malformed activity (not valid JSON) must not crash.
                store.append_message("s", "tool", "result", activity=None)
                # Write a malformed activity directly to the DB.
                store._conn.execute(
                    "UPDATE messages SET activity_json = ? "
                    "WHERE session_id = ? AND seq = ?",
                    ("{not valid json", "s", 0),
                )
                store._conn.commit()
                msgs = store.get_messages("s", include_activity=True)
                self.assertEqual(msgs[0]["content"], "result")
                self.assertNotIn("activity", msgs[0])
            finally:
                store.close()

    def test_projection_activity_only_when_requested(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                store.append_message(
                    "s",
                    "assistant",
                    {"role": "assistant", "content": "ok"},
                    activity={"version": 1},
                )
                model = store.get_messages("s")
                history = store.get_messages("s", include_activity=True)
                self.assertNotIn("activity", model[0])
                self.assertEqual(history[0]["activity"], {"version": 1})
            finally:
                store.close()

    def test_projection_tool_call_id_from_activity(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                store.append_message(
                    "s",
                    "tool",
                    "hello\n",
                    activity={"version": 1, "tool_call_id": "c1", "ok": True},
                )
                model = store.get_messages("s")
                history = store.get_messages("s", include_activity=True)
                # tool_call_id is projected in BOTH projections.
                self.assertEqual(model[0]["tool_call_id"], "c1")
                self.assertEqual(history[0]["tool_call_id"], "c1")
                # activity is history-only.
                self.assertNotIn("activity", model[0])
                self.assertEqual(
                    history[0]["activity"],
                    {"version": 1, "tool_call_id": "c1", "ok": True},
                )
            finally:
                store.close()

    def test_projection_update_content_leaves_activity(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                seq = store.append_message(
                    "s",
                    "tool",
                    "first",
                    activity={"version": 1, "tool_call_id": "c1"},
                )
                store.update_message_content("s", seq, "second")
                history = store.get_messages("s", include_activity=True)
                self.assertEqual(history[0]["content"], "second")
                self.assertEqual(
                    history[0]["activity"], {"version": 1, "tool_call_id": "c1"}
                )
            finally:
                store.close()

    def test_projection_update_activity_leaves_content(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                seq = store.append_message(
                    "s", "tool", "body", activity={"version": 1}
                )
                store.update_message_activity(
                    "s", seq, {"version": 1, "tool_call_id": "c9", "ok": True}
                )
                history = store.get_messages("s", include_activity=True)
                self.assertEqual(history[0]["content"], "body")
                self.assertEqual(
                    history[0]["activity"],
                    {"version": 1, "tool_call_id": "c9", "ok": True},
                )
            finally:
                store.close()


class LegacyTests(TestCase):
    """Read-only legacy tool_call_id inference."""

    def _store(self, directory: str) -> SessionStore:
        return SessionStore(Path(directory) / "sessions.db")

    def test_legacy_equal_size_batch_infers_ids(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                store.append_message(
                    "s",
                    "assistant",
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "a", "type": "function", "function": {"name": "t"}},
                            {"id": "b", "type": "function", "function": {"name": "t"}},
                        ],
                    },
                )
                store.append_message("s", "tool", "r1")
                store.append_message("s", "tool", "r2")
                msgs = store.get_messages("s", include_activity=True)
                self.assertEqual(msgs[1]["tool_call_id"], "a")
                self.assertEqual(msgs[2]["tool_call_id"], "b")
            finally:
                store.close()

    def test_legacy_ran_only_partial_infers_ids(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                store.append_message(
                    "s",
                    "assistant",
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "a",
                                "time": {"ran": "t0", "completed": "t1"},
                            },
                            {
                                "id": "b",
                                "time": {"ran": "t0", "completed": "t1"},
                            },
                            {
                                "id": "c",
                                "time": {"completed": "t1"},
                            },
                        ],
                    },
                )
                # Only two results were persisted (the two ran calls).
                store.append_message("s", "tool", "r1")
                store.append_message("s", "tool", "r2")
                msgs = store.get_messages("s", include_activity=True)
                self.assertEqual(msgs[1]["tool_call_id"], "a")
                self.assertEqual(msgs[2]["tool_call_id"], "b")
            finally:
                store.close()

    def test_legacy_ambiguous_partial_no_inference(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                # Partial batch with no completed stamps -> ambiguous.
                store.append_message(
                    "s",
                    "assistant",
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "a", "time": {"ran": "t0"}},
                            {"id": "b", "time": {"ran": "t0"}},
                            {"id": "c", "time": {}},
                        ],
                    },
                )
                store.append_message("s", "tool", "r1")
                store.append_message("s", "tool", "r2")
                msgs = store.get_messages("s", include_activity=True)
                self.assertNotIn("tool_call_id", msgs[1])
                self.assertNotIn("tool_call_id", msgs[2])
            finally:
                store.close()

    def test_legacy_mixed_batch_no_inference(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                store.append_message(
                    "s",
                    "assistant",
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "a"},
                            {"id": "b"},
                        ],
                    },
                )
                # One row has an explicit ID, one does not -> mixed.
                store.append_message(
                    "s",
                    "tool",
                    "r1",
                    activity={"version": 1, "tool_call_id": "a"},
                )
                store.append_message("s", "tool", "r2")
                msgs = store.get_messages("s", include_activity=True)
                # The explicit ID is preserved; the other row is left intact.
                self.assertEqual(msgs[1]["tool_call_id"], "a")
                self.assertNotIn("tool_call_id", msgs[2])
            finally:
                store.close()

    def test_legacy_boundary_does_not_cross(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                # Assistant with 2 calls, but a user row interrupts the batch.
                store.append_message(
                    "s",
                    "assistant",
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "a"},
                            {"id": "b"},
                        ],
                    },
                )
                store.append_message("s", "tool", "r1")
                store.append_message("s", "user", "interrupt")
                store.append_message("s", "tool", "r2")
                msgs = store.get_messages("s", include_activity=True)
                # Only the contiguous run immediately after the assistant
                # (just r1) is a batch; r2 is not paired.
                self.assertNotIn("tool_call_id", msgs[1])
                self.assertNotIn("tool_call_id", msgs[3])
            finally:
                store.close()


class WrapperTests(TestCase):
    """Wrapper model/history projections."""

    def _wrapper(self, directory: str):
        from src.agent.session_store import SessionStore as Wrapper

        return Wrapper(Path(directory) / "sessions.db")

    def test_wrapper_model_projection_no_activity(self):
        with TemporaryDirectory() as directory:
            wrapper = self._wrapper(directory)
            try:
                wrapper._store.create_session("s")
                wrapper.append_message(
                    "s",
                    "tool",
                    "result",
                    activity={"version": 1, "tool_call_id": "c1"},
                )
                model = wrapper.messages("s")
                self.assertNotIn("activity", model[-1])
                self.assertEqual(model[-1]["tool_call_id"], "c1")
            finally:
                wrapper.close()

    def test_wrapper_history_projection_has_activity(self):
        with TemporaryDirectory() as directory:
            wrapper = self._wrapper(directory)
            try:
                wrapper._store.create_session("s")
                wrapper.append_message(
                    "s",
                    "tool",
                    "result",
                    activity={"version": 1, "tool_call_id": "c1", "ok": True},
                )
                history = wrapper.get_messages("s")
                self.assertEqual(
                    history[-1]["activity"],
                    {"version": 1, "tool_call_id": "c1", "ok": True},
                )
                self.assertEqual(history[-1]["tool_call_id"], "c1")
            finally:
                wrapper.close()

    def test_wrapper_update_message_activity_proxy(self):
        with TemporaryDirectory() as directory:
            wrapper = self._wrapper(directory)
            try:
                wrapper._store.create_session("s")
                seq = wrapper.append_message("s", "tool", "body")
                wrapper.update_message_activity(
                    "s", seq, {"version": 1, "tool_call_id": "c9"}
                )
                history = wrapper.get_messages("s")
                self.assertEqual(
                    history[-1]["activity"], {"version": 1, "tool_call_id": "c9"}
                )
            finally:
                wrapper.close()
