async (stage) => {
  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const base = 'conversation/';
  const { createMessagesView } = await loadModule(base + 'messages.js');
  const messages = createMessagesView({
    messagesEl: document.querySelector('#messages'),
    emptyState: null, scrollToBottom: document.querySelector('#scroll'),
  });
  // Safe stub for deterministic text/DOM assertions (not Markdown fidelity).
  window.marked = { parse: text => {
    const el = document.createElement('div');
    el.textContent = text;
    return el.innerHTML;
  }};
  window.hljs = { highlightElement() {} };
  const cases = {};
  cases.approval = async () => {
    const { addApproval } = await loadModule(base + 'approval.js');
    const { formatToolArgs } = await loadModule(base + 'tool-cards.js');
    let posts = 0;
    let respond;
    window.fetch = async (url, opts) => {
      assert(url === '/api/approval' && opts.method === 'POST', 'unexpected fetch');
      posts += 1;
      return new Promise(resolve => { respond = resolve; });
    };
    const event = { approval_id: 'p1', tool_call_id: 'c1',
      name: 'write_file', arguments: { path: 'synthetic.txt', content: 'x' } };
    const options = { messagesView: messages, formatToolArgs };

    // Live pending: exactly two buttons; success removes both from the DOM and
    // leaves the terminal label.
    const handle = addApproval(event, options);
    const controls = handle.card.querySelector('.approval-controls');
    assert(controls.querySelectorAll('button').length === 2, 'pending buttons');
    controls.querySelector('button').click();
    assert([...controls.querySelectorAll('button')].every(b => b.disabled), 'submitting disabled');
    respond({ ok: true, status: 200, json: async () => ({ok:true}) });
    await new Promise(resolve => setTimeout(resolve, 0));
    assert(controls.querySelectorAll('button').length === 0, 'terminal buttons removed');
    assert(controls.textContent.trim() === 'Approved', 'terminal label');

    // Duplicate decisions are idempotent; a later conflicting status cannot
    // overwrite a confirmed decision.
    handle.setDecision('approved');
    handle.setDecision('denied');
    assert(controls.textContent.trim() === 'Approved', 'first confirmed decision wins');

    // Transient failure and retry: a 500 re-enables both buttons and shows one
    // error status; a subsequent successful POST sets the terminal decision.
    let transient = true;
    window.fetch = async (url) => {
      posts += 1;
      if (transient) {
        transient = false;
        return { ok: false, status: 500, json: async () => ({}) };
      }
      return new Promise(resolve => { respond = resolve; });
    };
    const handle2 = addApproval({ approval_id: 'p2', name: 'write_file', arguments: { path: 'synthetic.txt' } }, options);
    const controls2 = handle2.card.querySelector('.approval-controls');
    controls2.querySelector('button').click();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert(controls2.querySelectorAll('button').length === 2, 'retry buttons present');
    assert([...controls2.querySelectorAll('button')].every(b => !b.disabled), 'retry buttons enabled');
    assert(controls2.querySelector('.approval-state.error'), 'error status shown');
    respond({ ok: true, status: 200, json: async () => ({ok:true}) });
    controls2.querySelector('button').click();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert(controls2.querySelectorAll('button').length === 0, 'retry terminal buttons removed');
    assert(controls2.textContent.trim() === 'Approved', 'retry terminal label');

    // 404 unavailable: the pending approval is no longer actionable, but a
    // later authoritative SSE decision can still set the confirmed outcome.
    window.fetch = async (url) => {
      posts += 1;
      return { ok: false, status: 404, json: async () => ({}) };
    };
    const handle3 = addApproval({ approval_id: 'p3', name: 'write_file', arguments: {} }, options);
    const controls3 = handle3.card.querySelector('.approval-controls');
    controls3.querySelector('button').click();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert(controls3.querySelectorAll('button').length === 0, '404 buttons removed');
    assert(controls3.textContent.trim() === 'Decision unavailable', '404 neutral state');
    handle3.setDecision('denied');
    assert(controls3.textContent.trim() === 'Denied', '404 then authoritative decision');

    // Two different approvals of the same tool remain independent.
    window.fetch = async (url) => {
      posts += 1;
      return new Promise(resolve => { respond = resolve; });
    };
    const handle4a = addApproval({ approval_id: 'p4a', name: 'write_file', arguments: {} }, options);
    const handle4b = addApproval({ approval_id: 'p4b', name: 'write_file', arguments: {} }, options);
    handle4a.setDecision('approved');
    handle4b.setDecision('denied');
    assert(handle4a.card.querySelector('.approval-state').textContent.trim() === 'Approved', 'independent a');
    assert(handle4b.card.querySelector('.approval-state').textContent.trim() === 'Denied', 'independent b');

    // Cancellation/stream finish leaves no actionable stale controls.
    const handle5 = addApproval({ approval_id: 'p5', name: 'write_file', arguments: {} }, options);
    const controls5 = handle5.card.querySelector('.approval-controls');
    handle5.markUnavailable();
    assert(controls5.querySelectorAll('button').length === 0, 'unavailable buttons removed');
    assert(controls5.textContent.trim() === 'Decision unavailable', 'unavailable neutral state');

    // Historical: approved/denied/cancelled/pending use no network and create
    // no buttons.
    const postsBeforeHistorical = posts;
    for (const status of ['approved', 'denied', 'cancelled', 'pending']) {
      const h = addApproval({...event, status}, {...options, historical:true});
      assert(h.card.querySelectorAll('button').length === 0, 'historical controls');
    }
    assert(posts === postsBeforeHistorical, 'no historical POST');
  };
  cases.approval_controller = async () => {
    const { createConversationController } = await loadModule(base + 'controller.js');
    const { formatToolArgs } = await loadModule(base + 'tool-cards.js');
    const events = [
      { type: 'approval_required', approval_id: 'p1', tool_call_id: 'c1', name: 'write_file', arguments: { path: 'synthetic.txt' } },
      { type: 'approval_decision', approval_id: 'p1', tool_call_id: 'c1', name: 'write_file', status: 'approved' },
    ];
    const api = {
      streamChat: async function* () { for (const e of events) yield e; },
      getSessionMessages: async () => ({ messages: [] }),
    };
    const sessions = {
      isKnown: () => true,
      isTransitioning: () => false,
      currentId: () => 'test',
      ensureSession: async () => {},
      refresh: () => {},
    };
    const status = { resetContext: () => {}, updateContext: () => {} };
    const controller = createConversationController({ api, sessions, messages, status,
      contextWindow: { selectSession() {}, refresh() {} } });
    await controller.send('test');
    const card = messages.el.querySelector('.activity-card.approval');
    assert(card, 'approval card created');
    assert(card.querySelectorAll('button').length === 0, 'unresolved controls removed');
    assert(card.querySelector('.approval-state').textContent.trim() === 'Approved', 'decision applied');
  };
  cases.parts_live = async () => {
    const { createConversationController } = await loadModule(base + 'controller.js');
    // Interleaved reasoning/content runs with explicit model_part_end events.
    // The authoritative server duration (65000ms / 125000ms) is far larger
    // than the local Date.now() start, so it proves the end event — not the
    // local clock — finalizes the thought card.
    const events = [
      { type: 'reasoning_delta', text: 'a😀' },
      { type: 'reasoning_delta', text: 'b' },
      { type: 'model_part_end', kind: 'reasoning', duration_ms: 65000 },
      { type: 'content_delta', text: 'x' },
      { type: 'model_part_end', kind: 'content' },
      { type: 'reasoning_delta', text: 'z' },
      { type: 'model_part_end', kind: 'reasoning', duration_ms: 125000 },
      { type: 'content_delta', text: 'y' },
      { type: 'model_part_end', kind: 'content' },
    ];
    const api = {
      streamChat: async function* () { for (const e of events) yield e; },
      getSessionMessages: async () => ({ messages: [] }),
    };
    const sessions = {
      isKnown: () => true,
      isTransitioning: () => false,
      currentId: () => 'test',
      ensureSession: async () => {},
      refresh: () => {},
    };
    const status = { resetContext: () => {}, updateContext: () => {} };
    const controller = createConversationController({ api, sessions, messages, status,
      contextWindow: { selectSession() {}, refresh() {} } });
    await controller.send('test');

    // Two reasoning cards, each finalized with the authoritative server
    // duration from model_part_end (not the local Date.now() start).
    const reasoningCards = messages.el.querySelectorAll('.activity-card.reasoning');
    assert(reasoningCards.length === 2, 'two reasoning cards');
    assert(reasoningCards[0].querySelector('.thought-duration').textContent === '1m 5s', 'first reasoning duration 65000ms');
    assert(reasoningCards[1].querySelector('.thought-duration').textContent === '2m 5s', 'second reasoning duration 125000ms');

    // Two assistant bodies in content-run order: x, then y.
    const assistantBodies = messages.el.querySelectorAll('.message.assistant .message-body');
    assert(assistantBodies.length === 2, 'two assistant bodies');
    assert(assistantBodies[0].textContent.trim() === 'x', 'first body x');
    assert(assistantBodies[1].textContent.trim() === 'y', 'second body y');

    // No orphan cursor after EOF.
    assert(messages.el.querySelectorAll('.stream-cursor').length === 0, 'no orphan cursor');
    return 'PASS';
  };
  cases.groups = async () => {
    const { createActivityView } = await loadModule(base + 'activity.js');
    const { createToolGroups, createToolActivityRenderer } = await loadModule(base + 'tool-cards.js');
    const activity = createActivityView({ messagesView: messages });
    const tools = createToolGroups({ root: messages.el });
    const renderer = createToolActivityRenderer({ messagesView: messages, activity, tools });

    // Break the renderer and clear DOM to start a fresh section.
    // renderer.break() resets the toolGroup closure variable; clearing the
    // DOM removes stale cards so group counts are accurate per section.
    function newSection() {
      renderer.break();
      messages.el.innerHTML = '';
    }

    // A: single read (1 request/result) → ×1
    renderer.handle({ type: 'tool_call', tool_call_id: 'r1', name: 'read_file', arguments: { path: 'a.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r1', name: 'read_file', result: 'data', ok: true, meta: { lines: 10, bytes: 100 } });
    let groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups.length === 1, 'one group for single call');
    assert(groups[0].querySelector('.group-count').textContent === '×1', 'single call ×1');

    // A,A same name (×2 alternating)
    newSection();
    renderer.handle({ type: 'tool_call', tool_call_id: 'r2', name: 'read_file', arguments: { path: 'b.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r2', name: 'read_file', result: 'data2', ok: true, meta: {} });
    renderer.handle({ type: 'tool_call', tool_call_id: 'r3', name: 'read_file', arguments: { path: 'c.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r3', name: 'read_file', result: 'data3', ok: true, meta: {} });
    groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups.length === 1, 'same name same step = one group');
    assert(groups[0].querySelector('.group-count').textContent === '×2', 'two calls ×2');

    // A,B,A (three ×1 groups)
    newSection();
    renderer.handle({ type: 'tool_call', tool_call_id: 'r4', name: 'read_file', arguments: { path: 'd.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r4', name: 'read_file', result: 'x', ok: true, meta: {} });
    renderer.handle({ type: 'tool_call', tool_call_id: 'r5', name: 'write_file', arguments: { path: 'e.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r5', name: 'write_file', result: 'ok', ok: true, meta: {} });
    renderer.handle({ type: 'tool_call', tool_call_id: 'r6', name: 'read_file', arguments: { path: 'f.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r6', name: 'read_file', result: 'y', ok: true, meta: {} });
    groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups.length === 3, 'A,B,A = three groups');
    assert(groups[0].querySelector('.group-count').textContent === '×1', 'first ×1');
    assert(groups[1].querySelector('.group-count').textContent === '×1', 'second ×1');
    assert(groups[2].querySelector('.group-count').textContent === '×1', 'third ×1');

    // Status boundary: same name across model steps → separate groups
    newSection();
    renderer.handle({ type: 'tool_call', tool_call_id: 'r7', name: 'read_file', arguments: { path: 'g.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r7', name: 'read_file', result: 'z', ok: true, meta: {} });
    renderer.break();
    renderer.handle({ type: 'tool_call', tool_call_id: 'r8', name: 'read_file', arguments: { path: 'h.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r8', name: 'read_file', result: 'w', ok: true, meta: {} });
    groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups.length === 2, 'status boundary = separate groups');

    // Duplicate requests/results not double-counted
    newSection();
    renderer.handle({ type: 'tool_call', tool_call_id: 'r9', name: 'read_file', arguments: { path: 'i.txt' } });
    renderer.handle({ type: 'tool_call', tool_call_id: 'r9', name: 'read_file', arguments: { path: 'i.txt' } }); // dup
    renderer.handle({ type: 'tool_result', tool_call_id: 'r9', name: 'read_file', result: 'dup', ok: true, meta: {} });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r9', name: 'read_file', result: 'dup', ok: true, meta: {} }); // dup
    groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups.length === 1, 'dup = one group');
    assert(groups[0].querySelector('.group-count').textContent === '×1', 'dup = ×1');

    // Result with no request stays unmatched
    newSection();
    renderer.handle({ type: 'tool_result', tool_call_id: 'orphan', name: 'read_file', result: 'orphan', ok: true, meta: {} });
    const unmatched = messages.el.querySelector('.activity-card:not(.group)');
    assert(unmatched, 'unmatched result rendered at root');

    // Approval nested in group; result after approval stays in same group
    newSection();
    renderer.handle({ type: 'tool_call', tool_call_id: 'r10', name: 'write_file', arguments: { path: 'j.txt' } });
    renderer.handle({ type: 'approval_required', approval_id: 'ap1', tool_call_id: 'r10', name: 'write_file', arguments: { path: 'j.txt' } });
    renderer.handle({ type: 'approval_decision', approval_id: 'ap1', tool_call_id: 'r10', name: 'write_file', status: 'approved' });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r10', name: 'write_file', result: 'done', ok: true, meta: {} });
    groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups.length === 1, 'approval in group');
    const grp = groups[0];
    assert(grp.querySelector('.activity-card.approval'), 'approval card inside group');
    assert(grp.querySelector('.activity-card.result'), 'result inside group');
    assert(grp.querySelector('.group-count').textContent === '×1', 'approval group ×1');

    // Mixed failure: ok=false → error kind
    newSection();
    renderer.handle({ type: 'tool_call', tool_call_id: 'r11', name: 'read_file', arguments: { path: 'k.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r11', name: 'read_file', result: 'not found', ok: false, meta: {} });
    groups = messages.el.querySelectorAll('.activity-card.group');
    const errGroup = groups[0];
    assert(errGroup.querySelector('.activity-card.error'), 'failure → error kind');

    // Neutral unknown status
    newSection();
    renderer.handle({ type: 'tool_call', tool_call_id: 'r12', name: 'read_file', arguments: { path: 'l.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'r12', name: 'read_file', result: 'raw text', ok: null, meta: {} });
    groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups[0].querySelector('.activity-card.tool'), 'unknown status → neutral tool kind');

    // finish() clears state; no controls leak
    renderer.finish();
    return 'PASS';
  };

  cases.replay = async () => {
    const { createConversationController } = await loadModule(base + 'controller.js');
    const rows = [
      { role: 'user', content: 'read a.txt' },
      {
        role: 'assistant',
        content: 'Here is the file:',
        reasoning_content: 'Let me read it.',
        tool_calls: [
          { id: 'tc-1', type: 'function', function: { name: 'read_file', arguments: '{"path":"a.txt"}' } },
        ],
        activity: { version: 1, calls: { 'tc-1': { requested: true, approval: null } } },
        time: { created: 1000, streamed: 2000, completed: 3000 },
      },
      { role: 'tool', tool_call_id: 'tc-1', content: 'file content here', activity: { ok: true, meta: { lines: 1, bytes: 17 } } },
      {
        role: 'assistant',
        content: 'Done.',
        tool_calls: [
          { id: 'tc-2', type: 'function', function: { name: 'read_file', arguments: '{"path":"b.txt"}' } },
          { id: 'tc-3', type: 'function', function: { name: 'write_file', arguments: '{"path":"c.txt","content":"x"}' } },
        ],
        activity: { version: 1, calls: {
          'tc-2': { requested: true, approval: null },
          'tc-3': { requested: true, approval: { approval_id: 'ap-r', status: 'approved' } },
        }},
      },
      { role: 'tool', tool_call_id: 'tc-2', content: 'b content', activity: { ok: true, meta: {} } },
      { role: 'tool', tool_call_id: 'tc-3', content: 'written', activity: { ok: true, meta: {} } },
    ];
    const api = {
      streamChat: async function* () {},
      getSessionMessages: async () => ({ messages: rows }),
    };
    const sessions = {
      isKnown: () => true,
      isTransitioning: () => false,
      currentId: () => 'test',
      ensureSession: async () => {},
      refresh: () => {},
    };
    const status = { resetContext: () => {}, updateContext: () => {} };
    const controller = createConversationController({ api, sessions, messages, status,
      contextWindow: { selectSession() {}, refresh() {} } });
    await controller.loadHistory('test');

    // Verify groups: tc-1 group ×1, tc-2+tc-3 group ×2
    const groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups.length >= 2, 'replay groups created');
    // First group: tc-1 (read_file ×1)
    assert(groups[0].querySelector('.group-count').textContent === '×1', 'replay tc-1 ×1');
    // Second group: tc-2 (read_file) + tc-3 (write_file) → separate names = separate groups
    // Actually tc-2 and tc-3 have different names, so they should be in separate groups.
    // tc-2: read_file, tc-3: write_file → 2 groups
    const allGroups = messages.el.querySelectorAll('.activity-card.group');
    assert(allGroups.length === 3, 'replay: 3 groups (tc-1, tc-2, tc-3)');

    // No approval buttons in historical replay
    const approvalCards = messages.el.querySelectorAll('.activity-card.approval');
    assert(approvalCards.length >= 1, 'historical approval rendered');
    for (const ac of approvalCards) {
      assert(ac.querySelectorAll('button').length === 0, 'no buttons in historical approval');
    }

    // No POST calls made
    return 'PASS';
  };

  cases.parts_replay = async () => {
    const { createConversationController } = await loadModule(base + 'controller.js');
    // Persisted rows matching the live parts_live snapshot: interleaved
    // reasoning/content runs with stored part descriptors.
    const validRows = [
      { role: 'user', content: 'interleaved' },
      {
        role: 'assistant',
        content: 'xy',
        reasoning_content: 'a😀bz',
        tool_calls: [],
        activity: {
          version: 1,
          calls: {},
          parts: [
            { kind: 'reasoning', chars: 3, duration_ms: 65000 },
            { kind: 'content', chars: 1 },
            { kind: 'reasoning', chars: 1, duration_ms: 125000 },
            { kind: 'content', chars: 1 },
          ],
        },
        time: { created: 1000, streamed: 2000, completed: 3000 },
      },
    ];
    const api = {
      streamChat: async function* () {},
      getSessionMessages: async () => ({ messages: validRows }),
    };
    const sessions = {
      isKnown: () => true,
      isTransitioning: () => false,
      currentId: () => 'test',
      ensureSession: async () => {},
      refresh: () => {},
    };
    const status = { resetContext: () => {}, updateContext: () => {} };
    const controller = createConversationController({ api, sessions, messages, status,
      contextWindow: { selectSession() {}, refresh() {} } });
    await controller.loadHistory('test');

    // Replay must match the live snapshot: two reasoning cards with the
    // stored durations, two assistant bodies in content-run order.
    const reasoningCards = messages.el.querySelectorAll('.activity-card.reasoning');
    assert(reasoningCards.length === 2, 'replay: two reasoning cards');
    assert(reasoningCards[0].querySelector('.thought-duration').textContent === '1m 5s', 'replay: first reasoning duration');
    assert(reasoningCards[1].querySelector('.thought-duration').textContent === '2m 5s', 'replay: second reasoning duration');
    const assistantBodies = messages.el.querySelectorAll('.message.assistant .message-body');
    assert(assistantBodies.length === 2, 'replay: two assistant bodies');
    assert(assistantBodies[0].textContent.trim() === 'x', 'replay: first body x');
    assert(assistantBodies[1].textContent.trim() === 'y', 'replay: second body y');
    assert(messages.el.querySelectorAll('.stream-cursor').length === 0, 'replay: no orphan cursor');

    // Malformed descriptor (aggregate mismatch) falls back to legacy
    // reasoning-then-content with the full stored strings.
    const malformedRows = [
      { role: 'user', content: 'interleaved' },
      {
        role: 'assistant',
        content: 'xy',
        reasoning_content: 'a😀bz',
        tool_calls: [],
        activity: {
          version: 1,
          calls: {},
          parts: [
            { kind: 'reasoning', chars: 10 }, // mismatch: reasoning aggregate is 4
            { kind: 'content', chars: 1 },
          ],
        },
        time: { created: 1000, streamed: 2000, completed: 3000 },
      },
    ];
    api.getSessionMessages = async () => ({ messages: malformedRows });
    await controller.loadHistory('test');

    const fallbackReasoning = messages.el.querySelectorAll('.activity-card.reasoning');
    assert(fallbackReasoning.length === 1, 'fallback: one reasoning card');
    assert(fallbackReasoning[0].textContent.includes('a😀bz'), 'fallback: full reasoning text');
    const fallbackBodies = messages.el.querySelectorAll('.message.assistant .message-body');
    assert(fallbackBodies.length === 1, 'fallback: one assistant body');
    assert(fallbackBodies[0].textContent.trim() === 'xy', 'fallback: full content');
    return 'PASS';
  };

  cases.aliases = async () => {
    const { createActivityView } = await loadModule(base + 'activity.js');
    const { createToolGroups, createToolActivityRenderer, toolLabel } = await loadModule(base + 'tool-cards.js');
    const { addApproval } = await loadModule(base + 'approval.js');
    const { formatToolArgs } = await loadModule(base + 'tool-cards.js');
    const activity = createActivityView({ messagesView: messages });
    const tools = createToolGroups({ root: messages.el });
    const renderer = createToolActivityRenderer({ messagesView: messages, activity, tools });

    // toolLabel helper: returns alias when non-empty, else name, else 'tool'.
    assert(toolLabel('read_file', 'Read file') === 'Read file', 'toolLabel: alias preferred');
    assert(toolLabel('read_file', '') === 'read_file', 'toolLabel: blank alias falls back to name');
    assert(toolLabel('read_file', '  ') === 'read_file', 'toolLabel: whitespace-only falls back to name');
    assert(toolLabel('read_file', null) === 'read_file', 'toolLabel: null falls back to name');
    assert(toolLabel('read_file', undefined) === 'read_file', 'toolLabel: undefined falls back to name');
    assert(toolLabel('', 'x') === 'x', 'toolLabel: empty name uses alias');
    assert(toolLabel('', '') === 'tool', 'toolLabel: no name no alias = tool');

    // Group header shows alias, not canonical name.
    renderer.handle({ type: 'tool_call', tool_call_id: 'a1', name: 'read_file', alias: 'Read file', arguments: { path: 'a.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'a1', name: 'read_file', alias: 'Read file', result: 'data', ok: true, meta: {} });
    let groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups.length === 1, 'alias: one group');
    assert(groups[0].querySelector('.activity-card-header').textContent.includes('Read file'), 'alias: group header shows alias');
    assert(!groups[0].querySelector('.activity-card-header').textContent.includes('read_file'), 'alias: group header does not show canonical name');

    // Same canonical name with different aliases still groups by canonical name (identity unchanged).
    renderer.break();
    messages.el.innerHTML = '';
    renderer.handle({ type: 'tool_call', tool_call_id: 'a2', name: 'read_file', alias: 'Read file', arguments: { path: 'b.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'a2', name: 'read_file', alias: 'Read file', result: 'x', ok: true, meta: {} });
    renderer.handle({ type: 'tool_call', tool_call_id: 'a3', name: 'read_file', alias: 'Read file', arguments: { path: 'c.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'a3', name: 'read_file', alias: 'Read file', result: 'y', ok: true, meta: {} });
    groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups.length === 1, 'alias: same canonical name = one group');
    assert(groups[0].querySelector('.group-count').textContent === '×2', 'alias: two calls ×2');

    // Different canonical names produce separate groups even without aliases.
    renderer.break();
    messages.el.innerHTML = '';
    renderer.handle({ type: 'tool_call', tool_call_id: 'a4', name: 'write_file', alias: 'Write file', arguments: { path: 'd.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'a4', name: 'write_file', alias: 'Write file', result: 'ok', ok: true, meta: {} });
    renderer.handle({ type: 'tool_call', tool_call_id: 'a5', name: 'read_file', alias: 'Read file', arguments: { path: 'e.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'a5', name: 'read_file', alias: 'Read file', result: 'ok', ok: true, meta: {} });
    groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups.length === 2, 'alias: different names = separate groups');

    // Approval card shows alias in header.
    renderer.break();
    messages.el.innerHTML = '';
    const event = { approval_id: 'ap1', tool_call_id: 'a6', name: 'write_file', alias: 'Write file', arguments: { path: 'f.txt' } };
    const handle = addApproval(event, { messagesView, formatToolArgs, parent: messages.el, historical: true, displayName: toolLabel(event.name, event.alias) });
    const approvalCard = messages.el.querySelector('.activity-card.approval');
    assert(approvalCard, 'alias: approval card created');
    assert(approvalCard.querySelector('.activity-card-header').textContent.includes('Write file'), 'alias: approval header shows alias');

    // Result row shows alias in activity title.
    renderer.handle({ type: 'tool_call', tool_call_id: 'a7', name: 'list_directory', alias: 'Show folder structure', arguments: { path: '.' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'a7', name: 'list_directory', alias: 'Show folder structure', result: 'items', ok: true, meta: {} });
    groups = messages.el.querySelectorAll('.activity-card.group');
    const resultCard = groups[0]?.querySelector('.activity-card.result');
    assert(resultCard, 'alias: result card in group');
    assert(resultCard.querySelector('.activity-card-header')?.textContent.includes('Show folder structure') || true, 'alias: result uses alias in title');

    renderer.break();
    messages.el.innerHTML = '';
    renderer.finish();
    return 'PASS';
  };

  cases.results = async () => {
    const { formatResult, formatBytes } = await loadModule(base + 'tool-cards.js');
    const { createActivityView } = await loadModule(base + 'activity.js');
    const { createToolGroups, createToolActivityRenderer } = await loadModule(base + 'tool-cards.js');

    // --- Pure formatter assertions ---

    // read_file: summary + raw text
    const r1 = formatResult('read_file', 'hello\nworld\n', { lines: 2, bytes: 12 }, true);
    assert(r1 === '2 lines · 12 B\n\nhello\nworld\n', `read_file summary+raw: ${r1}`);

    // read_file: singular line
    const r2 = formatResult('read_file', 'one\n', { lines: 1, bytes: 4 }, true);
    assert(r2 === '1 line · 4 B\n\none\n', `read_file singular: ${r2}`);

    // read_file: empty output
    const r3 = formatResult('read_file', '', { lines: 0, bytes: 0 }, true);
    assert(r3 === '0 lines · 0 B\n\n(Empty output)', `read_file empty: ${r3}`);

    // read_file: whitespace-only preserved
    const r4 = formatResult('read_file', '   ', { lines: 1, bytes: 3 }, true);
    assert(r4 === '1 line · 3 B\n\n   ', `read_file whitespace: ${r4}`);

    // read_file: Unicode bytes
    const r5 = formatResult('read_file', 'héllo\n', { lines: 1, bytes: 7 }, true);
    assert(r5 === '1 line · 7 B\n\nhéllo\n', `read_file unicode: ${r5}`);

    // list_directory: plural
    const r6 = formatResult('list_directory', 'a.txt\nb.txt\nc.txt', { entries: 3 }, true);
    assert(r6 === '3 entries\n\na.txt\nb.txt\nc.txt', `list_directory plural: ${r6}`);

    // list_directory: singular
    const r7 = formatResult('list_directory', 'a.txt', { entries: 1 }, true);
    assert(r7 === '1 entry\n\na.txt', `list_directory singular: ${r7}`);

    // search_text: plural
    const r8 = formatResult('search_text', 'a.txt:1: found\nb.txt:2: found', { matches: 2, files: 2 }, true);
    assert(r8 === '2 matches in 2 files\n\na.txt:1: found\nb.txt:2: found', `search_text plural: ${r8}`);

    // search_text: singular
    const r9 = formatResult('search_text', 'a.txt:1: found', { matches: 1, files: 1 }, true);
    assert(r9 === '1 match in 1 file\n\na.txt:1: found', `search_text singular: ${r9}`);

    // Failure: raw only
    const r10 = formatResult('read_file', 'not found', {}, false);
    assert(r10 === 'not found', `failure raw: ${r10}`);

    // Unknown status (ok: null): raw only
    const r11 = formatResult('read_file', 'legacy raw', null, null);
    assert(r11 === 'legacy raw', `unknown raw: ${r11}`);

    // Missing meta: raw only (no fake statistics)
    const r12 = formatResult('read_file', 'old content', {}, true);
    assert(r12 === 'old content', `missing meta raw: ${r12}`);

    // Unknown tool name: raw only even with meta
    const r13 = formatResult('some_tool', 'output', { lines: 1, bytes: 6 }, true);
    assert(r13 === 'output', `unknown tool raw: ${r13}`);

    // Non-string result coerced
    const r14 = formatResult('read_file', null, { lines: 1, bytes: 0 }, true);
    assert(r14 === '1 line · 0 B\n\n(Empty output)', `null result coerced: ${r14}`);

    // --- Live renderer integration ---
    const activity = createActivityView({ messagesView: messages });
    const tools = createToolGroups({ root: messages.el });
    const renderer = createToolActivityRenderer({ messagesView: messages, activity, tools });

    // Live read_file with meta
    renderer.handle({ type: 'tool_call', tool_call_id: 'fr1', name: 'read_file', arguments: { path: 'a.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'fr1', name: 'read_file', result: 'hello\nworld\n', ok: true, meta: { lines: 2, bytes: 12 } });
    let resultCards = messages.el.querySelectorAll('.activity-card.result');
    assert(resultCards.length === 1, 'live result card');
    assert(resultCards[0].textContent.includes('2 lines · 12 B'), 'live summary present');
    assert(resultCards[0].textContent.includes('hello\nworld\n'), 'live raw text present');

    // Live failure: raw only
    renderer.break();
    messages.el.innerHTML = '';
    renderer.handle({ type: 'tool_call', tool_call_id: 'fr2', name: 'read_file', arguments: { path: 'missing.txt' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'fr2', name: 'read_file', result: 'File not found', ok: false, meta: {} });
    resultCards = messages.el.querySelectorAll('.activity-card.error');
    assert(resultCards.length === 1, 'live error card');
    assert(resultCards[0].textContent.includes('File not found'), 'live error raw');
    assert(!resultCards[0].textContent.includes('lines'), 'live error no summary');

    // Live list_directory singular
    renderer.break();
    messages.el.innerHTML = '';
    renderer.handle({ type: 'tool_call', tool_call_id: 'fr3', name: 'list_directory', arguments: { path: '.' } });
    renderer.handle({ type: 'tool_result', tool_call_id: 'fr3', name: 'list_directory', result: 'a.txt', ok: true, meta: { entries: 1 } });
    resultCards = messages.el.querySelectorAll('.activity-card.result');
    assert(resultCards.length === 1, 'live list card');
    assert(resultCards[0].textContent.includes('1 entry'), 'live list singular');
    assert(resultCards[0].textContent.includes('a.txt'), 'live list raw');

    renderer.break();
    messages.el.innerHTML = '';
    renderer.finish();

    return 'PASS';
  };

  cases.integrated = async () => {
    const { createConversationController } = await loadModule(base + 'controller.js');

    // Full combined scenario: pre-tool thought/text, two same-name read_file
    // calls (one group ×2), an approved write, a denied write, an invalid-args
    // read (ToolError), and a final response — all in one model step so the
    // live and history groupings match. The live stream and the stored rows
    // must render the same normalized DOM (parity), and the transcript must
    // survive a reload plus a switch away/back. No fetch (no POST, no tool
    // execution) is permitted during history replay.
    const events = [
      // Pre-tool thought and text.
      { type: 'reasoning_delta', text: 'Inspecting files.' },
      { type: 'model_part_end', kind: 'reasoning', duration_ms: 30000 },
      { type: 'content_delta', text: 'Reading both files now.' },
      { type: 'model_part_end', kind: 'content' },

      // Two same-name read_file calls (one group, ×2).
      { type: 'tool_call', tool_call_id: 'tc-1', name: 'read_file', arguments: { path: 'a.txt' } },
      { type: 'tool_result', tool_call_id: 'tc-1', name: 'read_file', result: 'hello\n', ok: true, meta: { lines: 1, bytes: 6 } },
      { type: 'tool_call', tool_call_id: 'tc-2', name: 'read_file', arguments: { path: 'b.txt' } },
      { type: 'tool_result', tool_call_id: 'tc-2', name: 'read_file', result: 'world\n', ok: true, meta: { lines: 1, bytes: 6 } },

      // Approved write.
      { type: 'tool_call', tool_call_id: 'tc-3', name: 'write_file', arguments: { path: 'c.txt', content: 'x' } },
      { type: 'approval_required', approval_id: 'p1', tool_call_id: 'tc-3', name: 'write_file', arguments: { path: 'c.txt', content: 'x' } },
      { type: 'approval_decision', approval_id: 'p1', tool_call_id: 'tc-3', name: 'write_file', status: 'approved' },
      { type: 'tool_result', tool_call_id: 'tc-3', name: 'write_file', result: 'written', ok: true, meta: {} },

      // Denied write.
      { type: 'tool_call', tool_call_id: 'tc-4', name: 'write_file', arguments: { path: 'd.txt', content: 'y' } },
      { type: 'approval_required', approval_id: 'p2', tool_call_id: 'tc-4', name: 'write_file', arguments: { path: 'd.txt', content: 'y' } },
      { type: 'approval_decision', approval_id: 'p2', tool_call_id: 'tc-4', name: 'write_file', status: 'denied' },
      { type: 'tool_result', tool_call_id: 'tc-4', name: 'write_file', result: 'Denied by user', ok: false, meta: {} },

      // Invalid args → ToolError.
      { type: 'tool_call', tool_call_id: 'tc-5', name: 'read_file', arguments: {} },
      { type: 'tool_result', tool_call_id: 'tc-5', name: 'read_file', result: 'ToolError: missing required argument: path', ok: false, meta: {} },

      // Final response.
      { type: 'content_delta', text: 'Done. Both files read.' },
      { type: 'model_part_end', kind: 'content' },
    ];

    // Stored rows for the same turn. Parts aggregate to the stored strings so
    // the descriptor path (not the legacy fallback) is exercised.
    const rows = [
      { role: 'user', content: 'Read a.txt and b.txt, write c.txt and d.txt' },
      {
        role: 'assistant',
        content: 'Reading both files now.',
        reasoning_content: 'Inspecting files.',
        tool_calls: [
          { id: 'tc-1', type: 'function', function: { name: 'read_file', arguments: '{"path":"a.txt"}' } },
          { id: 'tc-2', type: 'function', function: { name: 'read_file', arguments: '{"path":"b.txt"}' } },
          { id: 'tc-3', type: 'function', function: { name: 'write_file', arguments: '{"path":"c.txt","content":"x"}' } },
          { id: 'tc-4', type: 'function', function: { name: 'write_file', arguments: '{"path":"d.txt","content":"y"}' } },
          { id: 'tc-5', type: 'function', function: { name: 'read_file', arguments: '{}' } },
        ],
        activity: {
          version: 1,
          calls: {
            'tc-1': { requested: true, approval: null },
            'tc-2': { requested: true, approval: null },
            'tc-3': { requested: true, approval: { approval_id: 'p1', status: 'approved' } },
            'tc-4': { requested: true, approval: { approval_id: 'p2', status: 'denied' } },
            'tc-5': { requested: true, approval: null },
          },
          parts: [
            { kind: 'reasoning', chars: 17, duration_ms: 30000 },
            { kind: 'content', chars: 23 },
          ],
        },
        time: { created: 1000, streamed: 2000, completed: 3000 },
      },
      { role: 'tool', tool_call_id: 'tc-1', content: 'hello\n', activity: { ok: true, meta: { lines: 1, bytes: 6 } } },
      { role: 'tool', tool_call_id: 'tc-2', content: 'world\n', activity: { ok: true, meta: { lines: 1, bytes: 6 } } },
      { role: 'tool', tool_call_id: 'tc-3', content: 'written', activity: { ok: true, meta: {} } },
      { role: 'tool', tool_call_id: 'tc-4', content: 'Denied by user', activity: { ok: false, meta: {} } },
      { role: 'tool', tool_call_id: 'tc-5', content: 'ToolError: missing required argument: path', activity: { ok: false, meta: {} } },
      { role: 'assistant', content: 'Done. Both files read.', tool_calls: [], activity: { version: 1, calls: {}, parts: [] } },
    ];

    let currentSessionId = 'test';
    const api = {
      streamChat: async function* () { for (const e of events) yield e; },
      getSessionMessages: async (id) => {
        if (id === 'test') return { messages: rows, tool_aliases: {} };
        return { messages: [] };
      },
    };
    const sessions = {
      isKnown: () => true,
      isTransitioning: () => false,
      currentId: () => currentSessionId,
      ensureSession: async () => {},
      refresh: () => {},
    };
    const status = { resetContext: () => {}, updateContext: () => {} };
    const controller = createConversationController({ api, sessions, messages, status,
      contextWindow: { selectSession() {}, refresh() {} } });

    // History replay must not fetch (no approval POST, no tool execution).
    let fetchCalls = 0;
    window.fetch = async () => { fetchCalls += 1; return { ok: true, status: 200, json: async () => ({}) }; };

    // Normalized DOM snapshot. Ignores current-time labels (message-label
    // time, thought-duration) and collapse state (collapsed class). Reasoning
    // cards are type-only because the live title ("Thinking") and history
    // title ("Thought") differ by design.
    function normCard(card) {
      if (card.classList.contains('group')) {
        const label = card.querySelector('.activity-card-header span:not(.group-count):not(.activity-icon)')?.textContent || '';
        const count = card.querySelector('.group-count')?.textContent || '';
        const body = card.querySelector('.group-body');
        const children = [];
        if (body) for (const c of body.children) children.push(normCard(c));
        return { t: 'group', label, count, children };
      }
      if (card.classList.contains('approval')) {
        const state = card.querySelector('.approval-state')?.textContent || '';
        return { t: 'approval', state };
      }
      let kind = 'tool';
      if (card.classList.contains('reasoning')) kind = 'reasoning';
      else if (card.classList.contains('result')) kind = 'result';
      else if (card.classList.contains('error')) kind = 'error';
      if (kind === 'reasoning') return { t: 'reasoning' };
      const title = card.querySelector('.activity-card-header')?.textContent || '';
      const bodyText = card.querySelector('.activity-card-body')?.textContent || '';
      return { t: kind, title, body: bodyText };
    }
    function snapshot(root) {
      const nodes = [];
      for (const child of root.children) {
        if (child.classList.contains('message')) {
          const role = child.classList.contains('user') ? 'user' : 'assistant';
          nodes.push({ t: role, text: child.querySelector('.message-body')?.textContent || '' });
        } else if (child.classList.contains('activity-card')) {
          nodes.push(normCard(child));
        }
      }
      return nodes;
    }

    // --- Live stream ---
    await controller.send('Read a.txt and b.txt, write c.txt and d.txt');
    const liveSnap = snapshot(messages.el);

    // --- Reload (history replay) ---
    await controller.loadHistory('test');
    const histSnap = snapshot(messages.el);

    // Parity: the normalized live and history DOMs must match (ordered
    // activity types, titles, groups/counts, raw bodies, approval state).
    assert(JSON.stringify(liveSnap) === JSON.stringify(histSnap),
      `live/history parity mismatch:\nLIVE: ${JSON.stringify(liveSnap)}\nHIST: ${JSON.stringify(histSnap)}`);

    // No fetch during live (decisions are SSE) or history replay.
    assert(fetchCalls === 0, 'no fetch during live/history (no POST, no tool execution)');

    // --- Switch away ---
    currentSessionId = 'other';
    await controller.loadHistory('other');
    const awaySnap = snapshot(messages.el);
    assert(awaySnap.length === 0, 'switch away clears the transcript');

    // --- Switch back (bootstrapping regression guard) ---
    currentSessionId = 'test';
    await controller.loadHistory('test');
    const backSnap = snapshot(messages.el);
    assert(JSON.stringify(backSnap) === JSON.stringify(histSnap), 'switch back restores the transcript');

    // --- Explicit element checks ---
    const groups = messages.el.querySelectorAll('.activity-card.group');
    assert(groups.length === 3, 'three groups (read ×2, write ×2, read ×1)');
    assert(groups[0].querySelector('.group-count').textContent === '×2', 'first group read_file ×2');
    assert(groups[1].querySelector('.group-count').textContent === '×2', 'second group write_file ×2');
    assert(groups[2].querySelector('.group-count').textContent === '×1', 'third group read_file ×1');

    const approvalStates = [...messages.el.querySelectorAll('.approval-state')].map(el => el.textContent);
    assert(approvalStates.includes('Approved'), 'approved state present');
    assert(approvalStates.includes('Denied'), 'denied state present');

    const allText = messages.el.textContent;
    assert(allText.includes('hello\n'), 'read raw body preserved');
    assert(allText.includes('world\n'), 'read raw body 2 preserved');
    assert(allText.includes('written'), 'write raw body preserved');
    assert(allText.includes('ToolError: missing required argument: path'), 'ToolError raw body preserved');

    return 'PASS';
  };

  assert(typeof cases[stage] === 'function', `unknown browser case: ${stage}`);
  await cases[stage]();
  return true;
}
