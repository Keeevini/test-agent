# `src/static/` — Browser UI

The browser UI served as static assets. `index.html` is the single document;
`app.js` is the ES-module bootstrap that wires the domain modules together
exactly once; `styles.css` is the `@import` manifest for the stylesheets.

| File | Purpose | Notes |
|---|---|---|
| `index.html` | The HTML document: DOM skeleton, `marked`/`hljs` page globals, the `/static/app.js` module script. | Owns all DOM IDs and classes. |
| `app.js` | ES-module bootstrap. Imports the domain factories, constructs each controller exactly once, and wires lifecycle handlers. No rendering, no SSE switch, no CSS. | See below. |
| `styles.css` | `@import` manifest only. Loads the `css/` sheets in cascade order. | See `css/INDEX.md`. |
| `js/` | ES modules: transport, conversation, sessions, shell. | See `js/INDEX.md`. |
| `css/` | Stylesheets split by domain. | See `css/INDEX.md`. |

## `app.js` — bootstrap wiring
- `import * as api` from `js/api.js`; `createMessagesView`,
  `createConversationController` (conversation/), `initComposer`
  (conversation/), `createSessionsController` (sessions/), `createStatusView`,
  `initMobileSidebar`, and `createContextWindowController` (shell/).
- Constructs the views from `index.html` DOM IDs (`messages`, `emptyState`,
  `scrollToBottom`, `connectionBadge`, `contextIndicator`, `contextRingFill`,
  `contextCount`, `contextPanel`, `contextPanelBody`, `contextClose`,
  `contextBackdrop`, `sidebarToggle`, `sidebar`, `sidebarBackdrop`,
  `sessionList`, `sessionSearch`, `composer`, `prompt`, `sendButton`,
  `clearButton`) and the `.layout` element.
- Wires the `sessions` ↔ `conversation` lifecycle handlers **after**
  construction (`isBusy`, `onActivate`, `onClear`) to avoid circular imports.
  `onActivate` also calls `contextWindow.selectSession(id)`; `onClear` calls
  `contextWindow.selectSession(sessions.currentId())`.
- Listens for `online`/`offline` and drives the status view.
- Runs the one-time startup sequence: `status.checkHealth()`,
  `sessions.init()`, `conversation.loadHistory(sessions.currentId())`,
  `contextWindow.selectSession(sessions.currentId())`, then focuses the prompt.

## Invariants
- The module graph is a DAG; there are no circular imports. The only
  cross-controller hooks are the `sessions` lifecycle handlers, wired in `app.js`.
- `marked` and `hljs` are page globals loaded by `index.html`; `markdown.js` is
  the single module that touches them.
- `styles.css` import order is a cascade contract (first import wins ties). Do
  not reorder the `css/` files.
