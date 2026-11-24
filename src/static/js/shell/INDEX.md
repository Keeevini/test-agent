# `src/static/js/shell/` — Shell Status, Dialogs, Drawer, Context Window

Owns shell-level UI that is not part of the chat transcript: the
connectivity badge, the context-usage ring, the mobile sidebar drawer,
the context-window sidebar, and the accessible delete-confirm dialog.

| File | Purpose | Public API | Notes |
|---|---|---|---|
| `status.js` | Connectivity badge + context ring | `createStatusView` | See below. |
| `mobile-sidebar.js` | Mobile drawer open/close | `initMobileSidebar` | See below. |
| `confirm-dialog.js` | Accessible delete-confirm dialog | `showConfirmDelete` | See below. |
| `context-window.js` | Context-window sidebar controller | `createContextWindowController` | See below. |
| `context-panel.js` | Pure context-panel renderer | `renderContextPanel` | See below. |
| `context-raw.js` | Raw-messages inspector (pagination, accordion DOM) | `createRawMessageInspector` | See below. |

## `status.js` — `createStatusView({ badge, indicator, fill, count })`
- `checkHealth()` — `GET /api/health` (via `api.getHealth`); sets the badge to
  `UI ready`/`UI offline`.
- `showOffline()` — marks the badge offline.
- `updateContext(usage)` — paints the ring's `stroke-dashoffset` (driven by
  `percentage`), sets the formatted token count, and applies the
  `caution`/`warning`/`danger` class (at 50% / 80% / 95%).
- `resetContext()` — resets the ring to empty and the count to `0`.

## `mobile-sidebar.js` — `initMobileSidebar({ toggle, sidebar, backdrop })`
- `setOpen(open)` / `close()` — toggles the `open` class on the sidebar and
  backdrop and the toggle's `aria-expanded`.
- Wires the toggle and backdrop click handlers.

## `context-window.js` — `createContextWindowController({ api, status, layout, indicator, panel, panelBody, closeButton, backdrop })`
- Owns the right sidebar (`#contextPanel`) that shows the per-session context
  snapshot. The `hidden` attribute on the panel is authoritative: removing it
  opens the panel, setting it closes it. The `.context-open` class on the
  layout element toggles the grid column and the mobile drawer transform.
- `selectSession(id)` — sets the current selection and fetches a snapshot.
  **Preserves visibility:** never opens or closes the panel. A null id clears
  the cached snapshot, resets the ring, and renders unavailable **without a
  GET**; a non-null id requests a fresh snapshot. An accepted snapshot updates
  the ring even when the panel is closed; when closed it is cached without
  mounting/fetching raw rows, so a re-open renders the current cache.
- `refresh()` — re-fetches the current selection (never mutates `selectedId`).
- `open()` / `close()` / `toggle()` / `isOpen()` — panel state.
- **Request-race guard:** a monotonic generation counter + `requestedId ===
  selectedId` check ensures a stale response (older generation or a session
  switch) never redraws the panel.
- **Escape capture listener:** active only while the panel is open; calls
  `preventDefault()` and `stopPropagation()` so the global Escape-to-stop
  handler does not also fire. Bails when a `.confirm-overlay` is present so
  the confirm dialog owns Escape.
- 404 → "No context available for this session."; other errors →
  "Unable to load context." with a working Retry button.
- Delegates all panel rendering to `renderContextPanel` (in `context-panel.js`),
  reached only after the request-race guard has accepted the snapshot, so a
  stale response can never redraw the panel.
- **Raw-message inspector:** after each accepted snapshot, mounts a
  `createRawMessageInspector` (in `context-raw.js`) on the `#contextRawSection`
  anchor. The inspector is destroyed before every `fetchSnapshot` and on
  `closePanel()`, so a session switch never leaks old rows. `loadFirstPage()`
  is called only when the panel is open.

## `context-raw.js` — `createRawMessageInspector({ api, root, sessionId, isCurrent })`
- One instance per rendered raw section. Owns pagination, accordion DOM, and
  request invalidation. Returns `{ loadFirstPage, loadMore, destroy }`.
- **Generation-identity guards:** `isStale(gen)` is parameterized; each
  `fetchPage` captures `gen = generation` and checks `isStale(gen)` on
  success, failure, and uses `gen === generation` in `finally` to release
  `loading` only for the currently owned generation. `loadFirstPage` bumps
  generation before the first fetch; `loadMore` requires
  `generation > 0` and a matching generation. `destroy()` bumps generation
  and disposes, invalidating all pending completions.
- **One "Raw messages" heading:** the DOM initialization appends a single
  `<h3 class="context-detail-heading">Raw messages</h3>` after
  `root.replaceChildren()`, before the status, rows, and controls.
- **Native `<details>` rows:** each message row is a `<details><summary>`
  pair with role, seq, optional `created_ms` timestamp, and tool name.
  JSON is lazy-rendered via `JSON.stringify(raw, null, 2)` into
  `<pre><code>` `textContent` only on first expansion (no `innerHTML` from
  raw data).
- **"Load more" button:** shown when `has_more` is true; appends the next page
  to the existing rows. "Retry" button shown on fetch errors. Both controls
  are hidden via the `hidden` attribute (CSS `display: none !important`
  override in `context-panel.css`).
- **Status line:** "No stored messages · Showing 0 of 0" for an empty page;
  "Showing N of M" otherwise.
- **Security:** raw message data is untrusted local content. All message
  content is rendered with `textContent` only; never `innerHTML`.

## `context-panel.js` — `renderContextPanel(container, snapshot)`
- Pure snapshot → DOM renderer for the panel body. Receives only the accepted
  Stage 04 snapshot and rebuilds `container`'s children with
  `createElement`/`textContent`/`replaceChildren`. It never fetches, reads the
  current session id, mutates the ring, or touches global state.
- Renders, in order: the summary (used / limit · percentage), the
  capacity-scaled stacked bar, the category legend, the loaded-files
  `<details>` accordion, the system-prompt `<details>`, and the stable
  `contextRawSection` anchor (Stage 06 replaces only that section).
- Category order is exactly `previous_compaction → user_input → loaded_files →
  model_reasoning → tool_usage → other → available`. The bar is
  capacity-scaled (`100 * tokens / context_limit_tokens`, guarded by a positive
  limit) so a missing/non-positive limit never renders `Infinity%`.
- `system_prompt.text` and `loaded_files[].path` are untrusted local data,
  rendered with `textContent` only, never interpolated into markup.

## `confirm-dialog.js` — `showConfirmDelete(sessionTitle)`
- Returns a `Promise` that resolves `true` on confirm, `false` on cancel/close
  (backdrop, Escape). Builds the dialog DOM in place.
- Adds a **focus trap** (Tab/Shift+Tab cycle among the two buttons), restores
  focus to the previously active element on close, and **stops Escape
  propagation** so the global Escape-to-stop-stream handler does not run while
  the modal is open.
- Focuses the safe (Cancel) option so a stray Enter never confirms the delete.

## Invariants
- `status.js`, `context-window.js`, and `context-raw.js` are the shell modules
  that call transport helpers (`api.getHealth`, `api.getSessionContext`,
  `api.getContextRawMessages`); `mobile-sidebar.js` and `confirm-dialog.js`
  are DOM-only.
- `confirm-dialog.js` is the single modal; its Escape handler must keep
  `stopPropagation` so the composer's global Escape handler is not triggered.
- `context-window.js`'s Escape capture listener must keep
  `stopPropagation` and must bail when a `.confirm-overlay` is present.
