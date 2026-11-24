// Rebuilds a transcript from saved messages (history replay).
// Creates a fresh tool-group context and shared renderer for each replay so
// history does not reuse the previous live stream's groups. Reuses the same
// renderers the live path uses (activity, markdown, tools, reasoning).
//
// Tool rows are matched by explicit `tool_call_id` (Stage 01 contract).
// Legacy rows without IDs are matched positionally within their assistant
// step only. No FIFO across steps; no fabricated decisions.

import { formatToolArgs } from './tool-cards.js';
import { setThoughtTitle } from './reasoning.js';

// Reconstruct ordered reasoning/content parts from stored run descriptors.
// If `m.activity.parts` is a valid array whose aggregate lengths match the
// stored `reasoning_content` and `content` strings, render in descriptor
// order using code-point slicing. Otherwise fall back to the legacy
// reasoning-then-content render with `time`-derived duration.
function renderStepParts(m, { messagesView, activity, markdown }) {
  const parts = m.activity?.version === 1 ? m.activity.parts : null;

  // Validate all descriptors before any DOM mutation so a partially applied
  // part set cannot leave a duplicate fallback behind.
  let valid = false;
  let reasoningSource;
  let contentSource;
  if (Array.isArray(parts) && parts.length > 0) {
    reasoningSource = Array.from(m.reasoning_content || '');
    contentSource = Array.from(m.content || '');
    let reasoningSum = 0;
    let contentSum = 0;
    valid = true;
    for (const part of parts) {
      if (typeof part !== 'object' || part === null) { valid = false; break; }
      if (part.kind !== 'reasoning' && part.kind !== 'content') { valid = false; break; }
      if (!Number.isInteger(part.chars) || part.chars <= 0) { valid = false; break; }
      if (part.kind === 'reasoning') {
        reasoningSum += part.chars;
        if (part.duration_ms != null && (!Number.isInteger(part.duration_ms) || part.duration_ms < 0)) {
          valid = false; break;
        }
      } else {
        contentSum += part.chars;
      }
    }
    if (valid) {
      if (reasoningSum !== reasoningSource.length) valid = false;
      if (contentSum !== contentSource.length) valid = false;
    }
  }

  if (valid) {
    // Render in descriptor order. No arbitrary trim: whitespace-only segments
    // are kept when the live stream created them.
    const offset = { reasoning: 0, content: 0 };
    for (const part of parts) {
      const kind = part.kind;
      const source = kind === 'reasoning' ? reasoningSource : contentSource;
      const text = source.slice(offset[kind], offset[kind] + part.chars).join('');
      offset[kind] += part.chars;
      if (kind === 'reasoning') {
        const card = activity.addActivity('reasoning', 'Thought', text);
        if (part.duration_ms != null) {
          setThoughtTitle(card.header, part.duration_ms);
        }
      } else {
        const body = messagesView.addMessage('assistant', text);
        markdown.renderInto(body, text, { final: true });
      }
    }
    return;
  }

  // Legacy fallback: reasoning first, then content, with time-derived duration.
  const reasoningText = m.reasoning_content || '';
  const content = m.content || '';
  if (reasoningText.trim()) {
    const reasoningCard = activity.addActivity('reasoning', 'Thought', reasoningText);
    const time = m.time;
    if (time && time.created != null) {
      const end = time.streamed != null ? time.streamed : time.completed;
      if (end != null) {
        setThoughtTitle(reasoningCard.header, end - time.created);
      }
    }
  }
  if (content.trim()) {
    const body = messagesView.addMessage('assistant', content);
    markdown.renderInto(body, content, { final: true });
  }
}

export function parseToolArgs(json) {
  if (json == null) return {};
  if (typeof json === 'object') return json;
  try {
    const parsed = JSON.parse(json);
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed) ? parsed : json;
  } catch {
    return json;
  }
}

