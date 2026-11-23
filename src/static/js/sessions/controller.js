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

  // Lifecycle hooks wired post-construction to avoid circular imports.
  const lifecycle = { isBusy: () => false, onActivate: () => {}, onClear: () => {} };
  function setLifecycleHandlers(h) {
    lifecycle = h;
  }

  function currentId() {
    return sessionId;
  }

  function isKnown() {
    return sessionKnown;
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

  async function pollForSessionTitle() {
    if (titlePollsLeft <= 0) {
      titlePollTimer = null;
      return;
    }
    titlePollsLeft -= 1;
    if (!sessionKnown) {
      titlePollTimer = null;
      return;
    }
    const sessions = await api.listSessions(search.value.trim());
    if (!sessions) {
      titlePollTimer = null;
      return;
    }
    renderSessionList(sessions, {
      root,
      activeId: sessionId,
      onSelect: onSelect,
      onDelete: onDelete,
    });
    const current = sessions.find(s => s.session_id === sessionId);
    if (current && current.title === UNTITLED_SESSION_TITLE && titlePollsLeft > 0) {
      titlePollTimer = setTimeout(pollForSessionTitle, 3000);
    } else {
      titlePollTimer = null;
    }
  }

  // Monotonic sequence: a newer refresh supersedes older in-flight ones, so a
  // stale search result cannot replace a newer list.
  async function refresh(query) {
    if (query === undefined) query = search.value.trim();
    const generation = ++refreshGeneration;
    const sessions = await api.listSessions(query);
    if (generation !== refreshGeneration) return;
    if (!sessions) return;
    renderSessionList(sessions, {
      root,
      activeId: sessionId,
      onSelect: onSelect,
      onDelete: onDelete,
    });
    // The server generates a session's title after the turn's stream ends, so
    // a refresh at the end of a turn can land before the title is written.
    // While the current session is still showing the default title, keep the
    // list refreshing on delayed ticks so the generated title appears without
    // a page reload.
    const current = sessions.find(s => s.session_id === sessionId);
    if (current && current.title === UNTITLED_SESSION_TITLE && !titlePollTimer) {
      scheduleTitlePolls();
    }
  }

  // Block navigation while a turn is running (fix (a)): Stop and approval
  // controls remain available; the active session is preserved on rejection.
  async function activate(targetId) {
    if (lifecycle.isBusy()) return;
    if (targetId === sessionId) return;
    sessionId = targetId;
    sessionKnown = true;
    storage.setItem('localAgentSessionId', sessionId);
    root.querySelectorAll('.session-item').forEach(item => {
      item.classList.toggle('active', item.dataset.sessionId === sessionId);
    });
    cancelTitlePolls();
    lifecycle.onActivate(sessionId);
  }

  async function createNew() {
    if (lifecycle.isBusy()) return;
    const data = await api.createSession({ title: UNTITLED_SESSION_TITLE });
    sessionId = data.session_id;
    sessionKnown = true;
    storage.setItem('localAgentSessionId', sessionId);
    cancelTitlePolls();
    lifecycle.onClear();
    await refresh(search.value.trim());
  }

  async function remove(targetId) {
    if (lifecycle.isBusy()) return;
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
  }

  async function requestDelete(targetId, title) {
    const confirmed = await showConfirmDelete(title);
    if (!confirmed) return;
    await remove(targetId);
  }

  function onSelect(id) {
    activate(id);
    // Close the sidebar on mobile after selecting.
    if (window.matchMedia('(max-width: 920px)').matches) drawer.close();
  }

  function onDelete(id, title) {
    requestDelete(id, title);
  }

  // Load the list and set up search events. Initial history loading happens
  // exactly once, from the bootstrap (not here).
  async function init() {
    await refresh('');
    search.addEventListener('input', () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => {
        refresh(search.value.trim());
      }, 250);
    });
  }

  return {
    currentId,
    isKnown,
    ensureSession,
    activate,
    createNew,
    delete: remove,
    refresh,
    init,
    setLifecycleHandlers,
  };
}
