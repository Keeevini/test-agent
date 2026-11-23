// Rebuilds a transcript from saved messages (history replay).
// Creates a fresh tool-group context for each replay so history does not
// reuse the previous live stream's group. Reuses the same renderers the live
// path uses (activity, markdown, tools, reasoning).

import { formatToolArgs } from './tool-cards.js';
import { setThoughtTitle } from './reasoning.js';

export function parseToolArgs(json) {
  if (!json) return {};
  if (typeof json === 'object') return json;
  try {
    const parsed = JSON.parse(json);
    return typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

export function renderHistory(msgs, { messagesView, activity, markdown, tools }) {
  // Clear existing messages.
  messagesView.clear();
  if (!msgs.length) {
    messagesView.showEmpty();
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
        messagesView.addMessage('user', content);
      }
      continue;
    }

    if (role === 'tool') {
      // Attach the result to the most recent pending tool call's group.
      const result = m.content || '';
      const entry = pendingToolResults.shift();
      if (entry) {
        activity.addActivity('result', `Tool result · ${entry.name}`, result, entry.group.body);
        entry.group.countEl.textContent = `×${entry.group.n}`;
        entry.group.n += 1;
      }
      continue;
    }

    if (role === 'assistant') {
      const content = m.content || '';
      const reasoningText = m.reasoning_content || '';
      const toolCalls = m.tool_calls || [];

      // Start a fresh tool group for this message.
      tools.break();
      pendingToolResults = [];

      // Reasoning card. The duration is computed from the stored `time`
      // section (OpenCode's `created`/`streamed`/`completed`). Prefer
      // `streamed - created` (pure generation time); fall back to
      // `completed - created` for older messages without `streamed`.
      // Messages with no timing at all show the bare "Thought" label.
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

      // Tool calls.
      for (const tc of toolCalls) {
        const name = tc.function?.name || 'tool';
        const args = parseToolArgs(tc.function?.arguments);
        const group = tools.ensure(name);
        activity.addActivity('tool', `Tool request · ${name}`, formatToolArgs(name, args), group.body);
        group.countEl.textContent = `×${group.n}`;
        group.n += 1;
        pendingToolResults.push({ group, name });
      }

      // Final content.
      if (content.trim()) {
        const body = messagesView.addMessage('assistant', content);
        markdown.renderInto(body, content, { final: true });
      }
    }
  }
  messagesView.scroll(true);
}
