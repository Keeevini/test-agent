// Approval cards: render a pending approval with Approve/Deny controls and
// POST the decision. A confirmed decision is terminal and immutable: the
// buttons are physically removed and replaced by a single status node. Live
// cards are interactive; historical cards are read-only (no buttons, no
// POST). All status text is set via DOM text, never injected HTML.

import { respondToApproval } from '../api.js';

const DECISION_LABELS = {
  approved: 'Approved',
  denied: 'Denied',
  cancelled: 'Cancelled',
};

export function addApproval(event, { messagesView, formatToolArgs, parent = messagesView.el, historical = false, displayName }) {
  const display = displayName || event.name || 'tool';
  const card = document.createElement('div');
  card.className = 'activity-card approval';
  const header = document.createElement('div');
  header.className = 'activity-card-header';
  const icon = document.createElement('span');
  icon.className = 'activity-icon';
  icon.textContent = '⚠';
  header.appendChild(icon);
  header.appendChild(document.createTextNode(`Approval required · ${display}`));
  const pre = document.createElement('pre');
  pre.className = 'activity-card-body';
  pre.textContent = formatToolArgs(event.name, event.arguments);
  const controls = document.createElement('div');
  controls.className = 'approval-controls';

  // Terminal state: once a decision is confirmed it is immutable. Repeated
  // identical decisions are idempotent; a later conflicting status cannot
  // overwrite a confirmed decision.
  let decision = null;
  function setDecision(status) {
    if (!['approved', 'denied', 'cancelled'].includes(status)) return;
    if (decision !== null) return;
    decision = status;
    const label = document.createElement('span');
    label.className = `approval-state ${status}`;
    label.setAttribute('role', 'status');
    label.textContent = DECISION_LABELS[status];
    controls.replaceChildren(label);
    card.classList.add('resolved');
  }

  // Neutral "no actionable decision" state: used when the stream ends without
  // a decision, or a 404 makes the pending approval unavailable. It never
  // overrides a confirmed terminal decision (decision === null only).
  function markUnavailable() {
    if (decision !== null) return;
    const label = document.createElement('span');
    label.className = 'approval-state unavailable';
    label.textContent = 'Decision unavailable';
    controls.replaceChildren(label);
    card.classList.add('resolved');
  }

  if (historical) {
    // Historical cards are read-only: terminal status is rendered
    // immediately; unresolved history is neutral and never creates buttons.
    card.classList.add('historical');
    if (['approved', 'denied', 'cancelled'].includes(event.status)) {
      setDecision(event.status);
    } else {
      const label = document.createElement('span');
      label.className = 'approval-state unavailable';
      label.textContent = 'No recorded decision';
      controls.append(label);
      card.classList.add('resolved');
    }
  } else {
    const approve = document.createElement('button');
    approve.className = 'approve-button';
    approve.textContent = 'Approve';
    approve.setAttribute('aria-label', `Approve ${display}`);
    const deny = document.createElement('button');
    deny.className = 'deny-button';
    deny.textContent = 'Deny';
    deny.setAttribute('aria-label', `Deny ${display}`);

    let submitting = false;
    async function resolve(approved) {
      if (submitting) return;
      submitting = true;
      // Clear any prior transient error on retry.
      controls.querySelectorAll('.approval-state.error').forEach(el => el.remove());
      controls.querySelectorAll('button').forEach(b => b.disabled = true);
      try {
        await respondToApproval(event.approval_id, approved);
        setDecision(approved ? 'approved' : 'denied');
      } catch (error) {
        if (decision === null) {
          if (error && error.status === 404) {
            // Pending approval is no longer actionable; neutral state.
            markUnavailable();
          } else {
            // Transient failure: re-enable both and show one error status.
            controls.querySelectorAll('button').forEach(b => b.disabled = false);
            const err = document.createElement('span');
            err.className = 'approval-state error';
            err.textContent = 'Could not submit decision. Try again.';
            controls.append(err);
          }
        }
      }
      submitting = false;
    }

    approve.onclick = () => resolve(true);
    deny.onclick = () => resolve(false);
    controls.append(approve, deny);
  }

  card.append(header, pre, controls);
  parent.appendChild(card);
  messagesView.scroll();
  return { card, setDecision, markUnavailable };
}
