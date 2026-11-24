# `src/static/js/conversation/` — Transcript & Streaming

Owns the chat transcript, activity cards, and the live SSE stream.
`controller.js` is the single owner of active-turn state (busy flag,
`AbortController`, per-turn text); the other modules are renderers it
constructs. History replay reuses the same renderers with fresh, per-render
group contexts so a live stream and a replay never share state.

| File | Purpose | Public API | Notes |
|---|---|---|---|
| `controller.js` | Active-turn state + SSE dispatch | `createConversationController` | See below. |
| `composer.js` | Composer handlers + Enter/Escape | `initComposer` | See below. |
| `messages.js` | Message DOM, empty state, scroll, cursor | `createMessagesView` | See below. |
| `markdown.js` | Markdown/code render + frame scheduling | `renderInto`, `schedule`, `flushFinal`, `cancel` | The only module touching `marked`/`hljs`. |
| `activity.js` | Generic activity cards | `createActivityView` | See below. |
| `tool-cards.js` | Tool-call formatting, grouping + shared renderer | `createToolGroups`, `createToolActivityRenderer`, `formatToolArgs`, `formatResult`, `formatBytes`, `toolLabel` | Pure formatters, group factory, and a shared renderer for live/history dispatch. |
| `reasoning.js` | Reasoning title parse, timing, render | `createReasoningRenderer`, `updateThinkingTitle`, `setThoughtTitle`, `formatDuration` | Per-card RAF/cache. |
| `approval.js` | Approval card + decision POST | `addApproval` | See below. |
| `history.js` | Transcript replay from stored messages | `renderHistory`, `parseToolArgs` | See below. |

## `controller.js` — `createConversationController({ api, sessions, messages, status })`
- Owns `busy`, `abortController`, and per-turn `assistantText`/`assistantBody`/
  `reasoningCard` state. Per-turn state is local to a turn, never a shared
  singleton.
- `send(text)` — guards on `busy`/empty and `sessions.isTransitioning()`;
  **sets busy synchronously before the first `await`** (fix (c)) so a rapid
  second send cannot start a second turn while `ensureSession()` is pending.
  Calls `sessions.ensureSession()`, then iterates `api.streamChat` and
  dispatches each event. On completion/abort it finalizes reasoning, removes
  the cursor, and renders the final Markdown via `flushFinal` (which cancels
  the pending RAF and renders synchronously — fix (d)). Refreshes the sidebar
  in `finally`.
- `stop()` — aborts the in-flight `AbortController`.
- `isBusy()` — active-turn state only. `canSend()` — a turn may start only
  when nothing is busy **and** no session transition is in flight. Kept
  separate from `isBusy()` so the composer's Stop path does not fire while a
  create/delete transition is pending (there is no `AbortController` to
  stop).
- `onBusyChange(fn)` — busy state and a single listener registry (used by
  the composer to mirror state).
- `loadHistory(requestedId)` — **clears the transcript immediately** (cancels
  pending Markdown/reasoning frames, then `messages.clear()` +
  `messages.showEmpty()`) and resets the status context, so a transcript from
  the previous session is never visible under a newly selected one while the
  fetch is pending. **Stale-response guard:** a generation counter ensures
  only the most recent request, against the current session, is applied.
  Builds a fresh tool-group context and shared renderer per replay. Passes
  `stored.tool_aliases` (from the GET messages response) as `toolAliases` to
  `renderHistory` for display labels.
- `showEmpty()` — invalidates pending history and clears the transcript.
- `dispatch(event)` handles: `reasoning_delta`, `content_delta`,
  `model_part_end`, `context`, `status`, `tool_call`, `approval_required`,
  `approval_decision`, `tool_result`, `error`. A `tool_call` /
  `approval_required` / `tool_result` ends the current assistant message, so
  the next `content_delta` starts a fresh message. A `status` event is a
  model-step boundary: it breaks the tool renderer so each step gets its own
  groups. `model_part_end` finalizes the current run: `kind: 'reasoning'`
  calls `endReasoning(event.duration_ms)` (authoritative server duration) and
  `kind: 'content'` calls `endAssistant()`; the `reasoning_delta` /
  `content_delta` handlers are the fallback that also finalizes, so a missing
  end event still leaves no half-built card.
- `endReasoning(durationMs)` / `endAssistant()` — idempotent finalizers
  invoked from `model_part_end`, from EOF, from the `catch`/`finally` paths,
  and from status/tool boundaries, so a reasoning or assistant run is never
  left open.
- A `terminal_error` event is persisted on the last assistant row at the
  step-cap and surfaced through the same dispatch as other events.
- Per-turn `toolRenderer` (from `createToolActivityRenderer`): owns
  invocation and approval maps. `tool_call` / `approval_required` /
  `approval_decision` / `tool_result` are dispatched to `toolRenderer.handle()`.
  The `finally` block calls `toolRenderer.finish()` so no actionable controls
  survive a finished turn.

## `composer.js` — `initComposer({ composer, promptEl, sendButton, clearButton, conversation, onNewChat })`
- Mirrors the conversation's busy state onto the input/send button (single
  listener, registered once).
- Handles form submit (send or stop), Enter/Shift+Enter, textarea autosize
  (max 180px), the example-prompt buttons, and the clear button.
- Registers the global Escape-to-stop. The confirm dialog stops Escape
  propagation, so this never fires while a modal is open.

## `messages.js` — `createMessagesView({ messagesEl, emptyState, scrollToBottom })`
- `addMessage(role, initial)` → the message body element; `addCursor(body)` /
  `removeCursor()` — the stream cursor; `clear()`, `showEmpty()`, `scroll(force)`,
  `isAtBottom()`, `el`.
