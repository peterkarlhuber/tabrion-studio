import { useKeyNames } from './platform';
import { shortcutKeys, shortcutText, type Platform, type Shortcut } from './shortcuts';
import styles from './Keys.module.css';

/** A shortcut as `<kbd>` keys; screen readers read the whole shortcut once ("Ctrl+K", "⌘K"). */
export function Keys({ shortcut, platform }: { shortcut: Shortcut; platform: Platform }) {
  const names = useKeyNames();
  return (
    <span className={styles.keys}>
      <span aria-hidden="true" className={styles.visual}>
        {shortcutKeys(shortcut, platform, names).map((key) => (
          <kbd key={key} className={styles.key}>
            {key}
          </kbd>
        ))}
      </span>
      <span className="tb-sr-only">{shortcutText(shortcut, platform, names)}</span>
    </span>
  );
}
