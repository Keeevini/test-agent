"""Agent package.

Re-exports the public API so callers can do ``from src.agent import ...``.
The individual concerns live in their own modules:

- :mod:`.core` — the ``LocalAgent`` orchestrator (agent loop).
- :mod:`.approvals` — the approval gate for mutating tools.
- :mod:`.model` — the streaming llama-server client.
- :mod:`.session_store` — the async session store wrapper.
- :mod:`.prompt` — the system prompt.
"""

from .approvals import ApprovalManager
from .core import LocalAgent
from .session_store import SessionStore

__all__ = ["ApprovalManager", "LocalAgent", "SessionStore"]
