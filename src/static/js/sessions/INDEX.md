# `src/static/js/sessions/` — Session State & Sidebar

Owns the selected session ID, the `sessionKnown` flag, search refresh, the
title-poll timer, and persistence of the selected ID. Other modules read via
narrow methods, never a writable exported variable. `list.js` is view-only;
`title-scroll.js` is the WAAPI title-hover.

| File | Purpose | Public API | Notes |
|---|---|---|---|
| `controller.js` | Selected-session state + lifecycle | `createSessionsController` | See below. |
| `list.js` | Sidebar row rendering | `renderSessionList`, `formatSessionDate`, `formatTokens`, `TRASH_ICON` | View-only; no fetch. |
| `title-scroll.js` | WAAPI title hover | `startTitleScroll`, `stopTitleScroll`, `TITLE_SCROLL_SPEED` | Preserve unchanged. |

## `controller.js` — `createSessionsController({ api, root, search, storage, drawer })`
- Owns `sessionId`, `sessionKnown`, `refreshGeneration`, `titlePollTimer`, and
  `searchDebounce`.
- `init()` — attaches the debounced search listener **before** the initial
  network request (so a rejected first fetch leaves the search box wired for
  retry), then loads the list. Initial history loading is done by the
  bootstrap, not here.
- `currentId()`, `isKnown()`, `isTransitioning()`.
- `ensureSession()` — ensures the current session has a row in the database.
  Marks `sessionKnown` only after a successful POST; a failed create leaves the
  draft intact and `sessionKnown` false.
- `activate(targetId)` — **blocks navigation while a turn is running**
  (fix (a)) **or a create/delete transition is in flight**; Stop and approval
  controls remain available. Updates the active row, cancels title polls, and
  calls `lifecycle.onActivate`.
- `createNew()` — creates a new session (guarded by `isBusy` and the
  transition lock), clears the chat via `lifecycle.onClear`, and refreshes.
- `refresh(query)` — monotonic generation guard so a stale search result cannot
  replace a newer list. A rejected request (network **or** HTTP) leaves a
  recoverable `session-list-empty` row with `textContent` (not HTML) so the
  search box can be retried. While the current session still shows the default
  title, schedules delayed title polls (the server writes the title after the
  turn's stream ends).
- `remove(targetId)` / `requestDelete(targetId, title)` — delete a session
  (guarded by `isBusy` and the transition lock); deleting the active session
  clears the chat. `requestDelete` confirms via the shell dialog first.
- Transition lock: `createNew`/`remove` acquire a `transitionPending` flag
  **before** the first `await` and release it in `finally`. A failed
  mutation releases the lock and keeps the previous session and draft intact.
  `isTransitioning()` exposes the flag so the conversation controller can
  reject a `send()` while a transition is in flight.
- Title polls share the same `refreshGeneration` token as searches/refreshes;
  a poll that resolves after a newer request, a session switch, or a search
  query change is discarded so it cannot redraw the current sidebar.
- `setLifecycleHandlers(h)` — wires `isBusy`/`onActivate`/`onClear`
  post-construction to avoid circular imports.
- `onSelect(id)` / `onDelete(id, title)` — close the drawer on mobile after
  selecting.

## `list.js`
- `renderSessionList(sessions, { root, activeId, onSelect, onDelete })` —
  renders sidebar rows. View-only (no fetch). Delegates title hover to
  `title-scroll.js` via row-level `mouseenter`/`mouseleave` listeners.
- `formatSessionDate(dateStr, now)` — compares by local calendar date; normalizes
  the DB's UTC strings (`YYYY-MM-DD HH:MM:SS`) to an explicit UTC timestamp.
- `formatTokens(n)`; `TRASH_ICON` — the per-session delete button glyph.

## `title-scroll.js`
- `startTitleScroll(titleEl)` / `stopTitleScroll(titleEl)` — WAAPI animation
  that slowly reveals overflowing titles while a row is hovered.
- `TITLE_SCROLL_SPEED = 75` — pixels per second.
- **Invariant:** hover is an explicit interaction, so this runs even when
  reduced motion is set; WAAPI does not rely on the CSS transitions disabled by
  the global reduced-motion media rule. Preserve unchanged.

## Invariants
- The selected session ID is owned here; other modules read via `currentId()`.
- `activate`/`createNew`/`remove` are guarded by `lifecycle.isBusy()` and
  `transitionPending` so a running turn cannot be abandoned mid-stream and a
  slow create/delete cannot overlap a send or a newer navigation.
