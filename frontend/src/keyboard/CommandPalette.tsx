/**
 * The command palette (08 sections 1, 7 and 8, STU-052): ⌘K / Ctrl+K opens it, typing filters, ↑ ↓ Home End move,
 * Enter runs, Escape closes. It is a modal native `<dialog>` like the other dialogs (no Radix: the CSP refuses its
 * injected styles, docs/architecture/frontend.md) with the WAI-ARIA combobox pattern: the focus stays in the input,
 * `aria-activedescendant` names the active command, and the focus returns to the opener when it closes.
 */
import { Search } from 'lucide-react';
import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import { useTranslation } from 'react-i18next';

import clsx from '../lib/clsx';
import { filterCommands, type Command } from './commands';
import { Keys } from './Keys';
import { useKeyboardPlatform } from './platform';
import styles from './CommandPalette.module.css';

export function CommandPalette({
  open,
  commands,
  onClose,
}: {
  open: boolean;
  commands: Command[];
  onClose: () => void;
}) {
  return open ? <OpenPalette commands={commands} onClose={onClose} /> : null;
}

function OpenPalette({ commands, onClose }: { commands: Command[]; onClose: () => void }) {
  const { t } = useTranslation();
  const platform = useKeyboardPlatform();
  const ref = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const titleId = useId();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);

  const shown = useMemo(() => filterCommands(commands, query), [commands, query]);
  const index = Math.min(active, Math.max(shown.length - 1, 0));
  const current = shown[index];
  const optionId = (command: Command) => `${listId}-${command.id}`;

  useLayoutEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return undefined;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    input.current?.focus();
    const onNativeCancel = (event: Event) => {
      event.preventDefault();
      close.current();
    };
    dialog.addEventListener('cancel', onNativeCancel);
    return () => {
      dialog.removeEventListener('cancel', onNativeCancel);
      if (typeof dialog.close === 'function' && dialog.open) dialog.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  useEffect(() => {
    if (current === undefined) return;
    document.getElementById(optionId(current))?.scrollIntoView?.({ block: 'nearest' });
    // optionId only depends on the stable list ID.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  const run = (command: Command) => {
    // Close first: the focus goes back to the opener, then the command may move it (a view, the help dialog).
    onClose();
    command.run();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDialogElement>) => {
    const last = shown.length - 1;
    const moves: Record<string, number> = {
      ArrowDown: index >= last ? 0 : index + 1,
      ArrowUp: index <= 0 ? last : index - 1,
      Home: 0,
      End: last,
    };
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onClose();
    } else if (event.key in moves && shown.length > 0) {
      event.preventDefault();
      setActive(moves[event.key] ?? 0);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (current !== undefined) run(current);
    } else if (event.key === 'Tab') {
      // The input is the only stop in the palette (combobox pattern).
      event.preventDefault();
    }
  };

  return (
    <dialog
      ref={ref}
      className={styles.palette}
      aria-modal="true"
      aria-labelledby={titleId}
      onKeyDown={onKeyDown}
      data-testid="command-palette"
    >
      <h2 id={titleId} className="tb-sr-only">
        {t('keyboard.palette.title')}
      </h2>
      <div className={styles.search}>
        <Search aria-hidden="true" className={styles.icon} />
        <input
          ref={input}
          className={styles.input}
          type="text"
          role="combobox"
          aria-label={t('keyboard.palette.search')}
          aria-expanded="true"
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={current === undefined ? undefined : optionId(current)}
          placeholder={t('keyboard.palette.placeholder')}
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
        />
      </div>
      <ul
        id={listId}
        role="listbox"
        aria-label={t('keyboard.palette.list')}
        className={styles.list}
      >
        {shown.map((command, position) => (
          // The input owns the keyboard (combobox pattern); a click runs the command as Enter does.
          // eslint-disable-next-line jsx-a11y/click-events-have-key-events
          <li
            key={command.id}
            id={optionId(command)}
            role="option"
            aria-selected={position === index}
            className={clsx(styles.option, position === index && styles.active)}
            onMouseMove={() => setActive(position)}
            onClick={() => run(command)}
            data-command={command.id}
          >
            <span className={styles.label}>{command.label}</span>
            {command.current ? (
              <span className={styles.current}>{t('keyboard.palette.current')}</span>
            ) : null}
            {command.shortcut ? <Keys shortcut={command.shortcut} platform={platform} /> : null}
          </li>
        ))}
      </ul>
      {shown.length === 0 ? <p className={styles.empty}>{t('keyboard.palette.noMatch')}</p> : null}
      <p role="status" className="tb-sr-only">
        {t('keyboard.palette.count', { count: shown.length })}
      </p>
      <p className={styles.hint} aria-hidden="true">
        {t('keyboard.palette.hint')}
      </p>
    </dialog>
  );
}
