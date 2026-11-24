// Raw-messages inspector: one instance per rendered raw section.
// Owns pagination, accordion DOM, and request invalidation.
//
// Security: raw message data is untrusted local content. All message content
// is rendered with textContent only; never innerHTML. JSON is pretty-printed
// via JSON.stringify into <pre><code> textContent.
//
// The inspector never calls sessions.activate, changes selected session ID,
// starts/stops a chat turn, or modifies the ring.

export function createRawMessageInspector({ api, root, sessionId, isCurrent }) {
  let disposed = false;
  let generation = 0;
  let loading = false;
  let total = 0;
  let loadedCount = 0;
  let nextOffset = 0;

  // DOM elements managed by this inspector.
  let statusEl = null;
  let rowsContainer = null;
  let loadMoreBtn = null;
  let retryBtn = null;

  function isStale(gen) {
    return disposed || gen !== generation || !isCurrent();
  }

  function clearRows() {
    if (rowsContainer) {
      rowsContainer.replaceChildren();
    }
  }

  function clearAll() {
    if (statusEl) statusEl.replaceChildren();
    clearRows();
    if (loadMoreBtn) loadMoreBtn.replaceChildren();
    if (retryBtn) retryBtn.replaceChildren();
  }

  function setStatus(text) {
    if (!statusEl) return;
    statusEl.textContent = text;
  }

  function showLoadMore(visible) {
    if (!loadMoreBtn) return;
    if (visible) {
      loadMoreBtn.textContent = 'Load more';
      loadMoreBtn.disabled = false;
      loadMoreBtn.removeAttribute('hidden');
    } else {
      loadMoreBtn.setAttribute('hidden', '');
      loadMoreBtn.disabled = true;
    }
  }

  function showError(text) {
    setStatus(text);
    showLoadMore(false);
    if (!retryBtn) return;
    retryBtn.textContent = 'Retry';
    retryBtn.disabled = false;
    retryBtn.removeAttribute('hidden');
  }

  function buildRow(message) {
    const details = document.createElement('details');
    details.className = 'context-detail-raw-row';

    const summary = document.createElement('summary');
    summary.className = 'context-detail-raw-summary';

    // Chevron (CSS triangle).
    const chevron = document.createElement('span');
    chevron.className = 'context-detail-raw-chevron';
    summary.appendChild(chevron);

    // Role label.
    const roleSpan = document.createElement('span');
    roleSpan.className = `context-detail-raw-role context-detail-raw-role--${message.role}`;
    roleSpan.textContent = message.role;
    summary.appendChild(roleSpan);

    // Sequence number.
    const seqSpan = document.createElement('span');
    seqSpan.className = 'context-detail-raw-seq';
    seqSpan.textContent = `#${message.seq}`;
    summary.appendChild(seqSpan);

    // Optional local timestamp from created_ms.
    if (message.created_ms != null) {
      const timeSpan = document.createElement('span');
      timeSpan.className = 'context-detail-raw-time';
      const d = new Date(message.created_ms);
      timeSpan.textContent = d.toLocaleString();
      summary.appendChild(timeSpan);
    }

    // Short tool name when context_meta.name is a string.
    const raw = message.raw;
    if (raw && raw.context_meta && typeof raw.context_meta.name === 'string') {
      const toolSpan = document.createElement('span');
      toolSpan.className = 'context-detail-raw-tool';
      toolSpan.textContent = raw.context_meta.name;
      summary.appendChild(toolSpan);
    }

    summary.appendChild(document.createElement('span')); // spacer
    details.appendChild(summary);

    // Lazy JSON: create <pre><code> on first expansion only.
    const pre = document.createElement('pre');
    pre.className = 'context-detail-raw-json';
    const code = document.createElement('code');
    let jsonRendered = false;

    details.addEventListener('toggle', () => {
      if (!details.open || jsonRendered) return;
      jsonRendered = true;
      try {
        code.textContent = JSON.stringify(raw, null, 2);
      } catch {
        code.textContent = 'Unable to format stored message';
      }
    });

    pre.appendChild(code);
    details.appendChild(pre);

    return details;
  }

  function appendRows(messages) {
    if (!rowsContainer) return;
    const frag = document.createDocumentFragment();
    for (const msg of messages) {
      frag.appendChild(buildRow(msg));
    }
    rowsContainer.appendChild(frag);
  }

  async function fetchPage(offset, isAppend) {
    if (loading || isStale(generation)) return; // prevent duplicate concurrent loads
    loading = true;
    const gen = generation;

    try {
      const data = await api.getContextRawMessages(sessionId, { offset, limit: 20 });

      if (isStale(gen)) return;

      total = data.total;
      nextOffset = data.offset + data.messages.length;
      loadedCount += data.messages.length;

      if (!isAppend) {
        clearRows();
        loadedCount = data.messages.length;
      }

      appendRows(data.messages);

      // Update status.
      setStatus(total === 0
        ? 'No stored messages · Showing 0 of 0'
        : `Showing ${loadedCount} of ${total}`);

      // Load more button.
      showLoadMore(data.has_more);

      // Hide retry on success.
      if (retryBtn) {
        retryBtn.setAttribute('hidden', '');
        retryBtn.disabled = true;
      }
    } catch (error) {
      if (isStale(gen)) return;
      const msg = (error && error.message) || 'Unknown error';
      showError(`Unable to load raw messages: ${msg}`);
    } finally {
      if (gen === generation) loading = false;
    }
  }

  function loadFirstPage() {
    if (disposed || !isCurrent()) return;
    generation++;
    loading = false;
    nextOffset = 0;
    loadedCount = 0;
    total = 0;
    clearRows();
    setStatus('Loading…');
    showLoadMore(false);
    if (retryBtn) {
      retryBtn.setAttribute('hidden', '');
      retryBtn.disabled = true;
    }
    void fetchPage(0, false);
  }

  function loadMore() {
    if (generation === 0 || isStale(generation) || loading) return;
    void fetchPage(nextOffset, true);
  }

  function destroy() {
    disposed = true;
    generation++;
    clearAll();
  }

  // Initialize DOM structure.
  root.replaceChildren();

  const heading = document.createElement('h3');
  heading.className = 'context-detail-heading';
  heading.textContent = 'Raw messages';
  root.appendChild(heading);

  statusEl = document.createElement('div');
  statusEl.className = 'context-detail-raw-status';
  root.appendChild(statusEl);

  rowsContainer = document.createElement('div');
  rowsContainer.className = 'context-detail-raw-rows';
  root.appendChild(rowsContainer);

  loadMoreBtn = document.createElement('button');
  loadMoreBtn.className = 'context-detail-raw-loadmore ghost-button';
  loadMoreBtn.type = 'button';
  loadMoreBtn.setAttribute('hidden', '');
  loadMoreBtn.disabled = true;
  loadMoreBtn.addEventListener('click', loadMore);
  root.appendChild(loadMoreBtn);

  retryBtn = document.createElement('button');
  retryBtn.className = 'context-detail-raw-retry ghost-button';
  retryBtn.type = 'button';
  retryBtn.setAttribute('hidden', '');
  retryBtn.disabled = true;
  retryBtn.addEventListener('click', loadFirstPage);
  root.appendChild(retryBtn);

  return { loadFirstPage, loadMore, destroy };
}
