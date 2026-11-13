/**
 * F6 and Shift+F6 move the focus between the areas of the shell (08 section 7): header, activity bar, tabs, main area
 * and status bar, in that order, wrapping around. An area is an element with `data-region`; only rendered areas take
 * part (the tabs only with a workspace).
 *
 * A page can split the main area into panes with `data-region` of their own (the explorer's tree and detail, the SQL
 * editor's editor, parameters and result: 08 section 7, "Explorer/Editor/Ergebnis", TD-020). F6 then stops at each
 * pane instead of at the main area: the stops are the areas that hold no other area. A focus outside every stop (on
 * the main area itself after "Go to …", or on a page's toolbar between two panes) moves to the next stop after it in
 * the page's order, or to the last one before it.
 *
 * An area whose markup sets `tabindex="-1"` (the main area, the status bar) is focused itself. Any other area gets
 * the focus on its entry (an element with `data-region-entry`, or the first control inside it: the SQL text in the
 * editor's shadow root, the first parameter value, the tree's current item, the result grid), else on its current
 * item (the selected tab, the active navigation link), else on its first control, else on itself. A pane focused
 * itself because it was empty gets a `tabindex="-1"` only until the focus leaves it (C-1 of `stu-052b-review.md`), so
 * the next F6 finds the grid or the selected tab that appeared meanwhile.
 */

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';

export function regions(root: ParentNode = document): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>('[data-region]')];
}

/** The areas F6 stops at: those without an area inside them, in the page's order. */
export function stops(root: ParentNode = document): HTMLElement[] {
  return regions(root).filter((region) => region.querySelector('[data-region]') === null);
}

function target(region: HTMLElement): HTMLElement {
  if (region.getAttribute('tabindex') === '-1' && !('regionTabindex' in region.dataset)) {
    return region;
  }
  for (const entry of region.querySelectorAll<HTMLElement>('[data-region-entry]')) {
    // The SQL editor lives in a shadow root (its styles under the CSP, STU-012): look inside it too, for the text
    // first (a panel or gutter control of CodeMirror could come before it; I-2 of stu-052b-review.md).
    const shadow = entry.shadowRoot;
    const inner = entry.matches(FOCUSABLE)
      ? entry
      : (shadow?.querySelector<HTMLElement>('[contenteditable="true"]') ??
        shadow?.querySelector<HTMLElement>(FOCUSABLE) ??
        entry.querySelector<HTMLElement>(FOCUSABLE));
    if (inner !== null) return inner;
  }
  const current = region.querySelector<HTMLElement>(
    '[aria-current="page"], [role="tab"][aria-selected="true"]',
  );
  return current ?? region.querySelector<HTMLElement>(FOCUSABLE) ?? region;
}

function follows(node: Node, reference: Node): boolean {
  return (reference.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
}

/** Move the focus to the next (+1) or the previous (-1) area; returns the area's name. */
export function moveRegion(direction: 1 | -1, root: ParentNode = document): string | null {
  const all = stops(root);
  if (all.length === 0) return null;
  const active = document.activeElement;
  const index = all.findIndex((region) => region === active || region.contains(active));
  let next: HTMLElement | undefined;
  if (index !== -1) {
    next = all[(index + direction + all.length) % all.length];
  } else if (active === null || active === document.body) {
    next = direction === 1 ? all[0] : all[all.length - 1];
  } else if (direction === 1) {
    // The areas inside the focused element (the main area's panes) follow it in the page's order too.
    next = all.find((region) => follows(region, active)) ?? all[0];
  } else {
    next = all.findLast((region) => !follows(region, active)) ?? all[all.length - 1];
  }
  if (next === undefined) return null;
  focusArea(next);
  return next.dataset.region ?? null;
}

function focusArea(region: HTMLElement): void {
  const element = target(region);
  if (element === region) {
    // A pane without a control is focusable only while it has the focus: its tabindex goes when the focus leaves,
    // so it never stands in for the entry that appears later (C-1 of stu-052b-review.md).
    const added = !region.hasAttribute('tabindex');
    if (added) {
      region.tabIndex = -1;
      region.dataset.regionTabindex = '';
    }
    // An area has no focus ring of its own (tabindex -1); a keyboard move shows one until the focus leaves.
    region.dataset.regionFocus = '';
    region.addEventListener(
      'blur',
      () => {
        delete region.dataset.regionFocus;
        if (added) {
          region.removeAttribute('tabindex');
          delete region.dataset.regionTabindex;
        }
      },
      { once: true },
    );
  }
  element.focus();
}

/**
 * Focus one area by name, with the ring (I-2 of stu-052-td021-review.md, STU-053): after "Go to …" in the palette the
 * focus moves to the new page's main area, so a screen reader starts reading there instead of at the old opener.
 */
export function focusRegion(name: string, root: ParentNode = document): boolean {
  const region = regions(root).find((area) => area.dataset.region === name);
  if (region === undefined) return false;
  focusArea(region);
  return true;
}
