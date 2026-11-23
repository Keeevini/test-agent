// Sidebar title hover scroll. Slowly reveals overflowing titles while a row
// is hovered. Hover is an explicit interaction, so this runs even when
// reduced motion is set; WAAPI does not rely on the CSS transitions disabled
// by the global reduced-motion media rule. Preserve unchanged.

export const TITLE_SCROLL_SPEED = 75; // pixels per second; visible but still slow

export function startTitleScroll(titleEl) {
  const inner = titleEl.querySelector('span');
  if (!inner) return;

  const overflow = inner.scrollWidth - inner.clientWidth;
  if (overflow <= 0) return;

  // Restart the title from the beginning when its row is re-entered.
  inner._titleScrollAnimation?.cancel();
  inner._titleScrollAnimation = inner.animate(
    [
      { transform: 'translateX(0px)' },
      { transform: `translateX(-${overflow}px)` },
    ],
    {
      duration: (overflow / TITLE_SCROLL_SPEED) * 1000,
      easing: 'linear',
      fill: 'forwards',
    }
  );
}

export function stopTitleScroll(titleEl) {
  const inner = titleEl.querySelector('span');
  if (!inner) return;
  inner._titleScrollAnimation?.cancel();
  inner._titleScrollAnimation = null;
}
