// Composer: send/stop handlers, Enter/Shift+Enter, autosize, example
// prompts, and the Escape-to-stop. Owns the composer DOM and mirrors the
// conversation's busy state onto the input/send button. No networking.

export function initComposer({ composer, promptEl, sendButton, clearButton, conversation, onNewChat }) {
  // Mirror busy state onto the composer DOM (single listener, registered once).
  conversation.onBusyChange(value => {
    promptEl.disabled = value;
    sendButton.querySelector('.send-label').textContent = value ? 'Stop' : 'Send';
    sendButton.querySelector('.send-icon').textContent = value ? '■' : '↑';
    if (!value) promptEl.focus();
  });

  composer.addEventListener('submit', event => {
    event.preventDefault();
    if (conversation.isBusy()) {
      conversation.stop();
      return;
    }
    const text = promptEl.value;
    promptEl.value = '';
    promptEl.style.height = 'auto';
    conversation.send(text);
  });

  promptEl.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });

  promptEl.addEventListener('input', () => {
    promptEl.style.height = 'auto';
    promptEl.style.height = `${Math.min(promptEl.scrollHeight, 180)}px`;
  });

  // Example prompts.
  document.querySelectorAll('.example').forEach(button => {
    button.addEventListener('click', () => {
      promptEl.value = button.textContent;
      promptEl.focus();
    });
  });

  clearButton.addEventListener('click', async () => {
    if (conversation.isBusy()) return;
    if (!confirm('Start a new chat? This keeps the previous conversation in your history.')) return;
    await onNewChat();
  });

  // Escape stops the in-flight request. The confirm dialog stops Escape
  // propagation, so this never fires while a modal is open.
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && conversation.isBusy()) {
      conversation.stop();
    }
  });
}
