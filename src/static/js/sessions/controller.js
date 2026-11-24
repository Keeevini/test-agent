// The sessions controller exclusively owns the selected session ID,
// `sessionKnown`, the search refresh sequence, the title-poll timer, and
// persistence of the selected ID. Other modules read via narrow methods, not a
// writable exported variable.

import { renderSessionList } from './list.js';
import { showConfirmDelete } from '../shell/confirm-dialog.js';

const UNTITLED_SESSION_TITLE = 'Untitled Session';

export function createSessionsController({ api, root, search, storage, drawer }) {
  let sessionId = storage.getItem('localAgentSessionId');
  let sessionKnown = false;
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    storage.setItem('localAgentSessionId', sessionId);
  } else {
    sessionKnown = true;
  }

  let searchDebounce = null;
  let titlePollTimer = null;
  let titlePollsLeft = 0;
  let refreshGeneration = 0;
  let metadataSignature = null;
  function notifyMetadata(current) {
    if (!current || current.session_id !== sessionId) return;
    const signature = current.title;
    const changed = metadataSignature?.id === sessionId && metadataSignature.value !== signature;
    metadataSignature = {id: sessionId, value: signature};
    if (changed) lifecycle.onMetadataChanged?.(sessionId);
  }
  // One transition (createNew / remove) is in flight at a time. The lock is
  // acquired before the first await so a delayed mutation cannot overlap a
  // send or a newer navigation.
  let transitionPending = false;

  // Lifecycle hooks wired post-construction to avoid circular imports.
  let lifecycle = { isBusy: () => false, onActivate: () => {}, onClear: () => {} };
  function setLifecycleHandlers(h) {
    lifecycle = h;
  }

  function currentId() {
    return sessionId;
  }

  function isKnown() {
    return sessionKnown;
  }

  function isTransitioning() {
    return transitionPending;
  }

  // Ensure the current session has a row in the database and a valid id.
  // Mark known only after a successful POST; a failed create leaves the draft
  // intact and sessionKnown false.
  async function ensureSession() {
    if (sessionKnown) return sessionId;
    if (!sessionId) {
      sessionId = crypto.randomUUID();
      storage.setItem('localAgentSessionId', sessionId);
    }
    await api.createSession({ session_id: sessionId, title: UNTITLED_SESSION_TITLE });
    sessionKnown = true;
    return sessionId;
  }

  function cancelTitlePolls() {
    clearTimeout(titlePollTimer);
    titlePollTimer = null;
    titlePollsLeft = 0;
  }

  function scheduleTitlePolls(polls = 40) {
    clearTimeout(titlePollTimer);
    titlePollsLeft = polls;
    titlePollTimer = setTimeout(pollForSessionTitle, 3000);
  }

  // Title poll. Competes with searches/refreshes for the same latest-view
  // token; a poll that resolves after a newer request is discarded so a stale
  // result never redraws the current sidebar.
  async function pollForSessionTitle() {
    titlePollTimer = null; // Timer has fired; another refresh may schedule the next one.
    if (titlePollsLeft <= 0 || !sessionKnown) return;
    titlePollsLeft -= 1;

    const generation = ++refreshGeneration;
    const selectedId = sessionId;
    const query = search.value.trim();
    let items;
    try {
      items = await api.listSessions(query);
    } catch (error) {
      console.warn('Session title poll failed', error);
      return;
    }

    if (generation !== refreshGeneration ||
        selectedId !== sessionId ||
        query !== search.value.trim()) return;

    renderSessionList(items, {
      root,
      activeId: sessionId,
      onSelect,
      onDelete,
    });
    const current = items.find(item => item.session_id === sessionId);
    notifyMetadata(current);
    if (current?.title === UNTITLED_SESSION_TITLE && titlePollsLeft > 0) {
      titlePollTimer = setTimeout(pollForSessionTitle, 3000);
    }
  }

  // Monotonic sequence: a newer refresh supersedes older in-flight ones, so a
  // stale search result cannot replace a newer list. A rejected request leaves
  // a recoverable error row so the search box can be retried.
  async function refresh(query) {
    if (query === undefined) query = search.value.trim();
    const generation = ++refreshGeneration;
    let items;
    try {
      items = await api.listSessions(query);
    } catch (error) {
      if (generation !== refreshGeneration || query !== search.value.trim()) return;
      console.warn('Unable to load sessions', error);
      const empty = document.createElement('div');
      empty.className = 'session-list-empty';
      empty.textContent = 'Unable to load sessions. Try searching again.';
      root.replaceChildren(empty);
      return;
    }
    if (generation !== refreshGeneration || query !== search.value.trim()) return;
    renderSessionList(items, {
      root,
      activeId: sessionId,
      onSelect,
      onDelete,
    });
    // The server generates a session's title after the turn's stream ends, so
    // a refresh at the end of a turn can land before the title is written.
    // While the current session is still showing the default title, keep the
    // list refreshing on delayed ticks so the generated title appears without
    // a page reload.
    const current = items.find(item => item.session_id === sessionId);
    notifyMetadata(current);
    if (current?.title === UNTITLED_SESSION_TITLE && !titlePollTimer) {
      scheduleTitlePolls();
    }
  }

  // Block navigation while a turn is running (fix (a)): Stop and approval
  // controls remain available; the active session is preserved on rejection.
  // Navigation is also rejected while a create/delete transition is in flight.
  function activate(targetId) {
    if (lifecycle.isBusy() || transitionPending || targetId === sessionId) return false;
    sessionId = targetId;
    sessionKnown = true;
    storage.setItem('localAgentSessionId', sessionId);
    root.querySelectorAll('.session-item').forEach(item => {
      item.classList.toggle('active', item.dataset.sessionId === sessionId);
    });
    cancelTitlePolls();
    lifecycle.onActivate(sessionId);
    return true;
  }

  // The transition lock is acquired before the first await so a slow POST
  // cannot overlap a send or a newer navigation (repeated New chat is a no-op).
  async function createNew() {
    if (lifecycle.isBusy() || transitionPending) return false;
    transitionPending = true; // Must happen before the first await.
    try {
      const data = await api.createSession({ title: UNTITLED_SESSION_TITLE });
      sessionId = data.session_id;
      sessionKnown = true;
      storage.setItem('localAgentSessionId', sessionId);
      cancelTitlePolls();
      lifecycle.onClear();
      await refresh(search.value.trim());
      return true;
    } catch (error) {
      console.warn('Unable to create new chat', error);
      return false; // Keep the previous session and draft intact.
    } finally {
      transitionPending = false;
    }
  }

  // The transition lock is acquired before the first await so a slow DELETE
  // cannot overlap a send or a newer navigation.
  async function remove(targetId) {
    if (lifecycle.isBusy() || transitionPending) return false;
    transitionPending = true;
    try {
      await api.deleteSession(targetId);
      if (targetId === sessionId) {
        // Deleted the active session: clear the chat and show empty state.
        sessionId = null;
        sessionKnown = false;
        storage.removeItem('localAgentSessionId');
        cancelTitlePolls();
        lifecycle.onClear();
      }
      await refresh(search.value.trim());
      return true;
    } catch (error) {
      console.warn('Unable to delete session', error);
      return false;
    } finally {
      transitionPending = false;
    }
  }

  async function requestDelete(targetId, title) {
    const confirmed = await showConfirmDelete(title);
    if (!confirmed) return;
    await remove(targetId);
  }

  function onSelect(id) {
    if (!activate(id)) return;
    // Close the sidebar on mobile after selecting.
    if (window.matchMedia('(max-width: 920px)').matches) drawer.close();
  }

  function onDelete(id, title) {
    requestDelete(id, title);
  }

  // Load the list and set up search events. The search listener is attached
  // before the initial network request so a rejected first fetch leaves the
  // search box wired for retry. Initial history loading happens exactly once,
  // from the bootstrap (not here).
  async function init() {
    search.addEventListener('input', () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => {
        void refresh(search.value.trim());
      }, 250);
    });
    await refresh('');
  }

  return {
    currentId,
    isKnown,
    isTransitioning,
    ensureSession,
    activate,
    createNew,
    delete: remove,
    refresh,
    init,
    setLifecycleHandlers,
  };
}