export function renderHistory(msgs, { messagesView, activity, markdown, tools, toolRenderer, reasoning, toolAliases }) {
  // Clear existing messages.
  messagesView.clear();
  if (!msgs.length) {
    messagesView.showEmpty();
    return;
  }

  const msgsLen = msgs.length;
  for (let i = 0; i < msgsLen; i++) {
    const m = msgs[i];
    const role = m.role;
    if (role === 'system') continue;

    if (role === 'user') {
      const content = m.content || '';
      if (content.trim()) {
        messagesView.addMessage('user', content);
      }
      // User boundary: break the tool renderer.
      toolRenderer.break();
      continue;
    }

    if (role === 'tool') {
      // Tool rows are consumed within their assistant step below;
      // any stray tool row outside an assistant batch is rendered as
      // unmatched neutral output.
      activity.addActivity('tool', 'Unmatched tool result', m.content || '', messagesView.el);
      continue;
    }

    if (role === 'assistant') {
      // Break at assistant boundary: each assistant message gets its own
      // tool groups.
      toolRenderer.break();

      const toolCalls = m.tool_calls || [];

      // Reconstruct ordered reasoning/content parts from stored run
      // descriptors, falling back to legacy reasoning-then-content.
      renderStepParts(m, { messagesView, activity, markdown });

      // Consume exactly the immediately following contiguous tool rows.
      const rows = [];
      while (i + 1 < msgsLen && msgs[i + 1].role === 'tool') {
        i += 1;
        rows.push(msgs[i]);
      }

      // Build a map of result rows by nonempty tool_call_id. Do not
      // overwrite duplicates; preserve duplicates as unmatched.
      const resultMap = new Map();
      for (const row of rows) {
        const tcid = row.tool_call_id;
        if (tcid && !resultMap.has(tcid)) {
          resultMap.set(tcid, row);
        }
      }

      const isNew = m.activity?.version === 1;

      for (let callIndex = 0; callIndex < toolCalls.length; callIndex++) {
        const tc = toolCalls[callIndex];
        const callState = isNew ? m.activity?.calls?.[tc.id] : null;

        // Suppress synthetic interrupted rows for undispatched calls.
        // Unexpected nonsynthetic rows must remain visible as unmatched
        // diagnostics (handled by the unconsumed-rows pass below).
        if (isNew && callState?.requested !== true) {
          continue;
        }

        const id = tc.id || `legacy:${i}:${callIndex}`;
        const name = tc.function?.name || 'tool';
        const args = parseToolArgs(tc.function?.arguments);
        const alias = toolAliases?.[name];

        toolRenderer.handle({
          type: 'tool_call',
          tool_call_id: id,
          name,
          arguments: args,
          alias,
        }, { historical: true });

        // Render persisted approval state (read-only; no POST).
        if (callState?.approval) {
          const approval = callState.approval;
          toolRenderer.handle({
            type: 'approval_required',
            approval_id: approval.approval_id,
            tool_call_id: id,
            name,
            arguments: args,
            status: approval.status,
            alias,
          }, { historical: true });
        }

        // Match result row by actual tc.id; consume once.
        const resultRow = resultMap.get(tc.id);
        if (resultRow) {
          resultMap.delete(tc.id);
          toolRenderer.handle({
            type: 'tool_result',
            tool_call_id: id,
            name,
            result: resultRow.content || '',
            ok: resultRow.activity?.ok ?? null,
            meta: resultRow.activity?.meta ?? {},
            alias,
          }, { historical: true });
        } else {
          // No matched result: neutral "unavailable" marker.
          toolRenderer.handle({
            type: 'tool_unavailable',
            tool_call_id: id,
          }, { historical: true });
        }
      }

      // Display all unconsumed rows (including duplicates and stray rows)
      // as unmatched neutral outputs.
      for (const row of resultMap.values()) {
        toolRenderer.handle({
          type: 'tool_result',
          tool_call_id: row.tool_call_id || 'unmatched',
          name: 'tool',
          result: row.content || '',
          ok: row.activity?.ok ?? null,
          meta: row.activity?.meta ?? {},
          alias: toolAliases?.['tool'],
        }, { historical: true });
      }

      // Render the step-cap terminal error (same card as live).
      if (typeof m.activity?.terminal_error === 'string' && m.activity.terminal_error) {
        activity.addActivity('error', 'Agent error', m.activity.terminal_error);
      }
    }
  }

  // Finish the renderer and force scroll.
  toolRenderer.finish();
  messagesView.scroll(true);
}
