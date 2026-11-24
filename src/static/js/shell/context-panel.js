// Pure renderer for the context-window detailed panel: the summary, the
// capacity-scaled stacked bar, the loaded-file breakdown, the system prompt,
// and the stable raw-messages anchor. It receives only the accepted Stage 04
// snapshot and rebuilds the panel body's children. It never fetches, reads the
// current session id, mutates the ring, or touches global state.
//
// Security: system_prompt.text and loaded_files[].path are untrusted local
// data. They are rendered with textContent only and are never interpolated
// into markup.
//
// Text attribution is independent of provider-based active occupancy.

// Immutable display order. The bar and the legend share this order.
const CATEGORIES = [
  ['previous_compaction', 'Previous compaction'],
  ['user_input', 'User input'],
  ['loaded_files', 'Loaded files'],
  ['model_reasoning', 'Model reasoning'],
  ['tool_usage', 'Tool usage'],
  ['other', 'Other (system / assistant)'],
  ['available', 'Available'],
];

const SOURCE_LABEL = { tokenized: 'Tokenized text', estimated: 'Estimated', mixed: 'Mixed', none: 'No text' };
function categorySource(key, data) {
  if (key === 'previous_compaction') return 'Not implemented';
  if (key === 'available') return 'Configured limit';
  return SOURCE_LABEL[data.breakdown_sources?.[key]] || 'Estimated';
}

function num(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function fmt(value) {
  const n = num(value);
  return n === null ? '0' : new Intl.NumberFormat().format(n);
}

export function renderContextPanel(container, snapshot) {
  container.replaceChildren();
  const data = snapshot && typeof snapshot === 'object' ? snapshot : {};
  appendSummary(container, data);
  appendSystemPrompt(container, data);
  appendRawAnchor(container);
}

function appendSummary(container, data) {
  const section = document.createElement('section');
  section.className = 'context-detail-summary';

  const heading = document.createElement('h3');
  heading.className = 'context-detail-heading';
  heading.textContent = 'Summary';
  section.appendChild(heading);

  const used = num(data.used_tokens);
  const limit = num(data.context_limit_tokens);
  const breakdown = data.breakdown && typeof data.breakdown === 'object' ? data.breakdown : {};

  const counts = document.createElement('div');
  counts.className = 'context-detail-counts';
  if (used === null || limit === null) {
    // Required fields absent: show an unavailable state, not a fabricated 0.
    counts.textContent = 'Usage unavailable';
    section.appendChild(counts);
  } else {
    counts.appendChild(document.createElement('span')).textContent = `${fmt(used)} / ${fmt(limit)} tokens`;
    const pct = document.createElement('span');
    pct.className = 'context-detail-pct';
    const pctValue = limit > 0 ? (100 * used) / limit : null;
    pct.textContent = pctValue === null ? '' : `${pctValue.toFixed(1)}%`;
    counts.appendChild(pct);
    if (limit > 0 && used > limit) {
      const over = document.createElement('span');
      over.className = 'context-detail-over';
      over.textContent = 'Over configured limit';
      counts.appendChild(over);
    }
    section.appendChild(counts);
  }

  appendLifetime(section, data);
  appendSessionInfo(section, data);
  appendSourceLine(section, data);
  appendBar(section, data, breakdown);
  appendLegend(section, data, breakdown);
  container.appendChild(section);
}

function appendLifetime(section, data) {
  const lifetime = data.session_lifetime;
  const value = num(lifetime?.tokens_total);
  const source = {provider: 'provider', estimated: 'includes estimates', none: 'no calls'}[lifetime?.source];
  section.appendChild(makeSpan('context-detail-lifetime',
    `Lifetime session tokens: ${value === null ? '—' : fmt(value)}${source ? ` (${source})` : ''}`));
}

function formatTime(value) {
  if (typeof value !== 'string' || !value) return '—';
  // SQLite CURRENT_TIMESTAMP is UTC, not browser-local time.
  const iso = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value) ? value.replace(' ', 'T') + 'Z' : value;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}

