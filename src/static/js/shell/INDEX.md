# `src/static/js/shell/` — Shell Status, Dialogs, Drawer

Owns shell-level UI that is not part of the chat transcript: the
connectivity badge, the context-usage indicator, the mobile sidebar drawer,
and the accessible delete-confirm dialog.

| File | Purpose | Public API | Notes |
|---|---|---|---|
| `status.js` | Connectivity badge + context indicator | `createStatusView` | See below. |
| `mobile-sidebar.js` | Mobile drawer open/close | `initMobileSidebar` | See below. |
| `confirm-dialog.js` | Accessible delete-confirm dialog | `showConfirmDelete` | See below. |

## `status.js` — `createStatusView({ badge, indicator, fill, pct })`
- `checkHealth()` — `GET /api/health` (via `api.getHealth`); sets the badge to
  `UI ready`/`UI offline`.
- `showOffline()` — marks the badge offline.
- `updateContext(usage)` — sets the context-usage percentage, fill width, and
  the `caution`/`warning`/`danger` class (at 50% / 80% / 95%).
- `resetContext()` — resets the indicator to 0%.

## `mobile-sidebar.js` — `initMobileSidebar({ toggle, sidebar, backdrop })`
- `setOpen(open)` / `close()` — toggles the `open` class on the sidebar and
  backdrop and the toggle's `aria-expanded`.
- Wires the toggle and backdrop click handlers.

## `confirm-dialog.js` — `showConfirmDelete(sessionTitle)`
- Returns a `Promise` that resolves `true` on confirm, `false` on cancel/close
  (backdrop, Escape). Builds the dialog DOM in place.
- Adds a **focus trap** (Tab/Shift+Tab cycle among the two buttons), restores
  focus to the previously active element on close, and **stops Escape
  propagation** so the global Escape-to-stop-stream handler does not run while
  the modal is open.
- Focuses the safe (Cancel) option so a stray Enter never confirms the delete.

## Invariants
- `status.js` is the only shell module that calls a transport helper
  (`api.getHealth`); the other two are DOM-only.
- `confirm-dialog.js` is the single modal; its Escape handler must keep
  `stopPropagation` so the composer's global Escape handler is not triggered.
