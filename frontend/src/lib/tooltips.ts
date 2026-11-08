/**
 * WCAG 1.4.13 (content on hover or focus) for the CSS tooltips of icon buttons and navigation links (STU-053):
 *
 * - dismissible: Escape hides every tooltip until the pointer moves or the focus changes;
 * - hoverable: the tooltip is part of its trigger's box (no gap; pointer events on only while the trigger is hovered,
 *   so a click beside a link that only has the keyboard focus does not follow it), and the pointer can move onto it;
 * - persistent: CSS keeps it while the trigger is hovered or focused.
 */
const HIDDEN = 'data-tooltips-hidden';

export function installTooltipDismiss(root: HTMLElement = document.documentElement): () => void {
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') root.setAttribute(HIDDEN, '');
  };
  const show = () => {
    root.removeAttribute(HIDDEN);
  };
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('pointermove', show);
  window.addEventListener('focusin', show);
  return () => {
    window.removeEventListener('keydown', onKeyDown);
    window.removeEventListener('pointermove', show);
    window.removeEventListener('focusin', show);
  };
}
