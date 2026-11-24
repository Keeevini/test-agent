// The conversation controller exclusively owns active-turn state: the busy
// flag, the AbortController, and per-turn assistant/reasoning text. It
// dispatches SSE events to the renderers it constructs from the injected
// messages view. Per-turn state is local to a turn, never a shared singleton
// reused by history replay.

import { createActivityView } from './activity.js';
import { createToolGroups, createToolActivityRenderer } from './tool-cards.js';
import { createReasoningRenderer, updateThinkingTitle, setThoughtTitle } from './reasoning.js';
import { schedule as scheduleMarkdown, flushFinal, renderInto, cancel as cancelMarkdown } from './markdown.js';
import { renderHistory } from './history.js';

export function createConversationController({ api, sessions, messages, status }) {
  let busy = false;
  let abortController = null;
  const busyListeners = [];

  // Render helpers constructed once from the injected messages view.
  const activity = createActivityView({ messagesView: messages });
  const reasoning = createReasoningRenderer();

  function setBusy(value) {
    busy = value;
    busyListeners.forEach(fn => fn(value));
  }

  function isBusy() {
    return busy;
  }

  // A turn may start only when nothing is busy and no session transition is
  // in flight. Kept separate from isBusy() so the Stop path does not fire
  // during a navigation transition (there is no AbortController to stop).
  function canSend() {
    return !busy && !sessions.isTransitioning();
  }

  function onBusyChange(fn) {
    busyListeners.push(fn);
  }

  // History responses are applied only if their requested session is still
  // current and their request is still the most recent (stale-response guard).
  let historyGeneration = 0;

  // Cancel pending Markdown/reasoning frames, then clear DOM/cursor/scroll
  // state and reveal the empty state. Called on every navigation so a stale
  // transcript never appears under a newly selected session.
  function clearTranscript() {
    messages.el.querySelectorAll('.message-body').forEach(cancelMarkdown);
    messages.el.querySelectorAll('.activity-card.reasoning .activity-card-body')
      .forEach(reasoning.cancel);
    messages.clear();
    messages.showEmpty();
  }

  function invalidateHistory() {
    historyGeneration += 1;
  }

  function showEmpty() {
    invalidateHistory();
    clearTranscript();
  }

  async function loadHistory(requestedId) {
    const generation = ++historyGeneration;
    // Clear immediately: no transcript from A must be visible under B.
    clearTranscript();
    status.resetContext();
    if (!requestedId) return;

    try {
      const stored = await api.getSessionMessages(requestedId);
      if (generation !== historyGeneration || requestedId !== sessions.currentId()) return;
      // A fresh tool-group context and shared renderer per replay; no shared
      // live-stream groups.
      const tools = createToolGroups({ root: messages.el });
      const toolRenderer = createToolActivityRenderer({ messagesView: messages, activity, tools });
      renderHistory(stored.messages || [], {
        messagesView: messages,
        activity,
        markdown: { renderInto },
        tools,
        toolRenderer,
        reasoning,
        toolAliases: stored.tool_aliases || {},
      });
    } catch (error) {
      if (generation !== historyGeneration || requestedId !== sessions.currentId()) return;
      messages.showEmpty();
      console.warn('Failed to load session history', error);
    }
  }

  function stop() {
    if (abortController) abortController.abort();
  }

  async function send(text) {
    if (busy || !text || !text.trim() || sessions.isTransitioning()) return;
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

    // Per-turn tool groups and shared renderer. The renderer owns invocation
    // and approval maps; finish() in finally cleans up.
    const tools = createToolGroups({ root: messages.el });
    const toolRenderer = createToolActivityRenderer({ messagesView: messages, activity, tools });

    // Local finalizers. endReasoning finalizes the current reasoning card with
    // the authoritative server duration when supplied (model_part_end), falling
    // back to the local start for cancellation/older events. endAssistant
    // removes the cursor and flushes the active assistant body. Both are
    // idempotent so the finally/catch paths can call them safely.
    function endReasoning(durationMs) {
      if (reasoningCard && reasoningStartTime) {
        if (durationMs != null) {
          setThoughtTitle(reasoningCard.header, durationMs);
        } else {
          reasoning.finalize(reasoningCard, reasoningStartTime);
        }
      }
      reasoningCard = null;
      reasoningStartTime = null;
      reasoningText = '';
    }

    function endAssistant() {
      if (assistantBody) {
        messages.removeCursor();
        if (!assistantText.trim()) {
          assistantBody.textContent = '(No final text response.)';
        } else {
          flushFinal(assistantBody, assistantText);
        }
      }
      assistantBody = null;
      assistantText = '';
    }

    abortController = new AbortController();
    const signal = abortController.signal;
    messages.addMessage('user', trimmed);

    function dispatch(event) {
      if (event.type === 'model_part_end') {
        // Authoritative segment end. Reasoning carries the server duration;
        // content ends the active assistant body. The explicit end event is
        // the normal path; the delta handlers below are fallbacks only.
        if (event.kind === 'reasoning') {
          endReasoning(event.duration_ms);
        } else if (event.kind === 'content') {
          endAssistant();
        }
      } else if (event.type === 'reasoning_delta') {
        // A text segment may still be active from a fallback path; end it so
        // the reasoning card is not interleaved with live Markdown.
        endAssistant();
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
        // Fallback: an explicit model_part_end normally ends the reasoning run
        // before the first content delta.
        if (reasoningCard && reasoningStartTime) {
          endReasoning();
        }
        assistantText += event.text;
        messages.addCursor(assistantBody);
        scheduleMarkdown(assistantBody, assistantText);
        messages.scroll();
      } else if (event.type === 'context') {
        status.updateContext(event);
      } else if (event.type === 'status') {
        // Model-step boundary: end any prior text/reasoning, then break the
        // shared tool renderer so each step gets its own groups. Server emits
        // model deltas before tools, so the step boundary suffices.
        endReasoning();
        endAssistant();
        toolRenderer.break();
      } else if (event.type === 'tool_call' || event.type === 'approval_required' || event.type === 'approval_decision' || event.type === 'tool_result') {
        // Non-decision tool events end the current assistant message.
        if (event.type !== 'approval_decision') {
          endReasoning();
          endAssistant();
        }
        toolRenderer.handle(event);
      } else if (event.type === 'error') {
        endReasoning();
        endAssistant();
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

      // Normal EOF: the last model_part_end normally already ended the final
      // segment; the finalizers are idempotent no-ops in that case and provide
      // the fallback for streams that ended without an explicit end event.
      endReasoning();
      endAssistant();
    } catch (error) {
      // Capture the active body/text before resetting so the stopped /
      // connection labels are preserved. endReasoning cleans up the reasoning
      // card; the assistant body is handled inline to keep its labels.
      endReasoning();
      if (signal.aborted) {
        if (assistantBody) {
          if (!assistantText.trim()) {
            assistantBody.textContent = '(Stopped.)';
          } else {
            flushFinal(assistantBody, assistantText);
          }
          messages.removeCursor();
        }
        assistantBody = null;
        assistantText = '';
        activity.addActivity('error', 'Request stopped', 'Cancelled by user');
      } else {
        if (assistantBody) {
          assistantBody.textContent = `Request failed: ${error.message}`;
          messages.removeCursor();
        }
        assistantBody = null;
        assistantText = '';
        activity.addActivity('error', 'Connection error', error.stack || String(error));
      }
    } finally {
      // Idempotent final cleanup for any segment still active (e.g. a stream
      // that never emitted model_part_end). No-ops when no card/body is active.
      endReasoning();
      endAssistant();
      // Finish the tool renderer: unresolved live cards become unavailable,
      // maps are cleared.
      toolRenderer.finish();
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
    canSend,
    loadHistory,
    showEmpty,
    onBusyChange,
  };
}
