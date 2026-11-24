// Transport-only HTTP layer. No DOM access, no session/turn state.
// Each helper corresponds to one existing endpoint and preserves the
// current request/response JSON payloads.

export async function getHealth() {
  const r = await fetch('/api/health');
  if (!r.ok) throw new Error(`Health failed: HTTP ${r.status}`);
  return r.json();
}

export async function listSessions(query = '') {
  const url = query
    ? `/api/sessions/search?q=${encodeURIComponent(query)}`
    : '/api/sessions';
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Session list failed: HTTP ${r.status}`);
  const data = await r.json();
  return data.sessions || [];
}

export async function createSession({ session_id, title }) {
  const r = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id, title }),
  });
  if (!r.ok) throw new Error(`Session create failed: HTTP ${r.status}`);
  return r.json();
}

export async function deleteSession(id) {
  const r = await fetch(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!r.ok) throw new Error(`Session delete failed: HTTP ${r.status}`);
  return r.json();
}

export async function getSessionMessages(id) {
  const r = await fetch(`/api/sessions/${encodeURIComponent(id)}/messages`);
  if (!r.ok) throw new Error(`Session messages failed: HTTP ${r.status}`);
  return r.json();
}

export async function getSessionContext(id) {
  const r = await fetch(`/api/sessions/${encodeURIComponent(id)}/context`);
  if (!r.ok) throw new Error(`Session context failed: HTTP ${r.status}`);
  return r.json();
}

export async function getContextRawMessages(id, { offset = 0, limit = 20 } = {}) {
  const url = `/api/sessions/${encodeURIComponent(id)}/context/messages`
    + `?offset=${encodeURIComponent(offset)}&limit=${encodeURIComponent(limit)}`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Context raw messages failed: HTTP ${response.status}`);
  return response.json();
}

export async function respondToApproval(approval_id, approved) {
  const r = await fetch('/api/approval', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approval_id, approved }),
  });
  if (!r.ok) {
    const error = new Error(`Approval failed: HTTP ${r.status}`);
    error.status = r.status;
    throw error;
  }
  return r.json();
}

// Incremental SSE decoder: normalizes CRLF, keeps a trailing CR across
// transport chunks so a split CRLF pair is still normalized, and flushes any
// final trailing frame after EOF. Yields JSON event objects.
export async function* decodeSseEvents(response) {
  if (!response.ok || !response.body) {
    throw new Error(`Chat failed: HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let pendingCR = false;

  function appendText(text) {
    if (pendingCR) {
      text = '\r' + text;
      pendingCR = false;
    }
    if (text.endsWith('\r')) {
      text = text.slice(0, -1);
      pendingCR = true;
    }
    buffer += text.replace(/\r\n?/g, '\n');
  }

  function* takeFrames(final = false) {
    let boundary;
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const payload = frame.split('\n')
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart())
        .join('\n');
      if (!payload || payload === '[DONE]') continue;
      try { yield JSON.parse(payload); }
      catch (error) { console.warn('Invalid SSE frame', error); }
    }
    if (final && buffer.trim()) {
      const payload = buffer.split('\n')
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart())
        .join('\n');
      if (payload && payload !== '[DONE]') {
        try { yield JSON.parse(payload); }
        catch (error) { console.warn('Invalid trailing SSE frame', error); }
      }
      buffer = '';
    }
  }

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      appendText(decoder.decode(value, { stream: true }));
      yield* takeFrames();
    }
    appendText(decoder.decode());
    if (pendingCR) buffer += '\n';
    yield* takeFrames(true);
  } finally {
    reader.releaseLock();
  }
}

export async function* streamChat(payload, signal) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });
  yield* decodeSseEvents(response);
}
