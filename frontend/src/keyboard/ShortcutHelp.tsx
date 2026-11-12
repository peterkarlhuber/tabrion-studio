/**
 * Help: keyboard shortcuts (⌘/ or Ctrl+/, 08 section 7). It lists the shortcuts that work now, with the keys of this
 * platform: those of the whole page, those of the SQL editor's page (STU-012) and those of the explorer's (STU-010).
 */
import { useTranslation } from 'react-i18next';

import { Dialog } from '../components/ui/Dialog';
import { Keys } from './Keys';
import { useKeyboardPlatform } from './platform';
import {
  EDITOR_SHORTCUTS,
  EXPLORER_SHORTCUTS,
  PAGE_SHORTCUTS,
  SHORTCUTS,
  type ShortcutId,
} from './shortcuts';
import styles from './ShortcutHelp.module.css';

const GROUPS: [string, ShortcutId[]][] = [
  ['pageGroup', PAGE_SHORTCUTS],
  ['editorGroup', EDITOR_SHORTCUTS],
  ['explorerGroup', EXPLORER_SHORTCUTS],
];

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
        {GROUPS.map(([group, ids]) => (
          <tbody key={group}>
            <tr>
              <th scope="colgroup" colSpan={2} className={styles.group}>
                {t(`keyboard.help.${group}`)}
              </th>
            </tr>
            {ids.map((id) => (
              <tr key={id}>
                <td>{t(`keyboard.shortcuts.${id}`)}</td>
                <td>
                  <Keys shortcut={SHORTCUTS[id]} platform={platform} />
                </td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
      <p className={styles.note}>{t('keyboard.help.later')}</p>
    </Dialog>
  );
}
