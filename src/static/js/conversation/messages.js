// Message DOM, empty state, scroll pinning, and stream cursor.
// DOM-only state; no networking, no session state.

export function createMessagesView({ messagesEl, emptyState, scrollToBottom }) {
  let isPinned = true;
  let streamCursor = null;

  function scroll(force = false) {
    if (force || isPinned) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  }

  function hideEmptyState() {
    if (emptyState) emptyState.style.display = 'none';
  }

  function showEmpty() {
    if (emptyState) emptyState.style.display = '';
  }

  // Remove every message and reset scroll state. The caller cancels any
  // pending Markdown/reasoning frames first, so a stale frame cannot
  // repopulate a removed node.
  function clear() {
    removeCursor();
    Array.from(messagesEl.children)
      .filter(element => element !== emptyState)
      .forEach(element => element.remove());
    isPinned = true;
    scrollToBottom.hidden = true;
    messagesEl.scrollTop = 0;
  }

  function isAtBottom() {
    return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 40;
  }

  function addMessage(role, initial = '') {
    hideEmptyState();
    const article = document.createElement('article');
    article.className = `message ${role}`;
    const label = document.createElement('div');
    label.className = 'message-label';
    label.textContent = role === 'user' ? 'You' : 'Agent';
    const time = document.createElement('span');
    time.className = 'message-time';
    time.textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    label.append(document.createTextNode(' '), time);
    const body = document.createElement('div');
    body.className = 'message-body';
    body.textContent = initial;
    article.append(label, body);
    if (role === 'assistant') {
      const copyBtn = document.createElement('button');
      copyBtn.className = 'message-copy';
      copyBtn.textContent = 'Copy';
      copyBtn.setAttribute('aria-label', 'Copy message');
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(body.innerText);
        copyBtn.textContent = 'Copied';
        setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
      };
      article.insertBefore(copyBtn, body);
    }
    messagesEl.appendChild(article);
    scroll(true);
    return body;
  }

  function addCursor(body) {
    if (streamCursor) return;
    streamCursor = document.createElement('span');
    streamCursor.className = 'stream-cursor';
    streamCursor.setAttribute('aria-hidden', 'true');
    body.appendChild(streamCursor);
    body._streamCursor = streamCursor;
  }

  function removeCursor() {
    if (streamCursor) {
      streamCursor.remove();
      streamCursor = null;
    }
  }

  // Pin-aware auto-scroll: stop following the bottom when the user scrolls up.
  messagesEl.addEventListener('scroll', () => {
    isPinned = isAtBottom();
    scrollToBottom.hidden = isPinned;
  });
  scrollToBottom.addEventListener('click', () => {
    isPinned = true;
    scroll(true);
  });

  return {
    el: messagesEl,
    addMessage,
    clear,
    showEmpty,
    scroll,
    isAtBottom,
    addCursor,
    removeCursor,
  };
}
