// Approval cards: render a pending approval with Approve/Deny controls and
// POST the decision. Controls stay enabled for the life of the card (the
// active turn is still running) and are disabled after a decision is made.

import { respondToApproval } from '../api.js';

export function addApproval(event, { messagesView, formatToolArgs }) {
  const card = document.createElement('div');
  card.className = 'activity-card approval';
  const header = document.createElement('div');
  header.className = 'activity-card-header';
  const icon = document.createElement('span');
  icon.className = 'activity-icon';
  icon.textContent = '⚠';
  header.appendChild(icon);
  header.appendChild(document.createTextNode(`Approval required · ${event.name}`));
  const pre = document.createElement('pre');
  pre.className = 'activity-card-body';
  pre.textContent = formatToolArgs(event.name, event.arguments);
  const controls = document.createElement('div');
  controls.className = 'approval-controls';
  const approve = document.createElement('button');
  approve.className = 'approve-button';
  approve.textContent = 'Approve';
  approve.setAttribute('aria-label', `Approve ${event.name}`);
  const deny = document.createElement('button');
  deny.className = 'deny-button';
  deny.textContent = 'Deny';
  deny.setAttribute('aria-label', `Deny ${event.name}`);

  async function resolve(approved) {
    controls.querySelectorAll('button').forEach(b => b.disabled = true);
    try {
      await respondToApproval(event.approval_id, approved);
      controls.insertAdjacentHTML('beforeend', `<span class="approval-state">${approved ? 'Approved' : 'Denied'}</span>`);
    } catch {
      controls.insertAdjacentHTML('beforeend', '<span class="approval-state">Could not submit decision.</span>');
    }
  }

  approve.onclick = () => resolve(true);
  deny.onclick = () => resolve(false);
  controls.append(approve, deny);
  card.append(header, pre, controls);
  messagesView.el.appendChild(card);
  messagesView.scroll();
}
