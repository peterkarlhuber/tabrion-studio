/**
 * Help: keyboard shortcuts (⌘/ or Ctrl+/, 08 section 7). It lists the shortcuts that work now, with the keys of this
 * platform; the SQL editor's shortcuts are added when the editor arrives (STU-011, STU-012).
 */
import { useTranslation } from 'react-i18next';

import { Dialog } from '../components/ui/Dialog';
import { Keys } from './Keys';
import { useKeyboardPlatform } from './platform';
import { SHORTCUTS, type ShortcutId } from './shortcuts';
import styles from './ShortcutHelp.module.css';

const ROWS: ShortcutId[] = ['palette', 'help', 'nextRegion', 'previousRegion', 'explorerFilter'];

export function ShortcutHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const platform = useKeyboardPlatform();
  return (
    <Dialog
      open={open}
      title={t('keyboard.help.title')}
      description={<p>{t('keyboard.help.lead')}</p>}
      actions={[{ id: 'close', label: t('keyboard.help.close'), onSelect: onClose }]}
      initialFocus="close"
      onCancel={onClose}
      testId="shortcut-help"
    >
      <table className={styles.table}>
        <caption className="tb-sr-only">{t('keyboard.help.caption')}</caption>
        <thead>
          <tr>
            <th scope="col">{t('keyboard.help.action')}</th>
            <th scope="col">{t('keyboard.help.keys')}</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map((id) => (
            <tr key={id}>
              <td>{t(`keyboard.shortcuts.${id}`)}</td>
              <td>
                <Keys shortcut={SHORTCUTS[id]} platform={platform} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={styles.note}>{t('keyboard.help.later')}</p>
    </Dialog>
  );
}
