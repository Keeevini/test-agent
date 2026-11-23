// Accessible delete-confirm dialog. Returns a Promise that resolves to true
// when the user confirms and false when they cancel, close via
// backdrop/Escape, or dismiss. Adds a focus trap (Tab/Shift+Tab cycle among
// the two buttons), restores focus to the previously active element on close,
// and stops Escape propagation so the global Escape-to-stop-stream handler
// does not run while the modal is open.

export function showConfirmDelete(sessionTitle) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';

    const dialog = document.createElement('div');
    dialog.className = 'confirm-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-labelledby', 'confirm-dialog-title');

    const header = document.createElement('div');
    header.className = 'confirm-dialog-header';
    const titleEl = document.createElement('h2');
    titleEl.id = 'confirm-dialog-title';
    titleEl.className = 'confirm-dialog-title';
    titleEl.textContent = 'Delete session?';
    header.appendChild(titleEl);

    const body = document.createElement('div');
    body.className = 'confirm-dialog-body';
    body.textContent =
      `This permanently removes “${sessionTitle}” and all of its messages. ` +
      'This cannot be undone.';

    const actions = document.createElement('div');
    actions.className = 'confirm-dialog-actions';
    const cancelButton = document.createElement('button');
    cancelButton.className = 'confirm-dialog-button cancel';
    cancelButton.textContent = 'Cancel';
    const deleteButton = document.createElement('button');
    deleteButton.className = 'confirm-dialog-button delete';
    deleteButton.textContent = 'Delete';
    actions.append(cancelButton, deleteButton);

    dialog.append(header, body, actions);
    overlay.appendChild(dialog);

    const previouslyFocused = document.activeElement;
    let settled = false;
    function close(result) {
      if (settled) return;
      settled = true;
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      if (previouslyFocused && document.body.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
      resolve(result);
    }
    function onKey(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        close(false);
        return;
      }
      // Focus trap: Tab/Shift+Tab cycle among the two buttons.
      if (e.key === 'Tab') {
        const focusables = [cancelButton, deleteButton];
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    cancelButton.addEventListener('click', () => close(false));
    deleteButton.addEventListener('click', () => close(true));
    overlay.addEventListener('mousedown', (e) => {
      if (e.target === overlay) close(false);
    });

    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    // Focus the safe option so a stray Enter never confirms the delete.
    cancelButton.focus();
  });
}
