/**
 * The SQL text editor (03 section 2 C, 08 section 4.4): CodeMirror 6 with `@codemirror/lang-sql` in the dialect of
 * the tab's connection, through `@uiw/react-codemirror` (STU-ADR-0004, the pattern of nodestro-studio).
 *
 * **Content-Security-Policy.** CodeMirror writes its styles with `style-mod`, which adds a `<style>` element to a
 * document; the CSP of STU-003 (`style-src 'self'`) refuses that, and Studio does not weaken its CSP. In a shadow root
 * `style-mod` uses a constructed style sheet (`adoptedStyleSheets`) instead, which the CSP does not govern, so the
 * editor lives in a shadow root of its own. Its look comes from the `--tb-` tokens, which the shadow root inherits.
 * `e2e/editor-backend.spec.ts` checks the page under the real CSP without a violation.
 *
 * **Keyboard.** Tab moves the focus on (no tab trap, WCAG 2.1.2); ⌘↵/Ctrl+Enter runs, ⇧⌘↵/Shift+Ctrl+Enter runs the
 * script, ⌘./Ctrl+. cancels (08 section 7).
 */
import { PostgreSQL, sql as sqlLanguage, SQLite } from '@codemirror/lang-sql';
import { defaultHighlightStyle, HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { Prec } from '@codemirror/state';
import { EditorView, keymap } from '@codemirror/view';
import { useCodeMirror } from '@uiw/react-codemirror';
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import type { Dialect } from './sqlScan';
import styles from './Editor.module.css';

export interface SqlEditorHandle {
  /** The selection (from <= to) and the cursor position. */
  selection(): { from: number; to: number; head: number };
  /** Select a range, scroll it into view and focus the editor (a message's "go to statement"). */
  select(from: number, to: number): void;
  focus(): void;
}

export interface SqlEditorProps {
  value: string;
  onChange: (value: string) => void;
  dialect: Dialect;
  /** The accessible name of the text area. */
  label: string;
  /** The id of the element that describes the shortcuts (`aria-describedby` of the text area). */
  describedBy?: string;
  onRun: () => void;
  onRunScript: () => void;
  onCancel: () => void;
  /** Focus the text as soon as the editor exists (a new tab). */
  focusOnCreate?: boolean;
}

const theme = EditorView.theme({
  '&': {
    height: '100%',
    color: 'var(--tb-color-text)',
    backgroundColor: 'var(--tb-color-bg)',
    fontSize: 'var(--tb-text-md)',
  },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': { fontFamily: 'var(--tb-font-mono)', lineHeight: '1.5' },
  '.cm-content': { caretColor: 'var(--tb-color-text)' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--tb-color-text)' },
  '.cm-gutters': {
    backgroundColor: 'var(--tb-color-surface)',
    color: 'var(--tb-color-text-muted)',
    borderRight: '1px solid var(--tb-color-border)',
  },
  '.cm-activeLine': { backgroundColor: 'var(--tb-color-surface-hover)' },
  '.cm-activeLineGutter': {
    backgroundColor: 'var(--tb-color-surface-hover)',
    color: 'var(--tb-color-text)',
  },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': {
    backgroundColor: 'var(--tb-color-primary-subtle)',
  },
  '.cm-matchingBracket': {
    backgroundColor: 'var(--tb-color-primary-subtle)',
    outline: '1px solid var(--tb-color-primary)',
  },
  '.cm-tooltip': {
    backgroundColor: 'var(--tb-color-surface)',
    color: 'var(--tb-color-text)',
    border: '1px solid var(--tb-color-border-strong)',
  },
  '.cm-tooltip-autocomplete ul li[aria-selected]': {
    backgroundColor: 'var(--tb-color-selected)',
    color: 'var(--tb-color-text)',
  },
  '.cm-panels': { backgroundColor: 'var(--tb-color-surface)', color: 'var(--tb-color-text)' },
  '.cm-textfield, .cm-button': { color: 'var(--tb-color-text)' },
});

