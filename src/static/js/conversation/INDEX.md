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
| `tool-cards.js` | Tool-call formatting + grouping | `createToolGroups`, `formatToolArgs`, `formatResult`, `formatBytes` | Pure formatters + a group factory. |
| `reasoning.js` | Reasoning title parse, timing, render | `createReasoningRenderer`, `updateThinkingTitle`, `setThoughtTitle`, `formatDuration` | Per-card RAF/cache. |
| `approval.js` | Approval card + decision POST | `addApproval` | See below. |
| `history.js` | Transcript replay from stored messages | `renderHistory`, `parseToolArgs` | See below. |

## `controller.js` — `createConversationController({ api, sessions, messages, status, contextWindow })`
- Owns `busy`, `abortController`, and per-turn `assistantText`/`assistantBody`/
  `reasoningCard` state. Per-turn state is local to a turn, never a shared
  singleton.
- `send(text)` — guards on `busy`/empty and `sessions.isTransitioning()`;
  **sets busy synchronously before the first `await`** (fix (c)) so a rapid
  second send cannot start a second turn while `ensureSession()` is pending.
  Calls `sessions.ensureSession()`, and on the implicit-creation path
  rebinds `contextWindow.selectSession(sessions.currentId())` before
  `sessions.refresh()`, then iterates `api.streamChat` and dispatches each
  event. On completion/abort it finalizes reasoning, removes
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
  Builds a fresh tool-group context per replay.
- `showEmpty()` — invalidates pending history and clears the transcript.
- `dispatch(event)` handles: `reasoning_delta`, `content_delta`, `context`,
  `tool_call`, `approval_required`, `tool_result`, `error`. A `tool_call` /
  `approval_required` / `tool_result` ends the current assistant message, so
  the next `content_delta` starts a fresh message. The `context` event
  schedules a debounced `contextWindow.refresh()` (250 ms) so the sidebar
  snapshot is refreshed once per turn, not once per token chunk.
- `send()` calls `contextWindow.refresh()` in its `finally` block so the
  ring and sidebar reflect the tokens consumed by the turn.

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
- `createToolGroups({ root })` → `{ ensure(name), break() }` — an independent
  tool-group context so live turns and history replay never share group state.
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

## `approval.js` — `addApproval(event, { messagesView, formatToolArgs })`
- Renders an `approval` card with Approve/Deny controls and POSTs the decision
  via `respondToApproval`. Controls stay enabled for the life of the card and
  are disabled after a decision.

## `history.js` — `renderHistory(msgs, { messagesView, activity, markdown, tools })`
- Rebuilds a transcript from stored messages using the same renderers as the
  live path. Creates a fresh tool-group context per replay. Skips `system`
  messages; attaches `tool` results to the most recent pending tool call's
  group. Computes reasoning duration from the stored `time` section
  (`streamed - created`, falling back to `completed - created`).
- `parseToolArgs(json)` — tolerates string or object arguments.

## Invariants
- The controller is the single writer of turn state; renderers are stateless
  helpers constructed from the injected `messages` view.
- History replay builds fresh tool-group contexts and never reuses a live
  stream's groups.
- The `marked` page global is consumed by `markdown.js`, `activity.js`, and
  `reasoning.js`; `hljs` is touched only by `markdown.js`.
