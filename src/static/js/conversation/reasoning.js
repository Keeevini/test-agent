// Reasoning/thought cards: title parsing, timing, and throttled rendering.
// Each card stores its own cached text and RAF so two cards never share a
// renderer's queued content. `marked` is a page global (index.html).

export function formatDuration(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}

// Replace only the title text, keeping the ⚡ icon intact. Shows
// "Thought · {duration}" with the duration in a muted span.
export function setThoughtTitle(header, durationMs) {
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
export function updateThinkingTitle(header, text) {
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

// Renderer for a single reasoning card's body. RAF/cached text are stored on
// the content element so cards never share queued content.
export function createReasoningRenderer() {
  function render(content, text) {
    content._reasoningText = text;
    const card = content.closest('.activity-card');
    // Skip the DOM work while the card is collapsed. The reasoning text is the
    // largest chunk in the stream, and re-parsing it on every animation frame
    // pegs a CPU core in this tab, which competes with llama.cpp for the
    // machine's CPU and drags generation down.
    if (card && card.classList.contains('collapsed')) return;
    if (content._reasoningRaf) return;
    content._reasoningRaf = requestAnimationFrame(() => {
      content._reasoningRaf = null;
      content.innerHTML = marked.parse(content._reasoningText);
    });
  }

  function cancel(content) {
    if (content._reasoningRaf) {
      cancelAnimationFrame(content._reasoningRaf);
      content._reasoningRaf = null;
    }
  }

  function finalize(card, startMs) {
    if (!card || !startMs) return;
    const elapsed = Date.now() - startMs;
    setThoughtTitle(card.header, elapsed);
  }

  return { render, cancel, finalize };
}
