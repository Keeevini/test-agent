"""Session title generation.

Generates a short, descriptive title for a session from its conversation,
mirroring OpenCode V2's session title feature:

- Non-blocking: callers schedule this on the event loop (``asyncio.create_task``)
  and never await it in the turn path, so title generation runs concurrently
  with the first model turn and never delays the model.
- Idempotent / only-once: the caller (``LocalAgent``) schedules a title at most
  once per session (while untitled) and writes it via ``sessions.update_title``.
- Cheap: a small, fixed prompt built from the conversation text, sent to the
  single ``llama-server`` model via ``ModelClient.stream``.
- Searchable: the title is stored on the session row (existing ``title`` column).
"""

from __future__ import annotations

from typing import Any

from .model import ModelClient

# Maximum length of a generated title (characters).
MAX_LENGTH = 100
# Maximum number of conversation characters to include in the title prompt.
MAX_CONTEXT_LENGTH = 8000
# Maximum length of a single user/assistant message in the first-turn prompt.
MAX_FIRST_MESSAGE_LENGTH = 2000

_SYSTEM_PROMPT = (
    "You generate concise session titles. Given the conversation so far, "
    "respond with a single line of at most "
    f"{MAX_LENGTH} characters that summarizes the conversation in "
    "imperative or noun-phrase form. Do not include quotes, punctuation at the "
    "end, or any explanation. Output only the title."
)


class SessionTitleGenerator:
    """Generates a single title string for a session from its conversation.

    The generator is stateless apart from the model client it wraps; the
    caller decides when (and whether) to schedule generation.
    """

    def __init__(self, model_client: ModelClient) -> None:
        self._model_client = model_client

    def truncate(self, text: str, max_length: int) -> str:
        """Return ``text`` truncated to ``max_length`` characters.

        Truncation is a hard cap; callers that want a soft cap (e.g. for
        readability) should handle that themselves.
        """
        if len(text) <= max_length:
            return text
        return text[:max_length]

    def _message_prompt(self, content: str) -> str:
        """Build a short prompt from a single message's content."""
        return (
            f"The user's first message was: \"{self.truncate(content, MAX_FIRST_MESSAGE_LENGTH)}\"\n"
            f"Generate a title for the session."
        )

    def _conversation_prompt(self, messages: list[dict[str, Any]]) -> str:
        """Build a prompt summarizing the conversation so far."""
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if not content:
                continue
            lines.append(f"{role}: {self.truncate(str(content), MAX_CONTEXT_LENGTH)}")
        return (
            "Conversation so far:\n"
            + "\n".join(lines)
            + f"\n\nGenerate a title for this session (max {MAX_LENGTH} characters)."
        )

    async def generate(self, messages: list[dict[str, Any]]) -> str | None:
        """Generate a title from the conversation.

        Returns the first non-empty line of the model's response, or ``None``
        if the model returns nothing usable. The first non-empty line is taken
        because the model is asked for a single line and may add trailing
        whitespace or newlines.
        """
        if not messages:
            return None

        # Build the prompt from the whole conversation (all turns), not just
        # the first message, so the title reflects what the session is about.
        prompt = self._conversation_prompt(messages)

        assistant, events = await self._model_client.stream([
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])

        # Consume the iterator fully so the stream is closed and the assistant
        # dict is finalized (its ``content`` is set in the iterator's
        # ``finally`` block).
        async for _ in events:
            pass

        content = (assistant.get("content") or "").strip()
        if not content:
            return None

        # Take the first non-empty line as the title.
        for line in content.splitlines():
            line = line.strip()
            if line:
                return self.truncate(line, MAX_LENGTH)
        return None
