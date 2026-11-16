"""SQLite-backed persistent session store.

Wraps the built-in ``sqlite3`` module with a thread-safe connection and
exposes CRUD operations for sessions and their messages.

The data access logic is split across repository mixins so the two
concerns can be maintained independently:

- :class:`.session_repo.SessionsRepository` — session-property CRUD
  (title, model, tokens, cost, timestamps) on the ``sessions`` table.
- :class:`.message_repo.MessagesRepository` — message CRUD on the
  ``messages`` table.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .message_repo import MessagesRepository
from .session_repo import SessionsRepository
from .schema import apply_schema


class SessionStore(SessionsRepository, MessagesRepository):
    """Persistent session store backed by a single SQLite database file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_db()

    def _init_db(self) -> None:
        apply_schema(self._conn)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying database connection."""
        with self._lock:
            self._conn.close()
