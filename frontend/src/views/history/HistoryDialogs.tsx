/** The confirmation before the query history is deleted (STU-020; 08 section 6: the safe option has the focus). */
import { useTranslation } from 'react-i18next';

import { Dialog } from '../../components/ui/Dialog';

export function DeleteHistoryDialog({
  profileName,
  onDelete,
  onCancel,
}: {
  /** One profile's entries (its name, escaped); every entry when absent. */
  profileName?: string;
  onDelete: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open
      testId="dialog-delete-history"
      title={
        profileName === undefined
          ? t('history.deleteAllTitle')
          : t('history.deleteProfileTitle', {
              name: profileName,
              interpolation: { escapeValue: false },
            })
      }
      description={t(profileName === undefined ? 'history.deleteAllBody' : 'history.deleteBody')}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[
        { id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel },
        { id: 'delete', label: t('history.deleteConfirm'), variant: 'danger', onSelect: onDelete },
      ]}
    />
  );
}
