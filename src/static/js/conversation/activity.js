// Generic activity cards: header with icon, body, collapse state.
// Covers reasoning/tool/result/error/approval cards. Markup-only; the
// reasoning, tool, and approval renderers build on top of this. `marked` is a
// page global (index.html) used only for reasoning bodies.

const activityIcons = {
  reasoning: '⚡',
  tool: '🔧',
  result: '✓',
  error: '✕',
  approval: '⚠',
};

export function createActivityView({ messagesView }) {
  function addActivity(kind, title, body = '', parent = messagesView.el) {
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
      // from _reasoningText) shows the right content. The live flow sets this
      // in the reasoning renderer; history cards must set it too.
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
    if (messagesView.isAtBottom() || kind === 'approval') {
      messagesView.scroll();
    }

    return { card, content, header };
  }

  return { addActivity };
}