/**
 * Syntax colors from the tokens, so both themes keep their contrast: CodeMirror's default style with its fixed
 * colors replaced (keywords in the accent, strings green, comments muted, everything else in the text color).
 */
const COLORS: Record<string, string> = {
  '#708': 'var(--tb-color-primary)',
  '#a11': 'var(--tb-color-success)',
  '#940': 'var(--tb-color-text-muted)',
};
const highlight = syntaxHighlighting(
  HighlightStyle.define(
    defaultHighlightStyle.specs.map((spec) =>
      spec.color === undefined
        ? spec
        : { ...spec, color: COLORS[String(spec.color)] ?? 'var(--tb-color-text)' },
    ),
  ),
);

export const SqlEditor = forwardRef<SqlEditorHandle, SqlEditorProps>(function SqlEditor(
  {
    value,
    onChange,
    dialect,
    label,
    describedBy,
    onRun,
    onRunScript,
    onCancel,
    focusOnCreate = false,
  },
  ref,
) {
  const host = useRef<HTMLDivElement>(null);
  const [mount, setMount] = useState<{ root: ShadowRoot; element: HTMLDivElement } | null>(null);
  const handlers = useRef({ onRun, onRunScript, onCancel, onChange });
  useEffect(() => {
    handlers.current = { onRun, onRunScript, onCancel, onChange };
  }, [onRun, onRunScript, onCancel, onChange]);

  useLayoutEffect(() => {
    const element = host.current;
    if (element === null) return;
    const root = element.shadowRoot ?? element.attachShadow({ mode: 'open' });
    let inner = root.querySelector<HTMLDivElement>('div[data-editor]');
    if (inner === null) {
      inner = document.createElement('div');
      inner.dataset.editor = '';
      inner.style.height = '100%';
      root.append(inner);
    }
    setMount({ root, element: inner });
  }, []);

  const extensions = useMemo(
    () => [
      sqlLanguage({ dialect: dialect === 'sqlite' ? SQLite : PostgreSQL, upperCaseKeywords: true }),
      Prec.highest(
        keymap.of([
          { key: 'Mod-Enter', run: () => (handlers.current.onRun(), true) },
          { key: 'Shift-Mod-Enter', run: () => (handlers.current.onRunScript(), true) },
          { key: 'Mod-.', run: () => (handlers.current.onCancel(), true) },
        ]),
      ),
      EditorView.contentAttributes.of({
        'aria-label': label,
        ...(describedBy ? { 'aria-describedby': describedBy } : {}),
        'aria-multiline': 'true',
        spellcheck: 'false',
        autocapitalize: 'off',
        autocorrect: 'off',
      }),
      theme,
      highlight,
    ],
    [dialect, label, describedBy],
  );

  const stableChange = useMemo(() => (next: string) => handlers.current.onChange(next), []);

  const { view, setContainer } = useCodeMirror({
    value,
    onChange: stableChange,
    extensions,
    root: mount?.root,
    theme: 'none',
    autoFocus: focusOnCreate,
    height: '100%',
    indentWithTab: false,
    basicSetup: {
      foldGutter: false,
      highlightActiveLineGutter: true,
      syntaxHighlighting: false,
      tabSize: 2,
    },
  });

  useEffect(() => {
    if (mount !== null) setContainer(mount.element);
  }, [mount, setContainer]);

  useImperativeHandle(
    ref,
    () => ({
      selection() {
        const main = view?.state.selection.main;
        if (main === undefined) return { from: 0, to: 0, head: 0 };
        return { from: main.from, to: main.to, head: main.head };
      },
      select(from: number, to: number) {
        if (view === undefined) return;
        const length = view.state.doc.length;
        view.dispatch({
          selection: { anchor: Math.min(from, length), head: Math.min(to, length) },
          scrollIntoView: true,
        });
        view.focus();
      },
      focus() {
        view?.focus();
      },
    }),
    [view],
  );

  return <div ref={host} className={styles.editorHost} data-testid="sql-editor" />;
});
