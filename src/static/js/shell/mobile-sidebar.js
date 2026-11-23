// Mobile sidebar drawer: open/close state and toggle/backdrop handlers.

export function initMobileSidebar({ toggle, sidebar, backdrop }) {
  function setOpen(open) {
    sidebar.classList.toggle('open', open);
    backdrop.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
  }
  toggle.addEventListener('click', () => {
    setOpen(!sidebar.classList.contains('open'));
  });
  backdrop.addEventListener('click', () => setOpen(false));
  return { setOpen, close: () => setOpen(false) };
}
