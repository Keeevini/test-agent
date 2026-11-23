// Tool call formatting and grouping.
//
// `createToolGroups` returns an independent group context so live turns and
// history replay never share group state (fix: per-rendering-context groups).
// The formatters are pure and exported independently.

export const TOOL_ARG_ORDER = ['path', 'query', 'glob', 'source', 'destination', 'recursive', 'content'];

export function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatToolArgs(name, args) {
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

export function formatResult(name, result, meta, ok) {
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

// Returns an independent tool-group context: { ensure(name), break() }.
// `root` is the container the group cards are appended to; `addActivity` is
// used by the caller to place tool requests/results inside a group body.
export function createToolGroups({ root }) {
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
    root.appendChild(card);
    toolGroup = { card, header, countEl: count, body, name, n: 1 };
    return toolGroup;
  }

  return { ensure: ensureToolGroup, break: breakToolGroup };
}
