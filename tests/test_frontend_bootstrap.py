"""Source-level regression tests for the frontend bootstrap.

These guard the concrete contracts that the post-refactor review (R1-R8) found
broken. ``node --check`` passes on all of these files, so a *syntax* check
cannot catch them -- R1 in particular is a runtime ``const`` reassignment. The
assertions therefore load the real production JS and check the exact contracts
that, when violated, produce the reported regressions:

* R1  ``lifecycle`` must be reassignable (``let``), not a frozen ``const``.
* R5  the bootstrap starts the health check independently of session I/O.
* R3  ``createNew``/``remove`` acquire the transition lock before the first
      ``await``; ``send()`` rejects while a transition is in flight.
* R4  ``loadHistory`` clears the transcript before fetching the next session.
* R6  the title poll checks a staleness generation before redrawing.
* R7  ``loadHistory`` resets the status context on activation.

A true DOM assertion (badge leaving ``Checking…``, a rendered session row,
``submit`` default action canceled) requires a browser/JS runtime that is not
available in this environment, so this suite is the repeatable, in-repo guard
for the same regressions without a production dependency.
"""

import re
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parent.parent


def _read(*parts) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """Return the balanced-brace body of ``function NAME(...) { ... }``.

    A small brace matcher is sufficient here: none of the guarded functions
    contain braces inside string literals or comments.
    """
    match = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", source)
    assert match, f"{name}() not found"
    start = source.index("{", match.end())
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}()")


class FrontendBootstrapTests(TestCase):
    # ---------------------------------------------------------------- R1
    def test_lifecycle_is_reassignable_not_const(self):
        """R1: ``setLifecycleHandlers`` reassigns ``lifecycle``.

        Declaring it ``const`` raised ``TypeError: Assignment to constant
        variable`` and halted the entire bootstrap before any API request.
        """
        source = _read("src", "static", "js", "sessions", "controller.js")
        self.assertRegex(
            source,
            r"\blet\s+lifecycle\s*=\s*\{",
            "lifecycle must be declared with `let` so setLifecycleHandlers can reassign it",
        )
        self.assertNotRegex(
            source,
            r"\bconst\s+lifecycle\s*=",
            "lifecycle must not be a const (R1 regression)",
        )
        # The reassignment target must exist.
        self.assertIn("lifecycle = h", source)

    # ---------------------------------------------------------------- R5
    def test_bootstrap_starts_health_independently_of_session_io(self):
        """R5: a rejected session fetch must not block the health badge."""
        source = _read("src", "static", "app.js")
        self.assertIn("void status.checkHealth()", source)
        self.assertIn("void sessions.init()", source)
        self.assertIn("void conversation.loadHistory(", source)
        # The sequential `await sessions.init(); ... await status.checkHealth()`
        # form is what let a rejected fetch freeze the badge.
        self.assertNotIn("await sessions.init()", source)
        self.assertNotIn("await status.checkHealth()", source)

    def test_api_list_sessions_reports_http_errors(self):
        """R5: the list endpoint must throw, not silently return null."""
        source = _read("src", "static", "js", "api.js")
        self.assertIn("throw new Error(`Session list failed: HTTP", source)

    # ---------------------------------------------------------------- R3
    def test_create_new_acquires_transition_lock_before_first_await(self):
        source = _read("src", "static", "js", "sessions", "controller.js")
        body = _function_body(source, "createNew")
        lock_idx = body.index("transitionPending = true")
        await_idx = body.index("await api.createSession")
        self.assertLess(
            lock_idx,
            await_idx,
            "createNew must set transitionPending before its first await",
        )
        self.assertIn("finally", body)

    def test_remove_acquires_transition_lock_before_first_await(self):
        source = _read("src", "static", "js", "sessions", "controller.js")
        body = _function_body(source, "remove")
        lock_idx = body.index("transitionPending = true")
        await_idx = body.index("await api.deleteSession")
        self.assertLess(
            lock_idx,
            await_idx,
            "remove must set transitionPending before its first await",
        )
        self.assertIn("finally", body)

    def test_is_transitioning_is_exposed(self):
        source = _read("src", "static", "js", "sessions", "controller.js")
        self.assertIn("isTransitioning", source)
        self.assertIn("function isTransitioning", source)

    def test_conversation_can_send_and_send_guard(self):
        """R3: a turn may start only when nothing is busy and no transition
        is in flight; ``send()`` itself also rejects during a transition."""
        source = _read("src", "static", "js", "conversation", "controller.js")
        body = _function_body(source, "canSend")
        self.assertIn("sessions.isTransitioning()", body)
        send_body = _function_body(source, "send")
        self.assertIn("sessions.isTransitioning()", send_body)
        self.assertIn("canSend", source)  # exported

    def test_composer_retains_draft_when_send_blocked(self):
        """R3: a blocked submit must not consume the typed draft."""
        source = _read("src", "static", "js", "conversation", "composer.js")
        self.assertIn("if (!conversation.canSend()) return", source)

    # ---------------------------------------------------------------- R4
    def test_load_history_clears_before_fetching(self):
        """R4: a transcript from A must not be visible under B while B's
        history is pending -- the clear must happen before the fetch."""
        source = _read("src", "static", "js", "conversation", "controller.js")
        body = _function_body(source, "loadHistory")
        clear_idx = body.index("clearTranscript()")
        await_idx = body.index("await api.getSessionMessages")
        self.assertLess(
            clear_idx,
            await_idx,
            "loadHistory must clear the transcript before fetching",
        )

    def test_show_empty_invalidates_and_clears(self):
        source = _read("src", "static", "js", "conversation", "controller.js")
        body = _function_body(source, "showEmpty")
        self.assertIn("invalidateHistory()", body)
        self.assertIn("clearTranscript()", body)

    def test_messages_clear_resets_cursor_and_scroll(self):
        """R2: clearing the transcript must reset cursor/pin/scroll state so a
        stale frame cannot repopulate a removed node."""
        source = _read("src", "static", "js", "conversation", "messages.js")
        body = _function_body(source, "clear")
        self.assertIn("removeCursor()", body)
        self.assertIn("isPinned = true", body)

    # ---------------------------------------------------------------- R6
    def test_title_poll_checks_staleness(self):
        """R6: a title poll that resolves after a newer search/session switch
        must be discarded, not redraw the sidebar."""
        source = _read("src", "static", "js", "sessions", "controller.js")
        body = _function_body(source, "pollForSessionTitle")
        self.assertIn("generation !== refreshGeneration", body)
        self.assertIn("selectedId !== sessionId", body)
        self.assertIn("query !== search.value.trim()", body)

    # ---------------------------------------------------------------- R7
    def test_load_history_resets_context(self):
        """R7: switching sessions must reset the context indicator so a stale
        percentage (e.g. 86%) never persists under the new session."""
        source = _read("src", "static", "js", "conversation", "controller.js")
        body = _function_body(source, "loadHistory")
        self.assertIn("status.resetContext()", body)
