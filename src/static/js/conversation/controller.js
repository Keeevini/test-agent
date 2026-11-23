// The conversation controller exclusively owns active-turn state: the busy
// flag, the AbortController, and per-turn assistant/reasoning text. It
// dispatches SSE events to the renderers it constructs from the injected
// messages view. Per-turn state is local to a turn, never a shared singleton
// reused by history replay.

import { createActivityView } from './activity.js';
import { createToolGroups, formatToolArgs, formatResult } from './tool-cards.js';
import { createReasoningRenderer, updateThinkingTitle } from './reasoning.js';
import { schedule as scheduleMarkdown, flushFinal, renderInto } from './markdown.js';
import { renderHistory } from './history.js';
import { addApproval } from './approval.js';

export function createConversationController({ api, sessions, messages, status }) {
  let busy = false;
  let abortController = null;
  const busyListeners = [];

  // Render helpers constructed once from the injected messages view.
  const activity = createActivityView({ messagesView: messages });
  const tools = createToolGroups({ root: messages.el });
  const reasoning = createReasoningRenderer();

  function setBusy(value) {
    busy = value;
    busyListeners.forEach(fn => fn(value));
  }

  function isBusy() {
    return busy;
  }

  function onBusyChange(fn) {
    busyListeners.push(fn);
  }

  // History responses are applied only if their requested session is still
  // current and their request is still the most recent (stale-response guard).
  let historyGeneration = 0;

  async function loadHistory(requestedId) {
    const generation = ++historyGeneration;
    if (!requestedId) {
      messages.showEmpty();
      return;
    }
    try {
      const stored = await api.getSessionMessages(requestedId);
      if (generation !== historyGeneration || requestedId !== sessions.currentId()) return;
      // A fresh tool-group context per replay; no shared live-stream groups.
      renderHistory(stored.messages || [], {
        messagesView: messages,
        activity,
        markdown: { renderInto },
        tools: createToolGroups({ root: messages.el }),
        reasoning,
      });
    } catch (error) {
      if (generation !== historyGeneration || requestedId !== sessions.currentId()) return;
      messages.showEmpty();
      console.warn('Failed to load session history', error);
    }
  }

  function invalidateHistory() {
    historyGeneration += 1;
  }

  function showEmpty() {
    messages.showEmpty();
  }

  function stop() {
    if (abortController) abortController.abort();
  }

  async function send(text) {
    if (busy || !text || !text.trim()) return;
    const trimmed = text.trim();
    // Set busy synchronously before the first await so a rapid second send
    // cannot start a second turn while ensureSession() is pending (fix (c)).
    setBusy(true);
    invalidateHistory();
    const sessionJustCreated = !sessions.isKnown();

    // Per-turn state is local to this turn.
    let assistantText = '';
    let assistantBody = null;
    let reasoningCard = null;
    let reasoningStartTime = null;
    let reasoningText = '';
    tools.break();

    abortController = new AbortController();
    const signal = abortController.signal;
    messages.addMessage('user', trimmed);

    function dispatch(event) {
      if (event.type === 'reasoning_delta') {
        if (!reasoningCard) {
          reasoningStartTime = Date.now();
          reasoningText = '';
          reasoningCard = activity.addActivity('reasoning', 'Thinking', '');
        }
        reasoningText += event.text;
        updateThinkingTitle(reasoningCard.header, reasoningText);
        reasoning.render(reasoningCard.content, reasoningText);
      } else if (event.type === 'content_delta') {
        if (!assistantBody) {
          assistantBody = messages.addMessage('assistant', '');
        }
        if (reasoningCard && reasoningStartTime) {
          reasoning.finalize(reasoningCard, reasoningStartTime);
          reasoningCard = null;
          reasoningStartTime = null;
        }
        assistantText += event.text;
        messages.addCursor(assistantBody);
        scheduleMarkdown(assistantBody, assistantText);
        messages.scroll();
      } else if (event.type === 'context') {
        status.updateContext(event);
      } else if (event.type === 'tool_call') {
        if (reasoningCard && reasoningStartTime) {
          reasoning.finalize(reasoningCard, reasoningStartTime);
          reasoningCard = null;
          reasoningStartTime = null;
        }
        // A tool segment ends the current assistant message, so the next
        // content_delta starts a fresh message positioned after the tools.
        assistantBody = null;
        assistantText = '';
        const group = tools.ensure(event.name);
        activity.addActivity('tool', `Tool request · ${event.name}`, formatToolArgs(event.name, event.arguments), group.body);
        group.countEl.textContent = `×${group.n}`;
        group.n += 1;
      } else if (event.type === 'approval_required') {
        if (reasoningCard && reasoningStartTime) {
          reasoning.finalize(reasoningCard, reasoningStartTime);
          reasoningCard = null;
          reasoningStartTime = null;
        }
        assistantBody = null;
        assistantText = '';
        tools.break();
        addApproval(event, { messagesView: messages, formatToolArgs });
      } else if (event.type === 'tool_result') {
        if (reasoningCard && reasoningStartTime) {
          reasoning.finalize(reasoningCard, reasoningStartTime);
          reasoningCard = null;
          reasoningStartTime = null;
        }
        assistantBody = null;
        assistantText = '';
        const group = tools.ensure(event.name);
        activity.addActivity(event.ok ? 'result' : 'error', `${event.ok ? 'Tool result' : 'Tool failed'} · ${event.name}`, formatResult(event.name, event.result || '', event.meta, event.ok), group.body);
      } else if (event.type === 'error') {
        if (reasoningCard && reasoningStartTime) {
          reasoning.finalize(reasoningCard, reasoningStartTime);
          reasoningCard = null;
          reasoningStartTime = null;
        }
        activity.addActivity('error', 'Agent error', event.message || 'Unknown error');
        if (!assistantText.trim()) {
          assistantText = `Error: ${event.message}`;
        }
      }
    }

    try {
      await sessions.ensureSession();
      if (sessionJustCreated) sessions.refresh();

      for await (const event of api.streamChat({ session_id: sessions.currentId(), message: trimmed }, signal)) {
        dispatch(event);
      }

      messages.removeCursor();
      if (assistantBody) {
        if (!assistantText.trim()) {
          assistantBody.textContent = '(No final text response.)';
        } else {
          flushFinal(assistantBody, assistantText);
        }
      }
    } catch (error) {
      messages.removeCursor();
      if (signal.aborted) {
        if (assistantBody) {
          if (!assistantText.trim()) {
            assistantBody.textContent = '(Stopped.)';
          } else {
            flushFinal(assistantBody, assistantText);
          }
        }
        activity.addActivity('error', 'Request stopped', 'Cancelled by user');
      } else {
        if (assistantBody) {
          assistantBody.textContent = `Request failed: ${error.message}`;
        }
        activity.addActivity('error', 'Connection error', error.stack || String(error));
      }
    } finally {
      if (reasoningCard && reasoningStartTime) {
        reasoning.finalize(reasoningCard, reasoningStartTime);
      }
      setBusy(false);
      abortController = null;
      // Refresh the sidebar so a brand-new session (created server-side by
      // the first message) appears in the list, and so the session's title /
      // token count stay current after each turn.
      sessions.refresh();
    }
  }

  return {
    send,
    stop,
    isBusy,
    loadHistory,
    showEmpty,
    onBusyChange,
  };
}
