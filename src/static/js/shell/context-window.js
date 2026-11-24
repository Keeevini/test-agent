import { renderContextPanel } from './context-panel.js';
import { createRawMessageInspector } from './context-raw.js';

// The context-window controller owns the right sidebar that shows the
// per-session context snapshot. It is the only module that opens/closes the
// panel, fetches a snapshot, and guards a slow response against a session
// switch. The panel's `hidden` attribute is authoritative: removing it opens
// the panel, setting it closes it (CSS adds display:none!important). The
// controller paints the ring/count through the injected status view and caches
// the latest accepted snapshot so a re-open renders immediately.

export function createContextWindowController({ api, status, layout, indicator, panel, panelBody, closeButton, backdrop }) {
  let selectedId = null;
  let generation = 0; // monotonic: each fetch bumps it; stale responses are dropped
  let snapshot = null; // latest accepted { id, data }
  let open = false;
  let inspector = null; // raw-message inspector for the current raw section

  function isOpen() {
    return open;
  }

  function openPanel() {
    if (open) return;
    open = true;
    panel.removeAttribute('hidden');
    layout.classList.add('context-open');
    backdrop.classList.add('open');
    indicator.setAttribute('aria-expanded', 'true');
    document.addEventListener('keydown', onEscape, { capture: true });
    // Render the cached snapshot immediately if it is for the current
    // selection; a fresh selectSession falls through to the loading state.
    if (snapshot && snapshot.id === selectedId) {
      renderSnapshot(snapshot.data);
    } else if (!selectedId) {
      renderUnavailable();
    }
  }

  function closePanel() {
    if (!open) return;
    open = false;
    panel.setAttribute('hidden', '');
    layout.classList.remove('context-open');
    backdrop.classList.remove('open');
    indicator.setAttribute('aria-expanded', 'false');
    document.removeEventListener('keydown', onEscape, { capture: true });
    inspector?.destroy();
    inspector = null;
  }

  function toggle() {
    if (open) closePanel();
    else openPanel();
  }

  // Escape capture listener: only active while the panel is open (removed on
  // close). It stops propagation so the global Escape-to-stop-stream handler
  // does not also fire. A .confirm-overlay owns Escape while it is open, so we
  // bail out and let it handle the key first.
  function onEscape(event) {
    if (event.key !== 'Escape' && event.keyCode !== 27) return;
    if (document.querySelector('.confirm-overlay')) return;
    event.preventDefault();
    event.stopPropagation();
    closePanel();
  }

  function selectSession(id) {
    // Invalidate in-flight work even when the new selection has no id.
    ++generation;
    selectedId = id ?? null;
    snapshot = null;
    inspector?.destroy();
    inspector = null;
    status.resetContext();
    if (!selectedId) {
      renderUnavailable();
      return;
    }
    void fetchSnapshot(selectedId);
  }

  // Re-fetches the current selection. Never mutates selectedId.
  function refresh() {
    if (!selectedId) return;
    void fetchSnapshot(selectedId);
  }

  async function fetchSnapshot(requestedId) {
    const gen = ++generation;
    // Retire the previous raw inspector before replacing its DOM, including
    // same-session refreshes. A pending snapshot is not a reusable cache.
    inspector?.destroy();
    inspector = null;
    snapshot = null;
    panelBody.textContent = 'Loading context…';
    try {
      const data = await api.getSessionContext(requestedId);
      if (generation !== gen || requestedId !== selectedId) return;
      snapshot = { id: requestedId, data };
      status.updateContext(data);
      if (open) renderSnapshot(data);
      else clearBody();
    } catch (error) {
      if (generation !== gen || requestedId !== selectedId) return;
      if (String((error && error.message) || '').includes('404')) {
        status.resetContext();
        renderUnavailable();
      } else {
        renderError();
      }
    }
  }

  function clearBody() {
    panelBody.textContent = '';
  }

  function renderUnavailable() {
    clearBody();
    panelBody.textContent = 'No context available for this session.';
  }

  function renderError() {
    clearBody();
    panelBody.textContent = 'Unable to load context.';
    const retry = document.createElement('button');
    retry.className = 'context-retry ghost-button';
    retry.type = 'button';
    retry.textContent = 'Retry';
    retry.addEventListener('click', () => refresh());
    panelBody.appendChild(retry);
  }

  // Delegates to the pure panel renderer (context-panel.js). This is reached
  // only after the Stage 04 generation + selected-id guard has accepted the
  // snapshot (fetchSnapshot) or on the cached open path (openPanel), so a stale
  // response can never redraw the panel.
  //
  // After rendering, mounts the raw-message inspector on the new
  // #contextRawSection. The inspector is destroyed and recreated on every
  // accepted snapshot so a session switch never leaks old rows.
  function renderSnapshot(data) {
    inspector?.destroy();
    inspector = null;
    renderContextPanel(panelBody, data);
    const rawSection = panelBody.querySelector('#contextRawSection');
    if (rawSection) {
      const capturedId = selectedId;
      inspector = createRawMessageInspector({
        api,
        root: rawSection,
        sessionId: capturedId,
        isCurrent: () => selectedId === capturedId,
      });
      if (open) {
        inspector.loadFirstPage();
      }
    }
  }

  indicator.addEventListener('click', toggle);
  closeButton.addEventListener('click', () => closePanel());
  backdrop.addEventListener('click', () => closePanel());

  return { selectSession, refresh, isOpen, open: openPanel, close: closePanel, toggle };
}
