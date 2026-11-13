/**
 * STU-054 "de/en vollständig" (09; 08 section 0 and E-S3: MVP and 1.0 ship German and English complete), the source
 * side of it; `i18n.test.ts` checks the two catalogs against each other. Here, the page's source against the
 * catalogs:
 *
 * - every key the source names (in a `t(...)` call, a `<Trans i18nKey>` or a key-shaped string in a key table)
 *   exists in en.json, as itself or as its plural forms; a key built from a template has at least one key under its
 *   fixed prefix;
 * - no visible text is written into the markup: a JSX text, or the fixed text of an expression in a JSX child or in
 *   `aria-label`, `aria-description`, `aria-roledescription`, `aria-valuetext`, `title`, `placeholder`, `alt` or
 *   `label` (strings, the static parts of templates, both sides of a condition, `??` and `||`), is either from the
 *   catalog or on the short list below of names that are the same in every language (SQL keywords, pragma names);
 *   not seen: a module constant or a state value rendered later, and component text props under other names
 *   (recorded in TD-060);
 * - every plural has its `_one` and `_other` form in both languages.
 *
 * The page's source files are read as text through Vite's import.meta.glob and parsed with the TypeScript compiler
 * (a dev dependency already), so the check sees the syntax, not a pattern over the text.
 */
import ts from 'typescript';

import de from './locales/de.json';
import en from './locales/en.json';

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'string') out[path] = value;
    else Object.assign(out, flatten(value, path));
  }
  return out;
}

const english = flatten(en);
const german = flatten(de);
const GROUPS = new Set(Object.keys(en));
const PLURALS = ['_zero', '_one', '_two', '_few', '_many', '_other'];

/** The page's own source: no tests, no e2e harness, no test setup. */
const SOURCES = Object.entries(
  import.meta.glob<string>(
    ['../**/*.{ts,tsx}', '!../**/*.test.{ts,tsx}', '!../harness/**', '!../test/**'],
    {
      query: '?raw',
      import: 'default',
      eager: true,
    },
  ),
).map(([path, text]) => ({
  path,
  file: ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX),
}));

function known(key: string): boolean {
  return key in english || PLURALS.some((suffix) => `${key}${suffix}` in english);
}

/** The string literals a `t(...)` argument can evaluate to (through `a ? 'x' : 'y'`, `a ?? 'x'` and parentheses). */
function literals(node: ts.Expression): { text: string; template: boolean }[] {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
    return [{ text: node.text, template: false }];
  if (ts.isTemplateExpression(node)) return [{ text: node.head.text, template: true }];
  if (ts.isParenthesizedExpression(node)) return literals(node.expression);
  if (ts.isConditionalExpression(node))
    return [...literals(node.whenTrue), ...literals(node.whenFalse)];
  if (
    ts.isBinaryExpression(node) &&
    node.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken
  )
    return [...literals(node.left), ...literals(node.right)];
  return [];
}

function isTranslate(node: ts.CallExpression): boolean {
  const callee = node.expression;
  return (
    (ts.isIdentifier(callee) && callee.text === 't') ||
    (ts.isPropertyAccessExpression(callee) && callee.name.text === 't')
  );
}

function walk(node: ts.Node, visit: (node: ts.Node) => void): void {
  visit(node);
  ts.forEachChild(node, (child) => walk(child, visit));
}

function where(file: ts.SourceFile, node: ts.Node): string {
  const { line } = file.getLineAndCharacterOfPosition(node.getStart(file));
  return `${file.fileName.replace(/^\.\.\//, 'src/')}:${line + 1}`;
}

/** Texts that are the same in every language, each where the markup shows it. */
const NOT_TRANSLATED = new Set([
  // SQLite table options and pragma names, shown as code (explorer's SQLite tab, migration status).
  'STRICT',
  'WITHOUT ROWID',
  'journal_mode',
  'user_version',
  'page_size',
  'page_count',
  'foreign_keys',
  // The PostgreSQL policy clauses as column headers (STU-022 Policies tab).
  'USING',
  'WITH CHECK',
  // The boolean parameter values, as SQL writes them.
  'true',
  'false',
  // The URL scheme as the PostgreSQL field's placeholder.
  'postgresql://',
  // SQL's NULL as a value, and the key badges (hidden from screen readers, which read the translated text instead).
  'NULL',
  'PK',
  'FK',
  // The ER diagram's badge for a column that is a primary and a foreign key (hidden like PK and FK).
  'PF',
  // The placeholder of a JSON parameter: a JSON example, the same in every language.
  '{"key": "value"}',
]);
/** Strings shaped like a key that are not keys: the settings.toml fields the settings form points its issues at. */
const NOT_KEYS = new Set([
  'settings.limits.max_rows',
  'settings.limits.statement_timeout_ms',
  'settings.history.retention_days',
  'history.retention_days',
]);
const TEXT_ATTRIBUTES = new Set([
  'aria-label',
  'aria-description',
  'aria-roledescription',
  'aria-valuetext',
  'title',
  'placeholder',
  'alt',
  'label',
]);

/**
 * The fixed text an expression can show: a string, every static part of a template, and both sides of a condition,
 * `??` or `||` (review L-1 of PR #84). A call (`t(...)`), a name or a property is not text the markup writes.
 */
function staticTexts(node: ts.Expression): string[] {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return [node.text];
  if (ts.isTemplateExpression(node))
    return [node.head.text, ...node.templateSpans.map((span) => span.literal.text)];
  if (ts.isParenthesizedExpression(node)) return staticTexts(node.expression);
  if (ts.isConditionalExpression(node))
    return [...staticTexts(node.whenTrue), ...staticTexts(node.whenFalse)];
  if (
    ts.isBinaryExpression(node) &&
    [ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.BarBarToken].includes(
      node.operatorToken.kind,
    )
  )
    return [...staticTexts(node.left), ...staticTexts(node.right)];
  return [];
}

