/**
 * F6 and Shift+F6 move the focus between the areas of the shell (08 section 7): header, activity bar, tabs, main area
 * and status bar, in that order, wrapping around. An area is an element with `data-region`; only rendered areas take
 * part (the tabs only with a workspace). An area gets the focus on its current item (the selected tab, the active
 * navigation link), else on its first control, else on itself (`tabindex="-1"`).
 */

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function regions(root: ParentNode = document): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>('[data-region]')];
}

function target(region: HTMLElement): HTMLElement {
  if (region.tabIndex === -1 && region.hasAttribute('tabindex')) return region;
  const current = region.querySelector<HTMLElement>(
    '[aria-current="page"], [role="tab"][aria-selected="true"]',
  );
  return current ?? region.querySelector<HTMLElement>(FOCUSABLE) ?? region;
}

/** Move the focus to the next (+1) or the previous (-1) area; returns the area's name. */
export function moveRegion(direction: 1 | -1, root: ParentNode = document): string | null {
  const all = regions(root);
  if (all.length === 0) return null;
  const active = document.activeElement;
  const index = all.findIndex((region) => region === active || region.contains(active));
  const next =
    index === -1
      ? direction === 1
        ? all[0]
        : all[all.length - 1]
      : all[(index + direction + all.length) % all.length];
  if (next === undefined) return null;
  const element = target(next);
  if (element === next) {
    if (!next.hasAttribute('tabindex')) next.tabIndex = -1;
    // An area has no focus ring of its own (tabindex -1); a keyboard move shows one until the focus leaves.
    next.dataset.regionFocus = '';
    next.addEventListener('blur', () => delete next.dataset.regionFocus, { once: true });
  }
  element.focus();
  return next.dataset.region ?? null;
}