function appendSessionInfo(section, data) {
  const info = document.createElement('dl');
  info.className = 'context-detail-session-info';
  const count = key => num(data[key]) === null ? '—' : fmt(data[key]);
  for (const [label, value] of [
    ['Session name', typeof data.session_title === 'string' ? data.session_title : '—'],
    ['Created', formatTime(data.created_at)], ['Last activity', formatTime(data.updated_at)],
    ['Messages (all roles)', count('message_count')], ['Assistant messages', count('assistant_message_count')],
    ['User messages', count('user_message_count')],
  ]) {
    const term = document.createElement('dt'); term.textContent = label;
    const desc = document.createElement('dd'); desc.textContent = value;
    info.append(term, desc);
  }
  section.appendChild(info);
}

function appendSourceLine(section, data) {
  const line = document.createElement('div');
  line.className = 'context-detail-source';
  const usedSource = typeof data.used_source === 'string' ? data.used_source : '';
  let usedLabel;
  if (usedSource === 'provider_based') usedLabel = 'Provider-based total';
  else if (usedSource === 'mixed_estimate') usedLabel = 'Partly estimated total';
  else if (usedSource === 'estimated') usedLabel = 'Estimated total';
  else usedLabel = 'Unverified data';
  const breakdownLabel = data.breakdown_source === 'tokenizer_assisted'
    ? 'Tokenizer-assisted breakdown (approximate attribution)' : 'Estimated breakdown';
  line.textContent = `${usedLabel} · ${breakdownLabel}`;
  section.appendChild(line);
  const diff = num(data.attribution_difference);
  if (diff !== null && diff !== 0) {
    const note = diff > 0 ? `${fmt(diff)} tokens not attributed to text` : `${fmt(-diff)} more attributed text tokens than active usage; bar proportions adjusted only`;
    section.appendChild(makeSpan('context-detail-difference', note));
  }
}

function appendBar(container, data, breakdown) {
  const bar = document.createElement('div');
  bar.className = 'context-detail-bar';
  bar.setAttribute('role', 'img');
  bar.setAttribute('aria-label', barLabel(data, breakdown));
  const used = num(data.used_tokens) ?? 0;
  const attributed = num(data.attributed_tokens) ?? used;
  const scale = attributed > used && attributed > 0 ? used / attributed : 1;
  const entries = [...CATEGORIES.slice(0, -1), ['unattributed'], CATEGORIES.at(-1)];
  for (const [key] of entries) {
    const raw = key === 'unattributed' ? Math.max(0, used - attributed) : breakdown[key];
    const tokens = typeof raw === 'number' && Number.isFinite(raw) ? raw : 0;
    const displayTokens = key === 'available' || key === 'unattributed' ? tokens : tokens * scale;
    const width =
      typeof data.context_limit_tokens === 'number' && data.context_limit_tokens > 0
        ? 100 * displayTokens / data.context_limit_tokens
        : 0;
    const seg = document.createElement('div');
    seg.className = `context-detail-segment context-detail-segment--${key}`;
    seg.style.width = `${Math.max(0, width)}%`;
    bar.appendChild(seg);
  }
  container.appendChild(bar);
}

function barLabel(data, breakdown) {
  const used = num(data.used_tokens);
  const available = num(breakdown.available);
  const base = 'Context usage breakdown, estimated';
  if (used === null) return base;
  return `${base}; ${used} tokens used, approximately ${available === null ? 'unknown' : available} tokens available`;
}

function appendLegend(container, data, breakdown) {
  const legend = document.createElement('div');
  legend.className = 'context-detail-legend';
  for (const [key, label] of CATEGORIES) {
    const raw = breakdown[key];
    const tokens = typeof raw === 'number' && Number.isFinite(raw) ? raw : 0;
    const isFiles = key === 'loaded_files';
    const isTools = key === 'tool_usage';
    // The loaded-files row is the <summary> of a native <details>, so it is a
    // keyboard-accessible accordion; the other rows are plain divs.
    const row = document.createElement(isFiles || isTools ? 'summary' : 'div');
    row.className = 'context-detail-legend-row';
    row.appendChild(makeSwatch(key));
    row.appendChild(makeSpan('context-detail-legend-label', label));
    row.appendChild(makeSpan('context-detail-legend-count', fmt(tokens)));
    row.appendChild(makeSpan('context-detail-legend-source', categorySource(key, data)));
    if (isFiles || isTools) {
      const details = document.createElement('details');
      details.className = isFiles ? 'context-detail-files' : 'context-detail-files context-detail-tools';
      details.appendChild(row);
      details.appendChild(isFiles ? makeFileList(data) : makeToolList(data));
      legend.appendChild(details);
    } else {
      legend.appendChild(row);
    }
  }
  container.appendChild(legend);
}

