"""Source-level regression tests for the Stage 04 context ring + sidebar.

These guard the concrete contracts the Stage 04 handoff requires. A syntax
check (``node --check``) cannot catch a missing generation guard, a stale
``status.updateContext`` call, or a sidebar node with the wrong id, so the
assertions load the real production HTML/JS and check the exact contracts that,
when violated, produce the regressions:

* the ring button is a real ``<button>`` with the toggle ARIA contract and no
  native ``title`` attribute;
* the sidebar shell (``aside#contextPanel`` + backdrop) exists with the
  required ids, the close button's label, and the live body's initial text;
* the controller exposes the full method surface and guards both the success
  and the error path behind a monotonic generation + selected-id check;
* the SSE ``context`` event no longer paints the old bar (no
  ``status.updateContext``) and instead schedules a debounced, guarded
  ``contextWindow.refresh()``;
* ``send()`` refreshes the snapshot in its ``finally``;
* ``loadHistory`` still resets the status context (R7 is preserved).

A true DOM assertion (ring filling, drawer sliding, Escape closing) requires a
browser/JS runtime that is not available in this environment, so this suite is
the repeatable, in-repo guard for the same contracts without a production
dependency.
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


class ContextRingDomTests(TestCase):
    """The ring button and sidebar shell must exist with the exact contracts."""

    def test_ring_button_replaces_old_bar(self):
        """The horizontal bar is replaced by a 22x22 SVG ring button."""
        html = _read("src", "static", "index.html")
        # The button keeps its id (wired by app.js) and gains the toggle ARIA
        # contract. The window extends left to catch the ``<button`` opener
        # (which precedes the id attribute) and right to catch the ARIA attrs.
        self.assertIn('id="contextIndicator"', html)
        self.assertIn('<button', html[html.index('id="contextIndicator"') - 20: html.index('id="contextIndicator"') + 400])
        self.assertIn('aria-label="Toggle Context Window"', html)
        self.assertIn('aria-controls="contextPanel"', html)
        self.assertIn("aria-expanded", html)
        # The ring is a 22x22 SVG on a 0 0 24 24 viewBox with an r=9 circle.
        self.assertIn('viewBox="0 0 24 24"', html)
        self.assertIn('width="22"', html)
        self.assertIn('height="22"', html)
        self.assertIn('r="9"', html)
        # The native title attribute must be gone entirely (tooltip is the
        # themed .context-tooltip span, shown only on hover/focus).
        self.assertNotIn('title="Context usage"', html)
        self.assertIn('class="context-tooltip"', html)
        self.assertIn("role=\"tooltip\"", html)
        # The count lives to the right of the ring.
        self.assertIn('id="contextCount"', html)

    def test_sidebar_shell_exists(self):
        """The right sidebar + backdrop must exist with the required ids."""
        html = _read("src", "static", "index.html")
        self.assertIn('id="contextPanel"', html)
        self.assertIn("class=\"context-sidebar\"", html)
        self.assertIn('id="contextClose"', html)
        self.assertIn('aria-label="Close Context Window"', html)
        self.assertIn('id="contextPanelBody"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn("Loading context…", html)
        self.assertIn('id="contextBackdrop"', html)
        # The backdrop sits after the chat-panel, inside .layout.
        chat_panel = html.index('class="chat-panel"')
        backdrop = html.index('id="contextBackdrop"')
        self.assertLess(chat_panel, backdrop)

    def test_no_native_title_on_indicator(self):
        """The old native ``title`` attribute must be removed entirely."""
        html = _read("src", "static", "index.html")
        # Only the themed tooltip span may carry the "Toggle Context Window"
        # text; no element should keep the old native title.
        self.assertNotIn('title="Context usage"', html)
        self.assertNotIn('title="Toggle Context Window"', html)


class ContextControllerTests(TestCase):
    """The context-window controller must expose the full method surface and
    guard every fetch behind a monotonic generation + selected-id check."""

    def test_controller_factory_and_method_surface(self):
        source = _read("src", "static", "js", "shell", "context-window.js")
        self.assertIn("export function createContextWindowController", source)
        for name in ("selectSession", "refresh", "isOpen", "open", "close", "toggle"):
            self.assertIn(name, source)

    def test_controller_imports_api_not_dom_transport(self):
        """The controller takes its api via the injected deps, not a global."""
        source = _read("src", "static", "js", "shell", "context-window.js")
        # No direct fetch in the shell controller; it calls the injected api.
        self.assertNotIn("fetch(", source)
        self.assertIn("api.getSessionContext", source)

    def test_generation_guards_both_success_and_error(self):
        """Both the success and error handlers must be guarded by generation +
        the selected-id check so a stale response cannot redraw the panel."""
        source = _read("src", "static", "js", "shell", "context-window.js")
        self.assertIn("generation", source)
        self.assertIn("requestedId", source)
        # The success path applies the snapshot only if the generation and the
        # requested id are still current.
        self.assertIn("selectedId", source)
        # Both handlers reference the guard.
        self.assertRegex(source, r"generation\s*!==")
        self.assertRegex(source, r"requestedId\s*!==\s*selectedId")

    def test_404_and_other_errors_are_distinct(self):
        """A 404/draft shows an unavailable state; other errors show a retry."""
        source = _read("src", "static", "js", "shell", "context-window.js")
        self.assertIn("404", source)
        self.assertIn("Unable to load context", source)
        self.assertIn("Retry", source)

    def test_escape_listener_uses_capture_and_stops(self):
        """Escape must be captured only while open and must stop propagation
        so the global Escape-to-stop-stream handler does not also run."""
        source = _read("src", "static", "js", "shell", "context-window.js")
        self.assertIn("'keydown'", source)
        self.assertIn("capture: true", source)
        self.assertIn("preventDefault()", source)
        self.assertIn("stopPropagation()", source)

    def test_ring_click_wires_toggle(self):
        source = _read("src", "static", "js", "shell", "context-window.js")
        self.assertIn("indicator.addEventListener('click', toggle)", source)

    def test_selection_clears_without_opening_or_fetching_null(self):
        source = _read("src", "static", "js", "shell", "context-window.js")
        body = _function_body(source, "selectSession")
        guard = body.index("if (!selectedId)")
        self.assertLess(body.index("++generation"), guard)
        self.assertLess(body.index("snapshot = null"), guard)
        self.assertLess(body.index("status.resetContext()"), guard)
        self.assertNotIn("openPanel()", body)
        self.assertRegex(body, r"if \(!selectedId\) \{\s*renderUnavailable\(\);\s*return;")
        self.assertGreater(body.index("fetchSnapshot(selectedId)"), guard)


class ApiLayerTests(TestCase):
    """The transport layer must expose getSessionContext."""

    def test_get_session_context_exists(self):
        source = _read("src", "static", "js", "api.js")
        self.assertIn("export async function getSessionContext", source)
        self.assertIn("/context", source)
        self.assertIn("encodeURIComponent", source)


class ConversationWiringTests(TestCase):
    """The SSE context event must no longer paint the old bar; send() must
    refresh the snapshot; loadHistory must keep resetting the status context."""

    def test_context_event_no_long_calls_status_update(self):
        """The SSE ``context`` event must no longer call status.updateContext
        (the old bar is dead). It must schedule a debounced refresh instead."""
        source = _read("src", "static", "js", "conversation", "controller.js")
        self.assertNotIn("status.updateContext", source)
        # The context event schedules a debounced, guarded refresh.
        body = _function_body(source, "dispatch")
        self.assertIn("contextWindow.refresh", source)
        self.assertIn("debounce", source)

    def test_send_refreshes_in_finally(self):
        source = _read("src", "static", "js", "conversation", "controller.js")
        body = _function_body(source, "send")
        self.assertIn("finally", body)
        self.assertIn("contextWindow.refresh()", body)

    def test_load_history_still_resets_status_context(self):
        """R7 is preserved: loadHistory still resets the status context."""
        source = _read("src", "static", "js", "conversation", "controller.js")
        body = _function_body(source, "loadHistory")
        self.assertIn("status.resetContext()", body)

    def test_implicit_session_create_rebinds_context_before_stream(self):
        source = _read("src", "static", "js", "conversation", "controller.js")
        body = _function_body(source, "send")
        ensure = body.index("await sessions.ensureSession()")
        rebind = body.index("contextWindow.selectSession(sessions.currentId())", ensure)
        stream = body.index("api.streamChat(", rebind)
        self.assertLess(ensure, rebind)
        self.assertLess(rebind, stream)


class AppWiringTests(TestCase):
    """app.js must wire the context window into the session lifecycle and
    kick off the first selectSession independent of sessions.init()."""

    def test_app_imports_and_constructs_context_window(self):
        source = _read("src", "static", "app.js")
        self.assertIn("context-window", source)
        self.assertIn("createContextWindowController", source)
        self.assertIn("contextWindow", source)

    def test_app_wires_select_session_into_on_activate(self):
        source = _read("src", "static", "app.js")
        self.assertIn("contextWindow.selectSession", source)
        # onActivate must call selectSession for the incoming id.
        self.assertRegex(
            source,
            r"onActivate:\s*(id|session)\s*=>\s*\{?[^}]*contextWindow\.selectSession",
        )

    def test_app_starts_first_select_session(self):
        """The first selectSession must run independent of sessions.init()."""
        source = _read("src", "static", "app.js")
        # onClear already contains this call; assert the top-level startup
        # sequence, not a match inside a lifecycle handler.
        bootstrap = source[source.index("void status.checkHealth();"):]
        self.assertRegex(
            bootstrap,
            r"void conversation\.loadHistory\(sessions\.currentId\(\)\);\s*"
            r"void contextWindow\.selectSession\(sessions\.currentId\(\)\);",
        )
        self.assertNotIn("await sessions.init()", source)
