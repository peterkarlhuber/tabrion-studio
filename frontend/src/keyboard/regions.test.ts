/**
 * F6 over panes inside the main area (08 section 7, "Explorer/Editor/Ergebnis", TD-020). The shell's own order is
 * pinned in Keyboard.test.tsx; the real explorer and SQL editor pages run it keyboard-only in e2e/keyboard.spec.ts.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { focusRegion, moveRegion, stops } from './regions';

function page(main: string): void {
  document.body.innerHTML = `
    <header data-region="header"><a href="/">Home</a></header>
    <main id="main" tabindex="-1" data-region="main">${main}</main>
    <footer tabindex="-1" data-region="statusbar">Status</footer>`;
}

const EDITOR = `
  <h1>SQL editor</h1>
  <div role="group"><button id="run">Run</button></div>
  <div data-region="editor"><div id="sql" contenteditable="true">select 1</div></div>
  <section data-region="parameters"><input id="param" /></section>
  <section data-region="result">
    <div role="tablist"><button role="tab" aria-selected="true" id="result-tab">Result</button></div>
    <div data-region-entry=""><div role="grid" tabindex="0" id="grid"></div></div>
  </section>`;

function focused(): string {
  return (
    (document.activeElement as HTMLElement | null)?.id || (document.activeElement?.tagName ?? '')
  );
}

beforeEach(() => {
  document.body.innerHTML = '';
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('F6 over the panes of the main area', () => {
  it('stops at the panes instead of the main area', () => {
    page(EDITOR);
    expect(stops().map((region) => region.dataset.region)).toEqual([
      'header',
      'editor',
      'parameters',
      'result',
      'statusbar',
    ]);
  });

  it('keeps the main area as a stop on a page without panes', () => {
    page('<h1>Settings</h1>');
    expect(stops().map((region) => region.dataset.region)).toEqual(['header', 'main', 'statusbar']);
  });

  it('steps through the panes onto their entries and around', () => {
    page(EDITOR);
    document.querySelector<HTMLElement>('header a')?.focus();
    expect(moveRegion(1)).toBe('editor');
    expect(focused()).toBe('sql'); // the SQL text (contenteditable)
    expect(moveRegion(1)).toBe('parameters');
    expect(focused()).toBe('param');
    expect(moveRegion(1)).toBe('result');
    expect(focused()).toBe('grid'); // the entry wins over the selected result tab
    expect(moveRegion(1)).toBe('statusbar');
    expect(moveRegion(1)).toBe('header');
    expect(moveRegion(-1)).toBe('statusbar');
    expect(moveRegion(-1)).toBe('result');
    expect(moveRegion(-1)).toBe('parameters');
  });

  it('falls back to the current item when the entry holds no control yet', () => {
    page(EDITOR.replace('<div role="grid" tabindex="0" id="grid"></div>', ''));
    document.getElementById('param')?.focus();
    expect(moveRegion(1)).toBe('result');
    expect(focused()).toBe('result-tab');
  });

  it('moves from the main area itself to its first pane, or back to the area before it', () => {
    page(EDITOR);
    expect(focusRegion('main')).toBe(true); // "Go to …" in the palette
    expect(focused()).toBe('main');
    expect(moveRegion(1)).toBe('editor');
    focusRegion('main');
    expect(moveRegion(-1)).toBe('header');
  });

  it('moves from a control between two panes to the pane after it, or the one before it', () => {
    page(EDITOR.replace('<h1>SQL editor</h1>', '<div data-region="tree"><a href="#t">t</a></div>'));
    const run = document.getElementById('run');
    run?.focus();
    expect(moveRegion(1)).toBe('editor');
    run?.focus();
    expect(moveRegion(-1)).toBe('tree');
  });

  it('starts at the first or the last stop when nothing has the focus', () => {
    page(EDITOR);
    expect(moveRegion(1)).toBe('header');
    (document.activeElement as HTMLElement).blur();
    expect(moveRegion(-1)).toBe('statusbar');
  });

  it('focuses a pane without a control itself and shows the ring until it loses the focus', () => {
    page(
      '<section data-region="detail"><h2>Nothing chosen</h2></section><div data-region="tree"></div>',
    );
    document.querySelector<HTMLElement>('header a')?.focus();
    moveRegion(1);
    const detail = document.querySelector<HTMLElement>('[data-region="detail"]');
    expect(document.activeElement).toBe(detail);
    expect(detail?.tabIndex).toBe(-1);
    expect(detail).toHaveAttribute('data-region-focus');
    moveRegion(1);
    expect(detail).not.toHaveAttribute('data-region-focus');
  });
});

describe('an entry with a shadow root', () => {
  it('focuses the control inside the shadow root (the SQL editor)', () => {
    page('<div data-region="editor"><div id="host" data-region-entry=""></div></div>');
    const host = document.getElementById('host');
    const root = host?.attachShadow({ mode: 'open' });
    const text = document.createElement('div');
    text.setAttribute('contenteditable', 'true');
    text.id = 'cm';
    root?.append(text);
    document.querySelector<HTMLElement>('header a')?.focus();
    expect(moveRegion(1)).toBe('editor');
    expect(root?.activeElement).toBe(text);
    expect(document.activeElement).toBe(host); // the page sees the host, so F6 knows the area
    expect(moveRegion(1)).toBe('statusbar');
  });
});