- `removeCursor()` clears the body's `_streamCursor` reference before removing
  the cursor node, so a finalized run never leaves an orphan cursor behind.
- Pin-aware auto-scroll: stops following the bottom when the user scrolls up,
  re-pinned by the scroll-to-bottom button.

## `markdown.js`
- `renderInto(body, text, { final })` — parses `marked`, re-attaches the stream
  cursor, wraps code blocks in copy-header wrappers, and highlights code only
  when `final`.
- `schedule(body, text)` — per-body `requestAnimationFrame` throttle; a pending
  frame keeps running and reads the latest cached text, so rapid deltas
  collapse into one frame.
- `flushFinal(body, text)` — cancels the pending RAF and renders synchronously
  with `final: true`, so the final code highlighting runs exactly once
  (fix (d)).
- `cancel(body)` — drops a pending frame when the body is cleared.

## `activity.js` — `createActivityView({ messagesView })`
- `addActivity(kind, title, body, parent)` → `{ card, content, header }`.
  `kind` ∈ `reasoning`/`tool`/`result`/`error`/`approval`. Reasoning bodies are
  Markdown (`marked`) and collapse by default, with the text cached on the
  element so expand re-renders correctly.

## `tool-cards.js`
- `toolLabel(name, alias)` — display-only label resolver: returns the trimmed
  alias when non-empty, else the canonical `name`, else `'tool'`. Never used as
  a lookup key; group identity remains the canonical name.
- `createToolGroups({ root })` → `{ ensure(name, alias), break(), countInvocation(group, id) }`
  — an independent tool-group context so live turns and history replay never
  share group state. Counting is ID-based: one count per distinct attempted
  invocation ID. Group headers display `toolLabel(name, alias)`.
- `createToolActivityRenderer({ messagesView, activity, tools })` →
  `{ handle(event, { historical }), break(), finish() }` — the shared renderer
  for both live and history paths. Owns per-step `invocations` and `approvals`
  maps. Dispatches `tool_call` / `approval_required` / `approval_decision` /
  `tool_result` / `tool_unavailable` events into the correct group by ID.
  `finish()` marks unresolved live cards unavailable and clears maps.
- `formatToolArgs(name, args)`, `formatResult(name, result, meta, ok)`,
  `formatBytes(n)` — pure formatters. `TOOL_ARG_ORDER` fixes argument ordering.

## `reasoning.js`
- `createReasoningRenderer()` → `{ render, cancel, finalize }`. Each card stores
  its own `_reasoningText` and RAF so two cards never share queued content.
  `render` skips DOM work while the card is collapsed (the reasoning text is
  the largest chunk in the stream).
- `updateThinkingTitle(header, text)` — shows the title of the most recent
  reasoning part; parsed once per part and cached on the header (`dataset`) so
  the per-delta path is O(1) after the initial parse.
- `setThoughtTitle(header, durationMs)`; `formatDuration(ms)`.

## `approval.js` — `addApproval(event, { messagesView, formatToolArgs, parent, historical, displayName })`
- Returns `{ card, setDecision(status), markUnavailable() }`.
- Terminal `approved|denied|cancelled` is immutable: buttons are physically
  removed and replaced by a single status node. Live pending keeps two buttons
  with a `submitting` guard; a 404 maps to a neutral "Decision unavailable"
  state. Transient errors (e.g. 500) re-enable the buttons and show one
  `approval-state.error`.
- `historical` (default `false`) renders read-only: no POST, no buttons, no
  pulse. The `parent` option defaults to `messagesView.el`.
- `displayName` (optional) overrides the header/aria-label text; falls back to
  `event.name` when absent.

## `history.js` — `renderHistory(msgs, { messagesView, activity, markdown, tools, toolRenderer, reasoning, toolAliases })`
- Rebuilds a transcript from stored messages using the same renderers as the
  live path. Creates a fresh tool-group context and shared renderer per
  replay. Skips `system` messages. Tool rows are matched by explicit
  `tool_call_id` within the same assistant step (no FIFO across steps).
  Unmatched rows render as neutral diagnostics.
- `toolAliases` (optional, default `{}`) — a current-registry snapshot of
  `{ canonicalName: displayAlias }` passed to tool events for display labels.
- `renderStepParts(m, { messagesView, activity, markdown })` — reconstructs
  the ordered reasoning/content runs from a stored `activity.parts`
  aggregate. Validates every descriptor **before any DOM mutation**: the
  per-kind `chars` sums must equal the code-point lengths of
  `reasoning_content` / `content` (`Array.from`), and each reasoning part's
  `duration_ms` must be a non-negative integer. A valid aggregate renders in
  descriptor order, slicing each source by code points and calling
  `setThoughtTitle` on reasoning parts; any mismatch (or a missing/`version
  !== 1` aggregate) falls back to legacy reasoning-then-content with a
  `time`-derived duration (`streamed - created`, falling back to
  `completed - created`).
- A stored `terminal_error` on the last assistant row is rendered as
  `activity.addActivity('error', 'Agent error', ...)` after the tool-result
  loop.
- `parseToolArgs(json)` — tolerates string or object arguments; preserves
  raw primitives/arrays.

## Invariants
- The controller is the single writer of turn state; renderers are stateless
  helpers constructed from the injected `messages` view.
- History replay builds fresh tool-group contexts and never reuses a live
  stream's groups.
- The `marked` page global is consumed by `markdown.js`, `activity.js`, and
  `reasoning.js`; `hljs` is touched only by `markdown.js`.
