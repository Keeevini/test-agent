// Bootstrap and dependency wiring only. No rendering functions, no SSE switch
// statements, no session-row rendering, no CSS rules. The domain factories
// are wired exactly once here.

import * as api from './js/api.js';
import { createMessagesView } from './js/conversation/messages.js';
import { createConversationController } from './js/conversation/controller.js';
import { initComposer } from './js/conversation/composer.js';
import { createSessionsController } from './js/sessions/controller.js';
import { createStatusView } from './js/shell/status.js';
import { initMobileSidebar } from './js/shell/mobile-sidebar.js';

const byId = id => document.getElementById(id);

const messages = createMessagesView({
  messagesEl: byId('messages'),
  emptyState: byId('emptyState'),
  scrollToBottom: byId('scrollToBottom'),
});

const status = createStatusView({
  badge: byId('connectionBadge'),
  indicator: byId('contextIndicator'),
  fill: byId('contextFill'),
  pct: byId('contextPct'),
});

const drawer = initMobileSidebar({
  toggle: byId('sidebarToggle'),
  sidebar: byId('sidebar'),
  backdrop: byId('sidebarBackdrop'),
});

const sessions = createSessionsController({
  api,
  root: byId('sessionList'),
  search: byId('sessionSearch'),
  storage: localStorage,
  drawer,
});

const conversation = createConversationController({ api, sessions, messages, status });

// Connect controllers after construction to avoid circular module imports.
sessions.setLifecycleHandlers({
  isBusy: () => conversation.isBusy(),
  onActivate: id => conversation.loadHistory(id),
  onClear: () => {
    conversation.showEmpty();
    status.resetContext();
  },
});

initComposer({
  composer: byId('composer'),
  promptEl: byId('prompt'),
  sendButton: byId('sendButton'),
  clearButton: byId('clearButton'),
  conversation,
  onNewChat: () => sessions.createNew(),
});

window.addEventListener('online', () => status.checkHealth());
window.addEventListener('offline', () => status.showOffline());

// Initial history loading happens exactly once, here.
await sessions.init();
await conversation.loadHistory(sessions.currentId());
await status.checkHealth();
byId('prompt').focus();
