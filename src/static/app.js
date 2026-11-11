const messagesEl = document.getElementById('messages');
const composer = document.getElementById('composer');
const promptEl = document.getElementById('prompt');
const sendButton = document.getElementById('sendButton');
const clearButton = document.getElementById('clearButton');
const connectionBadge = document.getElementById('connectionBadge');
const emptyState = document.getElementById('emptyState');
const contextIndicator = document.getElementById('contextIndicator');
const contextFill = document.getElementById('contextFill');
const contextPct = document.getElementById('contextPct');
const sessionListEl = document.getElementById('sessionList');
const sessionSearchEl = document.getElementById('sessionSearch');

let sessionId = localStorage.getItem('localAgentSessionId') || crypto.randomUUID();
localStorage.setItem('localAgentSessionId', sessionId);

let busy = false;
let streamCursor = null;
let rawAssistantText = '';
let renderThrottle = null;
let abortController = null;
let isPinned = true;

function scrollMessages(force = false) {
  if (force || isPinned) {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

function hideEmptyState() {
  if (emptyState) emptyState.style.display = 'none';
}

function isAtBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
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
  scrollMessages(true);
  return body;
}

const activityIcons = {
  reasoning: '⚡',
  tool: '🔧',
  result: '✓',
  error: '✕',
  approval: '⚠',
};

function addActivity(kind, title, body = '', parent = messagesEl) {
  const card = document.createElement('div');
  card.className = `activity-card ${kind}`;
  const header = document.createElement('div');
  header.className = 'activity-card-header';
  const icon = document.createElement('span');
  icon.className = 'activity-icon';
  icon.textContent = activityIcons[kind] || '';
  header.appendChild(icon);
  header.appendChild(document.createTextNode(title));
  const isMarkdown = (kind === 'reasoning');
  const content = document.createElement(isMarkdown ? 'div' : 'pre');
  content.className = `activity-card-body${isMarkdown ? ' markdown' : ''}`;
  if (isMarkdown) {
    content.innerHTML = marked.parse(body);
    // Cache the text on the element so the expand handler (which re-renders
    // from _reasoningText) shows the right content. The live flow sets this in
    // renderReasoningCard; history cards must set it too.
    content._reasoningText = body;
  } else {
    content.textContent = body;
  }
  card.append(header, content);

  // Collapse reasoning, tool, and result cards by default
  if (kind === 'reasoning' || kind === 'tool' || kind === 'result') {
    card.classList.add('collapsed');
    header.addEventListener('click', () => {
      card.classList.toggle('collapsed');
      // Render the cached reasoning text once when the card is expanded.
      if (kind === 'reasoning' && !card.classList.contains('collapsed')) {
        content.innerHTML = marked.parse(content._reasoningText || '');
      }
    });
  }

  parent.appendChild(card);

  // Pin to bottom only if user is already at bottom, or if it's an approval
  if (isAtBottom(messagesEl) || kind === 'approval') {
    scrollMessages();
  }

  return { card, content, header };
}

function setBusy(value) {
  busy = value;
  promptEl.disabled = value;
  sendButton.querySelector('.send-label').textContent = value ? 'Stop' : 'Send';
  sendButton.querySelector('.send-icon').textContent = value ? '■' : '↑';
  if (!value) promptEl.focus();
}

function updateContextIndicator(usage) {
  if (!contextFill || !contextPct || !contextIndicator) return;
  const percent = Math.max(0, Math.min(100, usage.percentage || 0));
  contextPct.textContent = `${Math.round(percent)}%`;
  contextFill.style.width = `${percent}%`;
  contextIndicator.classList.remove('caution', 'warning', 'danger');
  if (percent >= 95) {
    contextIndicator.classList.add('danger');
  } else if (percent >= 80) {
    contextIndicator.classList.add('warning');
  } else if (percent >= 50) {
    contextIndicator.classList.add('caution');
  }
  contextIndicator.title = `Context: ${usage.tokens} / ${usage.max_tokens} tokens`;
}

function resetContextIndicator() {
  if (!contextFill || !contextPct || !contextIndicator) return;
  contextPct.textContent = '0%';
  contextFill.style.width = '0%';
  contextIndicator.classList.remove('caution', 'warning', 'danger');
  contextIndicator.title = 'Context: 0 tokens';
}

// ---------------------------------------------------------------------------
// Session management
// ---------------------------------------------------------------------------

let searchDebounce = null;
let sessionsLoading = false;

function formatSessionDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr;
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const isYesterday = d.getTime() === new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1).getTime();
  if (isToday) return d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  if (isYesterday) return 'Yesterday';
  return d.toLocaleDateString([], {month: 'short', day: 'numeric'});
}

