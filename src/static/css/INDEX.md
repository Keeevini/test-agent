# `src/static/css/` — Stylesheets

Stylesheets split by domain. `styles.css` (in `src/static/`) is the `@import`
manifest; it loads these sheets in cascade order. The 255 original lines are
preserved verbatim across these files, and the cascade order is retained
(first import wins ties).

| File | Purpose | Notes |
|---|---|---|
| `tokens.css` | `:root` theme variables (OpenCode dark palette, fonts). | All colors/fonts are defined here. |
| `base.css` | Reset, focus ring, body, `ghost-button`/`icon-button`. | |
| `layout.css` | App shell, topbar, badge, layout grid, chat panel. | |
| `sessions.css` | Sidebar, session list, rows, delete button, drawer. | |
| `conversation.css` | Messages, empty state, examples, stream cursor, Markdown, composer. | |
| `activity.css` | Activity cards, reasoning/tool/result/approval, context indicator. | |
| `dialogs.css` | Confirmation dialog (themed, non-native). | |
| `responsive.css` | Mobile drawer, reduced motion, print. | |

## Import order (cascade contract)
`tokens.css` → `base.css` → `layout.css` → `sessions.css` → `conversation.css`
→ `activity.css` → `dialogs.css` → `responsive.css`.

This order is load-bearing: earlier sheets have lower precedence in ties, and
`responsive.css` (mobile drawer, reduced motion, print) must stay last so its
media queries override the base rules. Do not reorder without verifying the
cascade.

## Invariants
- All shared values (colors, fonts) come from `tokens.css`; no sheet redefines
  a token.
- The WAAPI title-hover (`title-scroll.js`) does not rely on any CSS
  transition; the global reduced-motion rule in `responsive.css` disables CSS
  transitions/animations, not WAAPI.
