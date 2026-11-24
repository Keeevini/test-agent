"""Session activity storage tests.

Covers fresh schema rejection/reopen, explicit tool IDs, batch association,
and history/model projections. Uses only temporary databases.
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


class FreshSchemaTests(TestCase):
    def test_reject_old_db_without_migration(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.db"
            _create_v1_db(path)
            with sqlite3.connect(path) as conn:
                conn.execute("INSERT INTO sessions (session_id) VALUES ('old')")
            with self.assertRaisesRegex(RuntimeError, "Incompatible sessions database"):
                SessionStore(path)
            self.assertNotIn("activity_json", _v1_columns(path))
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute("SELECT session_id FROM sessions").fetchone()[0], "old")

    def test_fresh_schema_and_reopen(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.db"
            store = SessionStore(path)
            store.create_session("s")
            store.append_message("s", "tool", "keep", tool_call_id="c", activity={"version": 1, "ok": True})
            store.close()
            store = SessionStore(path)
            try:
                self.assertEqual(store.get_messages("s", include_activity=True), [{"role": "tool", "content": "keep", "tool_call_id": "c", "activity": {"version": 1, "ok": True}}])
                self.assertIn("tool_call_id", _v1_columns(path))
                self.assertIn("activity_json", _v1_columns(path))
                store.delete_session("s")
                self.assertEqual(store.get_messages("s"), [])
            finally:
                store.close()

    def test_explicit_id_required(self):
        with TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            try:
                store.create_session("s")
                for role, text, cid in [("tool", "x", None), ("tool", "x", ""), ("tool", {"x": 1}, "c"), ("user", "x", "c")]:
                    with self.assertRaises(ValueError):
                        store.append_message("s", role, text, tool_call_id=cid)
                self.assertEqual(store.get_messages("s"), [])
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
                store.append_message("s", "tool", '{"raw": true}', tool_call_id="c")
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
                store.append_message("s", "tool", "result", tool_call_id="c", activity=None)
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

    def test_projection_tool_call_id_from_column(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                store.append_message(
                    "s",
                    "tool",
                    "hello\n",
                    tool_call_id="c1", activity={"version": 1, "ok": True},
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
                    {"version": 1, "ok": True},
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
                    tool_call_id="c1", activity={"version": 1},
                )
                store.update_message_content("s", seq, "second")
                history = store.get_messages("s", include_activity=True)
                self.assertEqual(history[0]["content"], "second")
                self.assertEqual(
                    history[0]["activity"], {"version": 1}
                )
            finally:
                store.close()

    def test_projection_update_activity_leaves_content(self):
        with TemporaryDirectory() as directory:
            store = self._store(directory)
            try:
                store.create_session("s")
                seq = store.append_message(
                    "s", "tool", "body", tool_call_id="c9", activity={"version": 1}
                )
                store.update_message_activity(
                    "s", seq, {"version": 1, "ok": True}
                )
                history = store.get_messages("s", include_activity=True)
                self.assertEqual(history[0]["content"], "body")
                self.assertEqual(
                    history[0]["activity"],
                    {"version": 1, "ok": True},
                )
            finally:
                store.close()


class ToolAssociationTests(TestCase):
    def test_batch_identity_and_boundaries(self):
        from src.agent.session.tool_links import associate_tool_results
        def assistant(*names):
            return {"role": "assistant", "tool_calls": [{"id": cid, "function": {"name": name}} for cid, name in names]}
        def tool(cid):
            return {"role": "tool", "tool_call_id": cid}
        rows = [assistant(("x", "read_file"), ("y", "search_text")), tool("y"), tool("x"),
                assistant(("x", "list_directory")), tool("x"), {"role": "user"}, tool("x"),
                assistant(("x", "read_file"), ("x", "search_text")), tool("x")]
        links = associate_tool_results(rows)
        self.assertEqual({i: c["function"]["name"] for i, c in links.items()},
                         {1: "search_text", 2: "read_file", 4: "list_directory"})


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
                    tool_call_id="c1", activity={"version": 1},
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
                    tool_call_id="c1", activity={"version": 1, "ok": True},
                )
                history = wrapper.get_messages("s")
                self.assertEqual(
                    history[-1]["activity"],
                    {"version": 1, "ok": True},
                )
                self.assertEqual(history[-1]["tool_call_id"], "c1")
            finally:
                wrapper.close()

    def test_wrapper_update_message_activity_proxy(self):
        with TemporaryDirectory() as directory:
            wrapper = self._wrapper(directory)
            try:
                wrapper._store.create_session("s")
                seq = wrapper.append_message("s", "tool", "body", tool_call_id="c9")
                wrapper.update_message_activity(
                    "s", seq, {"version": 1}
                )
                history = wrapper.get_messages("s")
                self.assertEqual(
                    history[-1]["activity"], {"version": 1}
                )
            finally:
                wrapper.close()
