// Sidebar row rendering and date/token labels. View-only: no fetch. Title
// hover is delegated to title-scroll.js via row-level listeners.

import { startTitleScroll, stopTitleScroll } from './title-scroll.js';

// Trash glyph used by the per-session delete button (stroke = currentColor,
// so it picks up the button's text colour on hover).
export const TRASH_ICON =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>';

// Date labels are compared by local calendar date. The DB stores UTC strings
// (YYYY-MM-DD HH:MM:SS); normalize them to an explicit UTC timestamp so the
// comparison is unambiguous, then compare calendar dates (not epoch times).
export function formatSessionDate(dateStr, now = new Date()) {
  if (!dateStr) return '';
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(dateStr)
    ? dateStr.replace(' ', 'T') + 'Z'
    : dateStr;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return dateStr;

  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  }
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return date.toLocaleDateString([], {month: 'short', day: 'numeric'});
}

export function formatTokens(n) {
  if (n == null) return '';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function renderSessionList(sessions, { root, activeId, onSelect, onDelete }) {
  root.innerHTML = '';
  if (!sessions.length) {
    const empty = document.createElement('div');
    empty.className = 'session-list-empty';
    empty.textContent = 'No sessions found.';
    root.appendChild(empty);
    return;
  }
  for (const s of sessions) {
    const item = document.createElement('div');
    item.className = `session-item${s.session_id === activeId ? ' active' : ''}`;
    item.setAttribute('role', 'option');
    item.dataset.sessionId = s.session_id;

    const title = document.createElement('div');
    title.className = 'session-title';
    const titleSpan = document.createElement('span');
    titleSpan.textContent = s.title || 'Untitled Session';
    title.appendChild(titleSpan);
    // Listen on the whole item (not just the title strip) so the scroll
    // triggers wherever the pointer rests on the row, e.g. the meta line.
    item.addEventListener('mouseenter', () => startTitleScroll(title));
    item.addEventListener('mouseleave', () => stopTitleScroll(title));

    const meta = document.createElement('div');
    meta.className = 'session-meta';
    const date = formatSessionDate(s.updated_at || s.created_at);
    if (date) {
      const dateSpan = document.createElement('span');
      dateSpan.className = 'session-date';
      dateSpan.textContent = date;
      meta.appendChild(dateSpan);
    }
    if (s.tokens_total) {
      const tokSpan = document.createElement('span');
      tokSpan.className = 'session-tokens';
      tokSpan.textContent = `${formatTokens(s.tokens_total)} tok`;
      meta.appendChild(tokSpan);
    }

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'session-delete-btn';
    deleteBtn.setAttribute('aria-label', `Delete ${s.title || 'Untitled Session'}`);
    deleteBtn.title = 'Delete session';
    deleteBtn.innerHTML = TRASH_ICON;
    deleteBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      onDelete(s.session_id, s.title || 'Untitled Session');
    });

    item.append(title, meta, deleteBtn);
    item.addEventListener('click', () => onSelect(s.session_id));
    root.appendChild(item);
  }
}
