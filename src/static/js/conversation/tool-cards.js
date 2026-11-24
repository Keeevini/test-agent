// Tool call formatting and grouping.
//
// `createToolGroups` returns an independent group context so live turns and
// history replay never share group state (fix: per-rendering-context groups).
// The formatters are pure and exported independently.
//
// `createToolActivityRenderer` is the shared renderer for both live and
// history paths: it owns per-step invocation/approval maps and dispatches
// tool_call / approval_required / approval_decision / tool_result /
// tool_unavailable events into the correct group by ID.

import { addApproval } from './approval.js';

export function toolLabel(name, alias) {
  return typeof alias === 'string' && alias.trim() ? alias.trim() : (name || 'tool');
}

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
  const raw = typeof result === 'string' ? result : String(result ?? '');
  if (ok !== true) return raw;
  const m = meta || {};
  const count = n => Number.isFinite(n) && n >= 0;
  let summary = '';
  if (name === 'read_file' && count(m.lines) && count(m.bytes)) {
    summary = `${m.lines} line${m.lines === 1 ? '' : 's'} · ${formatBytes(m.bytes)}`;
  } else if (name === 'search_text' && count(m.matches) && count(m.files)) {
    summary = `${m.matches} match${m.matches === 1 ? '' : 'es'} in ${m.files} file${m.files === 1 ? '' : 's'}`;
  } else if (name === 'list_directory' && count(m.entries)) {
    summary = `${m.entries} ${m.entries === 1 ? 'entry' : 'entries'}`;
  }
  return summary ? `${summary}\n\n${raw === '' ? '(Empty output)' : raw}` : (raw === '' ? '(Empty output)' : raw);
}

// Returns an independent tool-group context: { ensure(name), break(), countInvocation(group, id) }.
// `root` is the container the group cards are appended to.
// Counting is ID-based: one count per distinct attempted invocation ID.
export function createToolGroups({ root }) {
  let toolGroup = null;

  function breakToolGroup() {
    toolGroup = null;
  }

  function countInvocation(group, id) {
    if (!group.ids.has(id)) {
      group.ids.add(id);
      group.n = group.ids.size;
      group.countEl.textContent = `×${group.n}`;
    }
  }

  function ensureToolGroup(name, alias) {
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
    label.textContent = toolLabel(name, alias);
    header.appendChild(label);
    const count = document.createElement('span');
    count.className = 'group-count';
    count.textContent = '×0';
    header.appendChild(count);
    const body = document.createElement('div');
    body.className = 'group-body';
    card.append(header, body);
    header.addEventListener('click', () => {
      // Guard: do not collapse while pending approval controls are present.
      if (card.querySelector('.approval-controls button')) return;
      card.classList.toggle('collapsed');
    });
    root.appendChild(card);
    toolGroup = { card, header, countEl: count, body, name, n: 0, ids: new Set() };
    return toolGroup;
  }

  return { ensure: ensureToolGroup, break: breakToolGroup, countInvocation };
}

// Shared tool activity renderer. One instance per rendering context (live
// turn or history replay). Owns per-step invocation and approval maps;
// do not keep global state.
export function createToolActivityRenderer({ messagesView, activity, tools }) {
  const invocations = new Map(); // tool_call_id -> { group, name, hasResult }
  const approvals = new Map();   // approval_id -> handle

  function handle(event, { historical = false } = {}) {
    if (event.type === 'tool_call') {
      const id = event.tool_call_id;
      if (invocations.has(id)) return; // ignore duplicate ID
      const group = tools.ensure(event.name, event.alias);
      const entry = { group, name: event.name, alias: event.alias, hasResult: false };
      invocations.set(id, entry);
      tools.countInvocation(group, id);
      const label = toolLabel(event.name, event.alias);
      activity.addActivity('tool', `Tool request · ${label}`, formatToolArgs(event.name, event.arguments), group.body);
    } else if (event.type === 'approval_required') {
      const aid = event.approval_id;
      if (approvals.has(aid)) return; // ignore duplicate approval ID
      const inv = invocations.get(event.tool_call_id);
      const parent = inv ? inv.group.body : messagesView.el;
      const displayName = toolLabel(event.name, event.alias);
      const h = addApproval(event, { messagesView, formatToolArgs, parent, historical, displayName });
      approvals.set(aid, h);
      // Open the group so pending controls are visible.
      if (inv) inv.group.card.classList.remove('collapsed');
    } else if (event.type === 'approval_decision') {
      const h = approvals.get(event.approval_id);
      if (h) h.setDecision(event.status);
    } else if (event.type === 'tool_result') {
      const id = event.tool_call_id;
      const inv = invocations.get(id);
      if (!inv) {
        // No matched request: render as unmatched neutral output at root.
        activity.addActivity('tool', 'Unmatched tool result', event.result || '', messagesView.el);
        return;
      }
      if (inv.hasResult) return; // ignore duplicate result
      inv.hasResult = true;
      const name = event.name || inv.name;
      const alias = event.alias || inv.alias;
      const label = toolLabel(name, alias);
      const kind = event.ok === true ? 'result' : event.ok === false ? 'error' : 'tool';
      const title = event.ok === true ? 'Tool result' : event.ok === false ? 'Tool failed' : 'Tool output (status unknown)';
      const body = event.ok == null
        ? (event.result || '')
        : formatResult(name, event.result || '', event.meta, event.ok);
      activity.addActivity(kind, `${title} · ${label}`, body, inv.group.body);
    } else if (event.type === 'tool_unavailable') {
      const id = event.tool_call_id;
      const inv = invocations.get(id);
      if (!inv || inv.hasResult) return;
      inv.hasResult = true;
      activity.addActivity('tool', 'Tool output unavailable', '', inv.group.body);
    }
  }

  function break_() {
    // Finish outstanding live controls (neutral unavailable only if
    // still unresolved), then clear maps and break the group.
    approvals.forEach(h => h.markUnavailable());
    invocations.clear();
    approvals.clear();
    tools.break();
  }

  function finish() {
    // Make any unresolved live cards unavailable, then clear state.
    approvals.forEach(h => h.markUnavailable());
    invocations.clear();
    approvals.clear();
  }

  return { handle, break: break_, finish };
}