describe('the page source against the catalogs (STU-054)', () => {
  it('reads the page source', () => {
    expect(SOURCES.length).toBeGreaterThan(50);
    expect(SOURCES.some(({ path }) => path.endsWith('views/explorer/ExplorerView.tsx'))).toBe(true);
  });

  it('names only keys that exist in the catalogs', () => {
    const missing: string[] = [];
    let seen = 0;
    for (const { file } of SOURCES) {
      walk(file, (node) => {
        let found: { text: string; template: boolean }[] = [];
        if (ts.isCallExpression(node) && isTranslate(node) && node.arguments[0] !== undefined)
          found = literals(node.arguments[0]);
        else if (
          ts.isJsxAttribute(node) &&
          node.name.getText(file) === 'i18nKey' &&
          node.initializer !== undefined
        ) {
          const value = ts.isJsxExpression(node.initializer)
            ? node.initializer.expression
            : node.initializer;
          if (value !== undefined) found = literals(value);
        } else if (
          (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) &&
          /^[a-z][A-Za-z]*(\.[A-Za-z0-9_]+)+$/.test(node.text) &&
          GROUPS.has(node.text.split('.')[0] as string) &&
          !NOT_KEYS.has(node.text)
        )
          // A key in a table (`{ key: 'explorer.kind.table' }`), passed to t() later.
          found = [{ text: node.text, template: false }];
        for (const { text, template } of found) {
          seen += 1;
          const ok = template
            ? Object.keys(english).some((key) => key.startsWith(text))
            : known(text) || Object.keys(english).some((key) => key.startsWith(`${text}.`));
          if (!ok) missing.push(`${where(file, node)} ${text}${template ? '…' : ''}`);
        }
      });
    }
    expect(seen).toBeGreaterThan(500);
    // A literal inside t(...) is seen twice (the call and the key-shaped string); report it once.
    expect([...new Set(missing)]).toEqual([]);
  });

  it('writes no visible text into the markup', () => {
    const written: string[] = [];
    for (const { file } of SOURCES) {
      walk(file, (node) => {
        let texts: string[] = [];
        if (ts.isJsxText(node)) texts = [node.text];
        else if (
          ts.isJsxExpression(node) &&
          node.expression !== undefined &&
          !ts.isJsxAttribute(node.parent)
        )
          texts = staticTexts(node.expression);
        else if (
          ts.isJsxAttribute(node) &&
          TEXT_ATTRIBUTES.has(node.name.getText(file)) &&
          node.initializer !== undefined
        ) {
          const value = ts.isJsxExpression(node.initializer)
            ? node.initializer.expression
            : node.initializer;
          if (value !== undefined) texts = staticTexts(value);
        }
        for (const raw of texts) {
          const text = raw.trim();
          if (/\p{L}{2,}/u.test(text) && !NOT_TRANSLATED.has(text))
            written.push(`${where(file, node)} ${JSON.stringify(text)}`);
        }
      });
    }
    expect(written).toEqual([]);
  });

  it('sees text in conditions, fallbacks, templates and the aria text attributes', () => {
    // The shapes review L-1 of PR #84 found unseen, written into a small component of their own.
    const sample = ts.createSourceFile(
      'sample.tsx',
      [
        'export const A = ({ x }: { x: boolean }) => (',
        '  <div aria-description="Several heads" aria-label={`Close the ${x} box`}>',
        "    {x ? 'Several heads found' : null}",
        "    {x ?? 'Nothing here'}",
        '  </div>',
        ');',
      ].join('\n'),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    const seen: string[] = [];
    walk(sample, (node) => {
      if (
        ts.isJsxExpression(node) &&
        node.expression !== undefined &&
        !ts.isJsxAttribute(node.parent)
      )
        seen.push(...staticTexts(node.expression));
      if (ts.isJsxAttribute(node) && node.initializer !== undefined) {
        const value = ts.isJsxExpression(node.initializer)
          ? node.initializer.expression
          : node.initializer;
        if (value !== undefined && TEXT_ATTRIBUTES.has(node.name.getText(sample)))
          seen.push(...staticTexts(value));
      }
    });
    expect(seen.map((text) => text.trim())).toEqual([
      'Several heads',
      'Close the',
      'box',
      'Several heads found',
      'Nothing here',
    ]);
  });

  it('keeps the lists of untranslated names and of key-shaped non-keys current', () => {
    const used = new Set<string>();
    for (const { file } of SOURCES) {
      walk(file, (node) => {
        if (ts.isJsxText(node)) used.add(node.text.trim());
        if (ts.isStringLiteral(node)) used.add(node.text.trim());
      });
    }
    expect([...NOT_TRANSLATED, ...NOT_KEYS].filter((text) => !used.has(text))).toEqual([]);
  });

  it('has both plural forms of every plural in both languages', () => {
    // English and German have the forms "one" and "other"; a key ending in either is a plural.
    const plurals = new Set(
      [...Object.keys(english), ...Object.keys(german)]
        .filter((key) => /_(one|other)$/.test(key))
        .map((key) => key.replace(/_(one|other)$/, '')),
    );
    expect(plurals.size).toBeGreaterThan(0);
    const missing: string[] = [];
    for (const key of plurals) {
      for (const [name, catalog] of [
        ['en', english],
        ['de', german],
      ] as const) {
        for (const suffix of ['_one', '_other'])
          if (!(`${key}${suffix}` in catalog)) missing.push(`${name}: ${key}${suffix}`);
      }
    }
    expect(missing).toEqual([]);
  });
});
