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
// Attribution is always estimated: providers report aggregate usage, not
// per-file/per-tool splits, so every token-attribution row is labeled
// "Estimated" even when the aggregate total is provider-based.

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

// Source label per category. Token-attribution rows are always Estimated; the
// available row is display-only capacity, not counted model usage.
const CATEGORY_SOURCE = {
  previous_compaction: 'Not implemented',
  user_input: 'Estimated',
  loaded_files: 'Estimated',
  model_reasoning: 'Estimated',
  tool_usage: 'Estimated',
  other: 'Estimated',
  available: 'Configured limit',
};

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

  appendSourceLine(section, data);
  appendBar(section, data, breakdown);
  appendLegend(section, data, breakdown);
  container.appendChild(section);
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
  // The breakdown is always an estimate, even when the aggregate is
  // provider-based; never assert per-category exactness.
  line.textContent = `${usedLabel} · Estimated breakdown`;
  section.appendChild(line);
}

function appendBar(container, data, breakdown) {
  const bar = document.createElement('div');
  bar.className = 'context-detail-bar';
  bar.setAttribute('role', 'img');
  bar.setAttribute('aria-label', barLabel(data, breakdown));
  for (const [key] of CATEGORIES) {
    const raw = breakdown[key];
    const tokens = typeof raw === 'number' && Number.isFinite(raw) ? raw : 0;
    const width =
      typeof data.context_limit_tokens === 'number' && data.context_limit_tokens > 0
        ? 100 * tokens / data.context_limit_tokens
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
    // The loaded-files row is the <summary> of a native <details>, so it is a
    // keyboard-accessible accordion; the other rows are plain divs.
    const row = document.createElement(isFiles ? 'summary' : 'div');
    row.className = 'context-detail-legend-row';
    row.appendChild(makeSwatch(key));
    row.appendChild(makeSpan('context-detail-legend-label', label));
    row.appendChild(makeSpan('context-detail-legend-count', fmt(tokens)));
    row.appendChild(makeSpan('context-detail-legend-source', CATEGORY_SOURCE[key]));
    if (isFiles) {
      const details = document.createElement('details');
      details.className = 'context-detail-files';
      details.appendChild(row);
      details.appendChild(makeFileList(data));
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