function formatTokens(n) {
  if (n == null) return '';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

async function fetchSessions(query = '') {
  try {
    const url = query
      ? `/api/sessions/search?q=${encodeURIComponent(query)}`
      : '/api/sessions';
    const r = await fetch(url);
    if (!r.ok) return null;
    const data = await r.json();
    return data.sessions || [];
  } catch {
    return null;
  }
}

function renderSessionList(sessions) {
  sessionListEl.innerHTML = '';
  if (!sessions.length) {
    const empty = document.createElement('div');
    empty.className = 'session-list-empty';
    empty.textContent = 'No sessions found.';
    sessionListEl.appendChild(empty);
    return;
  }
  for (const s of sessions) {
    const item = document.createElement('div');
    item.className = `session-item${s.session_id === sessionId ? ' active' : ''}`;
    item.setAttribute('role', 'option');
    item.dataset.sessionId = s.session_id;

    const title = document.createElement('div');
    title.className = 'session-title';
    title.textContent = s.title || 'New Chat';

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

    item.append(title, meta);
    item.addEventListener('click', () => {
      switchSession(s.session_id);
      // Close the sidebar on mobile after selecting
      if (window.matchMedia('(max-width: 920px)').matches) setSidebarOpen(false);
    });
    sessionListEl.appendChild(item);
  }
}

async function refreshSessionList(query = '') {
  const sessions = await fetchSessions(query);
  if (sessions) renderSessionList(sessions);
}

// Parse the JSON arguments string from a stored tool_call into an object.
// Falls back to an empty object if parsing fails.
function parseToolArgs(json) {
  if (!json) return {};
  if (typeof json === 'object') return json;
  try {
    const parsed = JSON.parse(json);
    return typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

async function renderHistoryMessages(msgs) {
  // Clear existing messages
  messagesEl.innerHTML = '';
  if (!msgs.length) {
    emptyState.style.display = '';
    return;
  }

  // Track tool calls whose results haven't been attached yet. In the stored
  // structure, an assistant message with tool_calls is followed by tool
  // messages carrying the results, in order.
  let pendingToolResults = [];

  for (const m of msgs) {
    const role = m.role;
    if (role === 'system') continue;

    if (role === 'user') {
      const content = m.content || '';
      if (content.trim()) {
        addMessage('user', content);
      }
      continue;
    }

    if (role === 'tool') {
      // Attach the result to the most recent pending tool call's group.
      const result = m.content || '';
      const entry = pendingToolResults.shift();
      if (entry) {
        addActivity('result', `Tool result · ${entry.name}`, result, entry.group.body);
        entry.group.countEl.textContent = `×${entry.group.n}`;
        entry.group.n += 1;
      }
      continue;
    }

    if (role === 'assistant') {
      const content = m.content || '';
      const reasoning = m.reasoning_content || '';
      const toolCalls = m.tool_calls || [];

      // Start a fresh tool group for this message
      breakToolGroup();
      pendingToolResults = [];

      // Reasoning card. The duration is computed from the stored `time`
      // section (OpenCode's `created`/`streamed`/`completed`). Prefer
      // `streamed - created` (pure generation time); fall back to
      // `completed - created` for older messages without `streamed`.
      // Messages with no timing at all show the bare "Thought" label.
      if (reasoning.trim()) {
        const reasoningCard = addActivity('reasoning', 'Thought', reasoning);
        const time = m.time;
        if (time && time.created != null) {
          const end = time.streamed != null ? time.streamed : time.completed;
          if (end != null) {
            setThoughtTitle(reasoningCard.header, end - time.created);
          }
        }
      }

      // Tool calls
      for (const tc of toolCalls) {
        const name = tc.function?.name || 'tool';
        const args = parseToolArgs(tc.function?.arguments);
        const group = ensureToolGroup(name);
        addActivity('tool', `Tool request · ${name}`, formatToolArgs(name, args), group.body);
        group.countEl.textContent = `×${group.n}`;
        group.n += 1;
        pendingToolResults.push({ group, name });
      }

      // Final content
      if (content.trim()) {
        const body = addMessage('assistant', content);
        renderMarkdownInto(body, content, true);
      }
    }
  }
  scrollMessages(true);
}

async function switchSession(targetId) {
  if (targetId === sessionId) return;
  sessionId = targetId;
  localStorage.setItem('localAgentSessionId', sessionId);
  // Clear and load history
  messagesEl.innerHTML = '';
  emptyState.style.display = 'none';
  try {
    const r = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`);
    if (!r.ok) throw new Error('failed');
    const data = await r.json();
    renderHistoryMessages(data.messages || []);
  } catch {
    emptyState.style.display = '';
  }
  // Update active state in sidebar
  sessionListEl.querySelectorAll('.session-item').forEach(item => {
    item.classList.toggle('active', item.dataset.sessionId === sessionId);
  });
  resetContextIndicator();
}

async function createNewSession() {
  try {
    const r = await fetch('/api/sessions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title: 'New Chat'}),
    });
    if (!r.ok) return;
    const data = await r.json();
    sessionId = data.session_id;
    localStorage.setItem('localAgentSessionId', sessionId);
    // Clear the chat
    messagesEl.innerHTML = '';
    emptyState.style.display = '';
    resetContextIndicator();
    // Refresh the list to show the new session
    await refreshSessionList(sessionSearchEl.value.trim());
  } catch {}
}

async function checkHealth() {
  try {
    const r = await fetch('/api/health');
    if (!r.ok) throw new Error('health failed');
    connectionBadge.textContent = 'UI ready';
    connectionBadge.className = 'badge online';
  } catch {
    connectionBadge.textContent = 'UI offline';
    connectionBadge.className = 'badge offline';
  }
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const TOOL_ARG_ORDER = ['path', 'query', 'glob', 'source', 'destination', 'recursive', 'content'];

function formatToolArgs(name, args) {
  if (!args || typeof args !== 'object') return String(args ?? '');
  const keys = Object.keys(args).sort((a, b) => {
    const ia = TOOL_ARG_ORDER.indexOf(a);
    const ib = TOOL_ARG_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });
  return keys.map(key => {
    let value = args[key];
    if (key === 'content' && typeof value === 'string') {
      value = `${value.length} chars`;
    } else if (value === null || value === undefined) {
      value = '""';
    } else if (typeof value === 'object') {
      try { value = JSON.stringify(value); } catch { value = String(value); }
    }
    return `${key}: ${value}`;
  }).join('\n');
}

function formatResult(name, result, meta, ok) {
  if (!ok) return result;
  const m = meta || {};
  if (name === 'read_file' && m.lines != null) {
    return `${m.lines} lines · ${formatBytes(m.bytes)}`;
  }
  if (name === 'search_text' && m.matches != null) {
    return `${m.matches} match${m.matches === 1 ? '' : 'es'} in ${m.files} file${m.files === 1 ? '' : 's'}`;
  }
  if (name === 'list_directory' && m.entries != null) {
    return `${m.entries} entrie${m.entries === 1 ? 'y' : 'ies'}`;
  }
  return result;
}

let toolGroup = null;

function breakToolGroup() {
  toolGroup = null;
}

function ensureToolGroup(name) {
  if (toolGroup && toolGroup.name === name) return toolGroup;
  breakToolGroup();
  const card = document.createElement('div');
  card.className = 'activity-card group collapsed';
  const header = document.createElement('div');
  header.className = 'activity-card-header';
  const icon = document.createElement('span');
  icon.className = 'activity-icon';
  icon.textContent = '🔧';
  header.appendChild(icon);
  const label = document.createElement('span');
  label.textContent = name;
  header.appendChild(label);
  const count = document.createElement('span');
  count.className = 'group-count';
  count.textContent = '×1';
  header.appendChild(count);
  const body = document.createElement('div');
  body.className = 'group-body';
  card.append(header, body);
  header.addEventListener('click', () => card.classList.toggle('collapsed'));
  messagesEl.appendChild(card);
  toolGroup = { card, header, countEl: count, body, name, n: 1 };
  return toolGroup;
}

function formatDuration(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}

function setThoughtTitle(header, durationMs) {
  // Replace only the title text, keeping the ⚡ icon intact. Shows
  // "Thought · {duration}" with the duration in a muted span.
  const icon = header.querySelector('.activity-icon');
  Array.from(header.childNodes).forEach(node => {
    if (node !== icon) node.remove();
  });
  const label = 'Thought · ';
  header.appendChild(document.createTextNode(label));
  const durSpan = document.createElement('span');
  durSpan.className = 'thought-duration';
  durSpan.textContent = formatDuration(durationMs);
  header.appendChild(durSpan);
}

function finalizeReasoningCard(reasoningCard, reasoningStartTime) {
  if (!reasoningCard || !reasoningStartTime) return;
  const elapsed = Date.now() - reasoningStartTime;
  setThoughtTitle(reasoningCard.header, elapsed);
}

async function resolveApproval(id, approved, controls) {
  controls.querySelectorAll('button').forEach(b => b.disabled = true);
  const response = await fetch('/api/approval', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({approval_id: id, approved}),
  });
  if (!response.ok) {
    controls.insertAdjacentHTML('beforeend', '<span class="approval-state">Could not submit decision.</span>');
    return;
  }
  controls.insertAdjacentHTML('beforeend', `<span class="approval-state">${approved ? 'Approved' : 'Denied'}</span>`);
}

function addApproval(event) {
  const card = document.createElement('div');
  card.className = 'activity-card approval';
  const header = document.createElement('div');
  header.className = 'activity-card-header';
  const icon = document.createElement('span');
  icon.className = 'activity-icon';
  icon.textContent = '⚠';
  header.appendChild(icon);
  header.appendChild(document.createTextNode(`Approval required · ${event.name}`));
  const pre = document.createElement('pre');
  pre.className = 'activity-card-body';
  pre.textContent = formatToolArgs(event.name, event.arguments);
  const controls = document.createElement('div');
  controls.className = 'approval-controls';
  const approve = document.createElement('button');
  approve.className = 'approve-button';
  approve.textContent = 'Approve';
  approve.setAttribute('aria-label', `Approve ${event.name}`);
  const deny = document.createElement('button');
  deny.className = 'deny-button';
  deny.textContent = 'Deny';
  deny.setAttribute('aria-label', `Deny ${event.name}`);
  approve.onclick = () => resolveApproval(event.approval_id, true, controls);
  deny.onclick = () => resolveApproval(event.approval_id, false, controls);
  controls.append(approve, deny);
  card.append(header, pre, controls);
  messagesEl.appendChild(card);
  scrollMessages();
}

function addStreamCursor(assistantBody) {
  if (streamCursor) return;
  streamCursor = document.createElement('span');
  streamCursor.className = 'stream-cursor';
  streamCursor.setAttribute('aria-hidden', 'true');
  assistantBody.appendChild(streamCursor);
}

function removeStreamCursor() {
  if (streamCursor) {
    streamCursor.remove();
    streamCursor = null;
  }
}

// Performs the synchronous DOM work of rendering markdown `text` into
// `assistantBody`: parse, wrap code blocks with copy headers, and (for final
// renders) highlight code. Shared by the live streaming path (throttled via
// requestAnimationFrame, using the accumulated `rawAssistantText`) and the
// history path (rendered synchronously from each message's own content).
function renderMarkdownInto(assistantBody, text, isFinal = false) {
  const html = marked.parse(text);
  assistantBody.innerHTML = html;
  // Re-append stream cursor if it was destroyed by innerHTML
  if (streamCursor) {
    assistantBody.appendChild(streamCursor);
  }
  // Wrap code blocks with headers
  assistantBody.querySelectorAll('pre').forEach(pre => {
    if (pre.parentElement?.classList.contains('code-block')) return;
    const code = pre.querySelector('code');
    const langClass = code?.className?.match(/language-(\w+)/)?.[1] || '';
    const wrapper = document.createElement('div');
    wrapper.className = 'code-block';
    const headerDiv = document.createElement('div');
    headerDiv.className = 'code-block-header';
    const langSpan = document.createElement('span');
    langSpan.className = 'code-lang';
    langSpan.textContent = langClass || 'text';
    const copyBtn = document.createElement('button');
    copyBtn.className = 'copy-btn';
    copyBtn.setAttribute('aria-label', 'Copy code');
    copyBtn.textContent = 'Copy';
    copyBtn.onclick = () => {
      navigator.clipboard.writeText(code?.textContent || pre.textContent || '');
      copyBtn.textContent = 'Copied';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
    };
    headerDiv.append(langSpan, copyBtn);
    pre.parentNode.insertBefore(wrapper, pre);
    wrapper.append(headerDiv, pre);
  });
  // Highlight code only on the final render, not on every frame.
  if (isFinal) {
    assistantBody.querySelectorAll('code').forEach(code => {
      try { hljs.highlightElement(code); } catch {}
    });
  }
}

function renderMarkdown(assistantBody, isFinal = false) {
  if (renderThrottle) return;
  renderThrottle = requestAnimationFrame(() => {
    renderThrottle = null;
    renderMarkdownInto(assistantBody, rawAssistantText, isFinal);
  });
}

const THINKING_PARAGRAPH_THRESHOLD = 300;

// Find the start index of the most recent reasoning part. Parts are delimited
// by a blank line followed by a bold title (\n\n**Title**). The first part
// begins at index 0. Searching starts at fromIndex so already-processed text
// is not re-scanned on every delta.
function findLastPartBoundary(text, fromIndex) {
  let pos = fromIndex;
  let last = fromIndex;
  while (true) {
    pos = text.indexOf('\n\n**', pos);
    if (pos === -1) break;
    last = pos + 2;
    pos += 2;
  }
  return last;
}

// Parse the leading bold title from a part's text. Returns null when the bold
// block is incomplete (still streaming) or absent.
function parsePartTitle(text, partStart) {
  const part = text.slice(partStart);
  const match = part.match(/^\*\*([^*\n]+)\*\*(?:\r?\n\r?\n|$)/);
  if (!match) return null;
  return match[1].trim();
}

// The header shows the title of the most recent reasoning part. The title is
// parsed once per part and cached on the header element (dataset), so the
// per-delta path is O(1) after the initial parse. No filler-word fallback:
// if no bold title is present, the header shows bare "Thinking".
function updateThinkingTitle(header, text) {
  const cachedStart = parseInt(header.dataset.thinkingPartStart, 10) || 0;
  const partStart = findLastPartBoundary(text, cachedStart);

  if (partStart !== cachedStart) {
    header.dataset.thinkingPartStart = String(partStart);
    header.dataset.thinkingTitle = '';
  }

  if (!header.dataset.thinkingTitle) {
    const title = parsePartTitle(text, partStart);
    if (title) {
      header.dataset.thinkingTitle = title;
    }
  }

  const title = header.dataset.thinkingTitle;
  const label = title ? 'Thinking · ' + title : 'Thinking';
  if (header.dataset.thinkingLabel === label) return;
  header.dataset.thinkingLabel = label;
  const icon = header.querySelector('.activity-icon');
  Array.from(header.childNodes).forEach(node => {
    if (node !== icon) node.remove();
  });
  header.appendChild(document.createTextNode(label));
}

let renderReasoningThrottle = null;
function renderReasoningCard(content, text) {
  // Cache the latest text on the element itself so each card renders its
  // own content (not the latest card's text) when expanded later.
  content._reasoningText = text;
  const card = content.closest('.activity-card');
  // Skip the DOM work while the card is collapsed. The reasoning text is the
  // largest chunk in the stream, and re-parsing it on every animation frame
  // pegs a CPU core in this tab, which competes with llama.cpp for the
  // machine's CPU and drags generation down to ~0.7 tok/s.
  if (card && card.classList.contains('collapsed')) return;
  if (renderReasoningThrottle) return;
  renderReasoningThrottle = requestAnimationFrame(() => {
    renderReasoningThrottle = null;
    content.innerHTML = marked.parse(content._reasoningText);
  });
}

async function sendMessage(text) {
  if (busy || !text.trim()) return;
  addMessage('user', text.trim());
  rawAssistantText = '';
  let assistantBody = null;
  let reasoningCard = null;
  let reasoningStartTime = null;
  let reasoningText = '';
  toolGroup = null;
  setBusy(true);
  abortController = new AbortController();
  const signal = abortController.signal;

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, message: text.trim()}),
      signal,
    });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});

      const frames = buffer.split('\n\n');
      buffer = frames.pop() || '';
      for (const frame of frames) {
        const line = frame.split('\n').find(x => x.startsWith('data:'));
        if (!line) continue;
        let event;
        try {
          event = JSON.parse(line.slice(5).trim());
        } catch (parseErr) {
          console.warn('SSE frame parse failure:', parseErr, line);
          continue;
        }

        if (event.type === 'reasoning_delta') {
          if (!reasoningCard) {
            reasoningStartTime = Date.now();
            reasoningText = '';
            reasoningCard = addActivity('reasoning', 'Thinking', '');
          }
          reasoningText += event.text;
          updateThinkingTitle(reasoningCard.header, reasoningText);
          renderReasoningCard(reasoningCard.content, reasoningText);
        } else if (event.type === 'content_delta') {
          if (!assistantBody) {
            assistantBody = addMessage('assistant', '');
          }
          if (reasoningCard && reasoningStartTime) {
            finalizeReasoningCard(reasoningCard, reasoningStartTime);
            reasoningCard = null;
            reasoningStartTime = null;
          }
          rawAssistantText += event.text;
          addStreamCursor(assistantBody);
          renderMarkdown(assistantBody);
          scrollMessages();
        } else if (event.type === 'context') {
          updateContextIndicator(event);
        } else if (event.type === 'tool_call') {
          if (reasoningCard && reasoningStartTime) {
            finalizeReasoningCard(reasoningCard, reasoningStartTime);
            reasoningCard = null;
            reasoningStartTime = null;
          }
          // A tool segment ends the current assistant message, so the next
          // content_delta starts a fresh message positioned after the tools.
          assistantBody = null;
          rawAssistantText = '';
          const group = ensureToolGroup(event.name);
          addActivity('tool', `Tool request · ${event.name}`, formatToolArgs(event.name, event.arguments), group.body);
          group.countEl.textContent = `×${group.n}`;
          group.n += 1;
        } else if (event.type === 'approval_required') {
          if (reasoningCard && reasoningStartTime) {
            finalizeReasoningCard(reasoningCard, reasoningStartTime);
            reasoningCard = null;
            reasoningStartTime = null;
          }
          assistantBody = null;
          rawAssistantText = '';
          breakToolGroup();
          addApproval(event);
        } else if (event.type === 'tool_result') {
          if (reasoningCard && reasoningStartTime) {
            finalizeReasoningCard(reasoningCard, reasoningStartTime);
            reasoningCard = null;
            reasoningStartTime = null;
          }
          assistantBody = null;
          rawAssistantText = '';
          const group = ensureToolGroup(event.name);
          addActivity(event.ok ? 'result' : 'error', `${event.ok ? 'Tool result' : 'Tool failed'} · ${event.name}`, formatResult(event.name, event.result || '', event.meta, event.ok), group.body);
        } else if (event.type === 'error') {
          if (reasoningCard && reasoningStartTime) {
            finalizeReasoningCard(reasoningCard, reasoningStartTime);
            reasoningCard = null;
            reasoningStartTime = null;
          }
          addActivity('error', 'Agent error', event.message || 'Unknown error');
          if (!rawAssistantText.trim()) {
            rawAssistantText = `Error: ${event.message}`;
          }
        }
      }
    }

    removeStreamCursor();
    if (assistantBody) {
      if (!rawAssistantText.trim()) {
        assistantBody.textContent = '(No final text response.)';
      } else {
        renderMarkdown(assistantBody, true);
      }
    }
  } catch (error) {
    removeStreamCursor();
    if (signal.aborted) {
      if (assistantBody) {
        if (!rawAssistantText.trim()) {
          assistantBody.textContent = '(Stopped.)';
        } else {
          renderMarkdown(assistantBody, true);
        }
      }
      addActivity('error', 'Request stopped', 'Cancelled by user');
    } else {
      if (assistantBody) {
        assistantBody.textContent = `Request failed: ${error.message}`;
      }
      addActivity('error', 'Connection error', error.stack || String(error));
    }
  } finally {
    if (reasoningCard && reasoningStartTime) {
      finalizeReasoningCard(reasoningCard, reasoningStartTime);
    }
    setBusy(false);
    abortController = null;
  }
}

composer.addEventListener('submit', event => {
  event.preventDefault();
  if (busy) {
    if (abortController) abortController.abort();
    return;
  }
  const text = promptEl.value;
  promptEl.value = '';
  promptEl.style.height = 'auto';
  sendMessage(text);
});

promptEl.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

promptEl.addEventListener('input', () => {
  promptEl.style.height = 'auto';
  promptEl.style.height = `${Math.min(promptEl.scrollHeight, 180)}px`;
});

document.querySelectorAll('.example').forEach(button => {
  button.addEventListener('click', () => {
    promptEl.value = button.textContent;
    promptEl.focus();
  });
});

clearButton.addEventListener('click', async () => {
  if (busy) return;
  if (!confirm('Start a new chat? This keeps the previous conversation in your history.')) return;
  await createNewSession();
});

// Pin-aware auto-scroll: stop following the bottom when the user scrolls up
const scrollToBottom = document.getElementById('scrollToBottom');
messagesEl.addEventListener('scroll', () => {
  isPinned = isAtBottom(messagesEl);
  scrollToBottom.hidden = isPinned;
});
scrollToBottom.addEventListener('click', () => {
  isPinned = true;
  scrollMessages(true);
});

// Browser offline detection
window.addEventListener('online', () => { checkHealth(); });
window.addEventListener('offline', () => {
  connectionBadge.textContent = 'Offline';
  connectionBadge.className = 'badge offline';
});

// Escape stops the in-flight request
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && busy && abortController) {
    abortController.abort();
  }
});

// Session list: initial load and search
refreshSessionList('');

// Load the current session's messages on page load
(async () => {
  try {
    const r = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`);
    if (!r.ok) return;
    const data = await r.json();
    renderHistoryMessages(data.messages || []);
  } catch {}
})();
sessionSearchEl.addEventListener('input', () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    refreshSessionList(sessionSearchEl.value.trim());
  }, 250);
});

// Mobile sidebar toggle
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarEl = document.getElementById('sidebar');
const sidebarBackdrop = document.getElementById('sidebarBackdrop');
function setSidebarOpen(open) {
  sidebarEl.classList.toggle('open', open);
  sidebarBackdrop.classList.toggle('open', open);
  sidebarToggle.setAttribute('aria-expanded', String(open));
}
sidebarToggle.addEventListener('click', () => {
  setSidebarOpen(!sidebarEl.classList.contains('open'));
});
sidebarBackdrop.addEventListener('click', () => setSidebarOpen(false));

checkHealth();
promptEl.focus();
