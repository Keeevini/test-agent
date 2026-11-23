// Shell status: connectivity badge and context-usage indicator. Owns the
// shell status DOM only. `getHealth` is the transport helper from api.js.

import { getHealth } from '../api.js';

export function createStatusView({ badge, indicator, fill, pct }) {
  async function checkHealth() {
    try {
      await getHealth();
      badge.textContent = 'UI ready';
      badge.className = 'badge online';
    } catch {
      badge.textContent = 'UI offline';
      badge.className = 'badge offline';
    }
  }

  function showOffline() {
    badge.textContent = 'Offline';
    badge.className = 'badge offline';
  }

  function updateContext(usage) {
    if (!fill || !pct || !indicator) return;
    const percent = Math.max(0, Math.min(100, usage.percentage || 0));
    pct.textContent = `${Math.round(percent)}%`;
    fill.style.width = `${percent}%`;
    indicator.classList.remove('caution', 'warning', 'danger');
    if (percent >= 95) {
      indicator.classList.add('danger');
    } else if (percent >= 80) {
      indicator.classList.add('warning');
    } else if (percent >= 50) {
      indicator.classList.add('caution');
    }
    indicator.title = `Context: ${usage.tokens} / ${usage.max_tokens} tokens`;
  }

  function resetContext() {
    if (!fill || !pct || !indicator) return;
    pct.textContent = '0%';
    fill.style.width = '0%';
    indicator.classList.remove('caution', 'warning', 'danger');
    indicator.title = 'Context: 0 tokens';
  }

  return { checkHealth, showOffline, updateContext, resetContext };
}
