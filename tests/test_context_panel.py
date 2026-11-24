"""Source-level regression tests for the Stage 05 context summary panel.

These guard the concrete contracts the Stage 05 handoff requires. A DOM
assertion (bar filling, legend ordering, file rows) needs a browser/JS
runtime that is not available in this environment, so the suite loads the real
production JS/CSS and checks the exact contracts that, when violated, produce
the regressions:

* ``context-panel.js`` exports a pure ``renderContextPanel`` that rebuilds the
  panel body from an accepted snapshot using ``createElement``/``textContent``/
  ``replaceChildren`` only, with no ``fetch`` and no ``innerHTML`` on the
  untrusted ``system_prompt.text`` / ``loaded_files[].path`` data;
* the category order is exactly
  ``previous_compaction -> user_input -> loaded_files -> model_reasoning ->
  tool_usage -> other -> available`` and the bar/legend share that order;
* the stacked bar is capacity-scaled (``100 * tokens / limit``), not a share
  of the used amount, uses ``overflow:hidden`` + ``flex: 0 0 auto`` segments,
  and never renders ``Infinity%`` when the limit is missing/non-positive;
* the loaded-files legend row is a keyboard-accessible native ``<details>``;
* the system prompt is rendered as inert text (``textContent``), never
  interpolated;
* a stable ``contextRawSection`` anchor is recreated on every render (Stage 06
  replaces only this section);
* ``context-window.js`` imports ``renderContextPanel`` and calls it only after
  the Stage 04 generation + selected-id guard has accepted the snapshot;
* ``styles.css`` imports ``context-panel.css`` before ``responsive.css``.

A true DOM assertion (bar filling, legend ordering, file rows) requires a
browser/JS runtime that is not available in this environment, so this suite is
the repeatable, in-repo guard for the same contracts without a production
dependency.
"""

from pathlib import Path

from unittest import TestCase

ROOT = Path(__file__).resolve().parent.parent


def _read(*parts) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _panel() -> str:
    return _read("src", "static", "js", "shell", "context-panel.js")


def _controller() -> str:
    return _read("src", "static", "js", "shell", "context-window.js")


class PanelRendererContractTests(TestCase):
    """The panel renderer must be a pure, safe snapshot -> DOM renderer."""

    def test_exports_render_context_panel(self):
        source = _panel()
        self.assertIn("export function renderContextPanel", source)

    def test_renderer_is_pure_no_fetch(self):
        """The renderer never fetches; it only receives the accepted snapshot."""
        source = _panel()
        self.assertNotIn("fetch(", source)

    def test_renderer_never_interpolates_untrusted_data(self):
        """system_prompt.text and loaded_files[].path are untrusted local data.
        They must be rendered with textContent, never interpolated into
        innerHTML."""
        source = _panel()
        # The panel must build nodes with createElement and assign text via
        # textContent; it must not build HTML strings for the untrusted fields.
        self.assertIn("createElement", source)
        self.assertIn("textContent", source)
        self.assertIn("replaceChildren", source)
        # No innerHTML assignment anywhere in the renderer.
        self.assertNotIn("innerHTML", source)

    def test_category_order_is_exact(self):
        """The seven categories are in the precise stated order."""
        source = _panel()
        for key in (
            "previous_compaction",
            "user_input",
            "loaded_files",
            "model_reasoning",
            "tool_usage",
            "other",
            "available",
        ):
            self.assertIn(f"'{key}'", source)
        # Order: each key appears before the next in the CATEGORIES literal.
        order = [
            "previous_compaction",
            "user_input",
            "loaded_files",
            "model_reasoning",
            "tool_usage",
            "other",
            "available",
        ]
        positions = [source.index(f"'{key}'") for key in order]
        self.assertEqual(positions, sorted(positions))

    def test_category_labels(self):
        source = _panel()
        for label in (
            "Previous compaction",
            "User input",
            "Loaded files",
            "Model reasoning",
            "Tool usage",
            "Other (system / assistant)",
            "Available",
        ):
            self.assertIn(label, source)


