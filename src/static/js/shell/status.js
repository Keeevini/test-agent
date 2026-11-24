// Shell status: connectivity badge and context-usage ring. Owns the shell
// status DOM only. `getHealth` is the transport helper from api.js. The ring
// is painted by adjusting the fill circle's stroke-dashoffset; the count is
// the formatted used-token count shown to the right of the ring.

import { getHealth } from '../api.js';

const CIRCUMFERENCE = 2 * Math.PI * 9; // r=9 on a 0 0 24 24 viewBox

function formatTokens(n) {
  if (!Number.isFinite(n)) return '0';
  const abs = Math.abs(n);
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1)}m`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1)}k`.replace(/\.0$/, '');
  return String(Math.round(n));
}

export function createStatusView({ badge, indicator, fill, count }) {
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
    if (!fill || !count || !indicator) return;
    const percent = Math.max(0, Math.min(100, usage.percentage || 0));
    const used = usage.used_tokens ?? usage.tokens ?? 0;
    // Paint the ring: dashoffset = circumference * (1 - percent/100).
    fill.style.strokeDashoffset = CIRCUMFERENCE * (1 - percent / 100);
    count.textContent = formatTokens(used);
    indicator.classList.remove('caution', 'warning', 'danger');
    if (percent >= 95) {
      indicator.classList.add('danger');
    } else if (percent >= 80) {
      indicator.classList.add('warning');
    } else if (percent >= 50) {
      indicator.classList.add('caution');
    }
  }

  function resetContext() {
    if (!fill || !count || !indicator) return;
    fill.style.strokeDashoffset = CIRCUMFERENCE;
    count.textContent = '0';
    indicator.classList.remove('caution', 'warning', 'danger');
  }

  return { checkHealth, showOffline, updateContext, resetContext };
}