function makeSwatch(key) {
  const swatch = document.createElement('span');
  swatch.className = `context-detail-swatch context-detail-swatch--${key}`;
  return swatch;
}

function makeSpan(className, text) {
  const span = document.createElement('span');
  span.className = className;
  span.textContent = text;
  return span;
}

function makeFileList(data) {
  const list = document.createElement('div');
  list.className = 'context-detail-files-list';
  const files = Array.isArray(data.loaded_files) ? data.loaded_files : [];
  if (files.length === 0) {
    list.appendChild(makeSpan('context-detail-files-empty', 'No attributed file reads'));
    return list;
  }
  for (const file of files) {
    const row = document.createElement('div');
    row.className = 'context-detail-file-row';
    const path = document.createElement('span');
    path.className = 'context-detail-files-path';
    path.textContent = file && typeof file.path === 'string' ? file.path : '';
    row.appendChild(path);
    const tokens = document.createElement('span');
    tokens.className = 'context-detail-file-tokens';
    tokens.textContent = fmt(file && num(file.tokens));
    row.appendChild(tokens);
    tokens.title = SOURCE_LABEL[file?.source] || 'Estimated';
    if (file && typeof file.occurrences === 'number' && file.occurrences > 1) {
      const occ = document.createElement('span');
      occ.className = 'context-detail-file-occurrences';
      occ.textContent = `\u00d7${file.occurrences}`;
      row.appendChild(occ);
    }
    list.appendChild(row);
  }
  return list;
}

function makeToolList(data) {
  const list = document.createElement('div');
  list.className = 'context-detail-files-list';
  const tools = Array.isArray(data.tool_usage_details) ? data.tool_usage_details : [];
  if (!tools.length) list.appendChild(makeSpan('context-detail-files-empty', 'No attributed tools'));
  for (const tool of tools) {
    const row = document.createElement('div'); row.className = 'context-detail-tool-row';
    row.appendChild(makeSpan('context-detail-files-path', tool.name || '(unmatched tool)'));
    row.appendChild(makeSpan('context-detail-file-tokens', fmt(tool.tokens)));
    row.appendChild(makeSpan('context-detail-tool-components',
      `${fmt(tool.calls)} calls · calls ${fmt(tool.call_tokens)} · results ${fmt(tool.result_tokens)} · definition ${fmt(tool.definition_tokens)} · ${SOURCE_LABEL[tool.source] || 'Estimated'}`));
    list.appendChild(row);
  }
  return list;
}

function appendSystemPrompt(container, data) {
  const section = document.createElement('section');
  section.className = 'context-detail-system';

  const sp = data.system_prompt && typeof data.system_prompt === 'object' ? data.system_prompt : {};
  const text = typeof sp.text === 'string' ? sp.text : '';
  const source = typeof sp.source === 'string' ? sp.source : '';

  // A native <details> keeps a huge prompt out of the initial layout until
  // expanded, without truncating the stored source.
  const details = document.createElement('details');
  details.className = 'context-detail-system-details';
  const summary = document.createElement('summary');
  summary.className = 'context-detail-system-summary';
  summary.appendChild(makeSpan('context-detail-heading', 'System prompt'));
  if (source === 'persisted') {
    summary.appendChild(makeSpan('context-detail-system-source', 'Stored in this conversation'));
  } else if (source === 'configured_not_yet_sent') {
    summary.appendChild(makeSpan('context-detail-system-source', 'Configured — not yet sent to model'));
  }
  details.appendChild(summary);

  if (text) {
    const promptEl = document.createElement('pre');
    promptEl.className = 'context-detail-system-text';
    promptEl.textContent = text;
    details.appendChild(promptEl);
  } else {
    details.appendChild(makeSpan('context-detail-system-empty', 'No system prompt available'));
  }
  section.appendChild(details);
  container.appendChild(section);
}

function appendRawAnchor(container) {
  const section = document.createElement('section');
  section.id = 'contextRawSection';
  section.className = 'context-detail-raw';
  section.appendChild(makeSpan('context-detail-heading', 'Raw messages'));
  section.appendChild(makeSpan('context-detail-raw-placeholder', 'Available in the next stage'));
  container.appendChild(section);
}
