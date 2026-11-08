/// <reference types="node" />
// Contrast test of the design tokens in both themes (STU-005 acceptance; 08 section 8: text 4.5:1, UI elements
// 3:1). Pattern: axentra-studio src/styles/tokens.test.ts. The rendered pages are checked again in a real browser by
// the axe color-contrast rule in e2e/a11y.spec.ts, in light and dark.
// Read from disk: Vitest's CSS handling turns `?raw` CSS imports into empty strings.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { contrastRatio } from '../lib/contrast';

const tokensCss = readFileSync(resolve(process.cwd(), 'src/styles/tokens.css'), 'utf8');

/** Extract `--name: #hex;` declarations from one CSS block. */
function parseBlock(css: string, selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`Selector not found: ${selector}`);
  const end = css.indexOf('\n}', start);
  const block = css.slice(start, end);
  const tokens: Record<string, string> = {};
  for (const match of block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) {
    tokens[match[1] as string] = match[2] as string;
  }
  return tokens;
}

const light = parseBlock(tokensCss, ':root');
const darkOnly = parseBlock(tokensCss, ":root[data-theme='dark']");
const dark = { ...light, ...darkOnly };

const TEXT_AA = 4.5;
const UI_AA = 3; // non-text contrast (WCAG 1.4.11): focus ring, control borders, the PROD frame

/** [foreground, background, minimum ratio] */
const PAIRS: [string, string, number][] = [
  ['tb-color-text', 'tb-color-bg', TEXT_AA],
  ['tb-color-text', 'tb-color-surface', TEXT_AA],
  ['tb-color-text', 'tb-color-surface-2', TEXT_AA],
  ['tb-color-text', 'tb-color-surface-hover', TEXT_AA],
  ['tb-color-text', 'tb-color-chrome', TEXT_AA],
  ['tb-color-text', 'tb-color-selected', TEXT_AA],
  ['tb-color-heading', 'tb-color-surface', TEXT_AA],
  ['tb-color-heading', 'tb-color-bg', TEXT_AA],
  ['tb-color-text-muted', 'tb-color-surface', TEXT_AA],
  ['tb-color-text-muted', 'tb-color-bg', TEXT_AA],
  ['tb-color-text-muted', 'tb-color-surface-2', TEXT_AA],
  ['tb-color-text-muted', 'tb-color-chrome', TEXT_AA],
  ['tb-color-text-subtle', 'tb-color-surface', TEXT_AA],
  ['tb-color-text-subtle', 'tb-color-bg', TEXT_AA],
  ['tb-color-link', 'tb-color-surface', TEXT_AA],
  ['tb-color-link', 'tb-color-bg', TEXT_AA],
  ['tb-color-link', 'tb-color-chrome', TEXT_AA],
  ['tb-color-link', 'tb-color-primary-subtle', TEXT_AA],
  ['tb-color-link', 'tb-color-selected', TEXT_AA],
  ['tb-color-on-primary', 'tb-color-primary', TEXT_AA],
  ['tb-color-on-primary', 'tb-color-primary-hover', TEXT_AA],
  ['tb-color-on-danger', 'tb-color-danger', TEXT_AA],
  ['tb-color-on-danger', 'tb-color-danger-hover', TEXT_AA],
  ['tb-color-success', 'tb-color-success-bg', TEXT_AA],
  ['tb-color-warning', 'tb-color-warning-bg', TEXT_AA],
  ['tb-color-danger', 'tb-color-danger-bg', TEXT_AA],
  ['tb-color-info', 'tb-color-info-bg', TEXT_AA],
  ['tb-color-neutral', 'tb-color-neutral-bg', TEXT_AA],
  ['tb-color-danger', 'tb-color-surface', TEXT_AA],
  ['tb-color-text', 'tb-color-code-bg', TEXT_AA],
  ['tb-null', 'tb-color-surface', TEXT_AA],
  ['tb-color-text', 'tb-diff-added', TEXT_AA],
  ['tb-color-text', 'tb-diff-changed', TEXT_AA],
  ['tb-color-text', 'tb-diff-removed', TEXT_AA],
  // Environment badges: the text carries the meaning (WCAG 1.4.1), so it needs text contrast.
  ['tb-env-dev', 'tb-env-dev-bg', TEXT_AA],
  ['tb-env-test', 'tb-env-test-bg', TEXT_AA],
  ['tb-env-prod', 'tb-env-prod-bg', TEXT_AA],
  ['tb-env-prod-bg', 'tb-color-chrome', UI_AA],
  ['tb-env-prod-bg', 'tb-color-surface', UI_AA],
  ['tb-env-prod-frame', 'tb-color-chrome', UI_AA],
  ['tb-env-prod-frame', 'tb-color-bg', UI_AA],
  // Focus ring (2 px, 08 section 9) and control borders.
  ['tb-color-focus', 'tb-color-bg', UI_AA],
  ['tb-color-focus', 'tb-color-surface', UI_AA],
  ['tb-color-focus', 'tb-color-surface-2', UI_AA],
  ['tb-color-focus', 'tb-color-chrome', UI_AA],
  ['tb-color-control-border', 'tb-color-surface', UI_AA],
  ['tb-color-control-border', 'tb-color-bg', UI_AA],
  ['tb-color-primary', 'tb-color-surface', UI_AA],
];

describe.each([
  ['light', light],
  ['dark', dark],
])('%s theme tokens', (_name, tokens) => {
  it.each(PAIRS)('%s on %s meets %d:1', (fg, bg, minimum) => {
    const foreground = tokens[fg];
    const background = tokens[bg];
    expect(foreground, `missing token --${fg}`).toBeDefined();
    expect(background, `missing token --${bg}`).toBeDefined();
    expect(contrastRatio(foreground as string, background as string)).toBeGreaterThanOrEqual(
      minimum,
    );
  });
});

describe('the dark theme', () => {
  it('redefines every colour token of the light theme', () => {
    const colours = Object.keys(light).filter((name) => /^tb-(color|env|null|diff)/.test(name));
    expect(colours.length).toBeGreaterThan(40);
    expect(colours.filter((name) => !(name in darkOnly))).toEqual([]);
  });
});

describe('contrastRatio', () => {
  it('matches known WCAG values', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(contrastRatio('#ffffff', '#ffffff')).toBeCloseTo(1, 5);
    expect(contrastRatio('#767676', '#ffffff')).toBeGreaterThan(4.5);
  });

  it('rejects malformed colors', () => {
    expect(() => contrastRatio('red', '#ffffff')).toThrow();
  });
});
