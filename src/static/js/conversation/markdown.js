// Markdown/code rendering and frame scheduling.
//
// Renders markdown `text` into a body element: parses, wraps code blocks with
// copy headers, and (for final renders) highlights code. Uses a per-body
// requestAnimationFrame throttle so a pending frame does not suppress a later
// final render (fix (d)). `marked` and `hljs` are page globals loaded by
// index.html; this module is the single place that touches them.

function renderInto(body, text, { final = false } = {}) {
  const html = marked.parse(text);
  body.innerHTML = html;
  // Re-append the stream cursor if one is attached to this body (innerHTML
  // destroyed it). Owned by the messages view; preserved here.
  if (body._streamCursor) {
    body.appendChild(body._streamCursor);
  }
  // Wrap code blocks with headers.
  body.querySelectorAll('pre').forEach(pre => {
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
  if (final) {
    body.querySelectorAll('code').forEach(code => {
      try { hljs.highlightElement(code); } catch {}
    });
  }
}

// Schedule a non-final render for this body. A pending frame keeps running;
// it reads the latest cached text so rapid deltas collapse into one frame.
function schedule(body, text) {
  body._mdText = text;
  if (body._mdRaf) return;
  body._mdRaf = requestAnimationFrame(() => {
    body._mdRaf = null;
    renderInto(body, body._mdText, { final: false });
  });
}

// Cancel the pending animation frame for this body and render synchronously
// so the final code highlighting runs exactly once.
function flushFinal(body, text) {
  if (body._mdRaf) {
    cancelAnimationFrame(body._mdRaf);
    body._mdRaf = null;
  }
  body._mdText = text;
  renderInto(body, text, { final: true });
}

// Drop a pending frame when the body is cleared so a stale render cannot
// repopulate a removed node.
function cancel(body) {
  if (body._mdRaf) {
    cancelAnimationFrame(body._mdRaf);
    body._mdRaf = null;
  }
}

export { renderInto, schedule, flushFinal, cancel };