class StackedBarTests(TestCase):
    """The bar must be capacity-scaled, clipped, and never Infinity%."""

    def test_bar_uses_role_img_and_capacity_width(self):
        source = _panel()
        self.assertIn('role', source)
        self.assertIn("context-detail-bar", source)
        # Segment width is 100 * tokens / limit, not a share of used.
        self.assertRegex(source, r"100\s*\*\s*\(?[^)]*context_limit_tokens")

    def test_bar_clips_and_never_infinite(self):
        """Over-limit data must clip (overflow:hidden) and the limit must be
        checked before dividing so a missing/non-positive limit yields no
        Infinity%."""
        source = _panel()
        # The renderer guards the division by a positive limit.
        self.assertRegex(source, r"limit\s*>\s*0")
        # The CSS clips overfull bars.
        css = _read("src", "static", "css", "context-panel.css")
        self.assertIn("overflow:hidden", css)
        # Segments must not be shrunk by flex (capacity share, not used share).
        self.assertIn("flex: 0 0 auto", css)


class LoadedFilesTests(TestCase):
    """The loaded-files legend row must be a native <details> with per-path
    rows, and the file list must be empty-state safe."""

    def test_loaded_files_is_native_details(self):
        source = _panel()
        self.assertIn("createElement('details')", source)
        self.assertIn("createElement('summary')", source)

    def test_file_rows_use_text_content_for_paths(self):
        """File paths are untrusted; they must be textContent, not HTML."""
        source = _panel()
        self.assertIn("context-detail-files-path", source)
        # The path value is assigned via textContent.
        self.assertRegex(source, r"context-detail-files-path[\s\S]*?textContent")

    def test_empty_file_state(self):
        source = _panel()
        self.assertIn("No attributed file reads", source)


class SystemPromptTests(TestCase):
    """The system prompt must be inert text with a source label."""

    def test_system_prompt_is_inert_text(self):
        source = _panel()
        self.assertIn("context-detail-system-text", source)
        # The prompt text is assigned via textContent, preserving newlines.
        self.assertRegex(source, r"context-detail-system-text[\s\S]*?textContent")

    def test_system_prompt_source_labels(self):
        source = _panel()
        self.assertIn("Stored in this conversation", source)
        self.assertIn("Configured", source)

    def test_system_prompt_empty_state(self):
        source = _panel()
        self.assertIn("No system prompt available", source)


class RawAnchorTests(TestCase):
    """A stable contextRawSection anchor must be recreated on every render so
    Stage 06 can replace only that section."""

    def test_raw_anchor_id_is_recreated(self):
        source = _panel()
        self.assertIn("contextRawSection", source)
        self.assertIn("Raw messages", source)
        self.assertIn("Available in the next stage", source)


class ControllerWiringTests(TestCase):
    """context-window.js must call renderContextPanel only after the Stage 04
    guard has accepted the snapshot."""

    def test_controller_imports_renderer(self):
        source = _controller()
        self.assertIn("renderContextPanel", source)
        self.assertIn("context-panel", source)

    def test_controller_still_guards_both_handlers(self):
        """The generation + selected-id guard must remain intact; the renderer
        is reached only through the guarded renderSnapshot."""
        source = _controller()
        self.assertRegex(source, r"generation\s*!==")
        self.assertRegex(source, r"requestedId\s*!==\s*selectedId")
        self.assertIn("renderSnapshot", source)
        # The renderer is called from renderSnapshot, which is only reached
        # after the guard in fetchSnapshot and on the cached open path.
        self.assertIn("renderContextPanel(panelBody", source)


class StylesheetTests(TestCase):
    """styles.css must import context-panel.css before responsive.css, and the
    sheet must define the .context-detail-* selectors using theme tokens."""

    def test_styles_imports_context_panel_before_responsive(self):
        styles = _read("src", "static", "styles.css")
        self.assertIn("context-panel.css", styles)
        self.assertIn("responsive.css", styles)
        self.assertLess(
            styles.index("context-panel.css"),
            styles.index("responsive.css"),
        )

    def test_css_defines_detail_selectors_with_tokens(self):
        css = _read("src", "static", "css", "context-panel.css")
        self.assertIn(".context-detail-bar", css)
        self.assertIn(".context-detail-segment", css)
        self.assertIn(".context-detail-legend", css)
        self.assertIn("var(--", css)
