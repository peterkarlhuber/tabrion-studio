/**
 * The confirmation dialogs of STU-051 (08 section 6: D-01, D-02, D-06, D-08). Each one opens with the focus on the
 * option that changes nothing, Escape means that option too, and a destructive action is never the default. The
 * owners that open them arrive with their work packages: D-01 with project trust (STU-006), D-02 with connecting
 * (STU-008), D-06 with the SQL editor (STU-012) and D-08 with running queries (STU-011). D-03..D-05 belong to the Data
 * Editor (STU-026, locked behind G-S2) and D-07 to Studio 1.0; they are not built.
 */
import { useTranslation } from 'react-i18next';

import { Dialog } from '../ui/Dialog';
import styles from './StudioDialogs.module.css';

/** D-01: project profiles seen for the first time or changed. Default: "Don't load". */
export function TrustProjectDialog({
  open,
  file,
  connections,
  hosts,
  files,
  changed = false,
  onTrust,
  onReject,
}: {
  open: boolean;
  file: string;
  connections: string[];
  hosts: string[];
  files: string[];
  changed?: boolean;
  onTrust: () => void;
  onReject: () => void;
}) {
  const { t } = useTranslation();
  const none = t('dialogs.trustProject.none');
  return (
    <Dialog
      open={open}
      testId="dialog-trust-project"
      title={t(changed ? 'dialogs.trustProject.titleChanged' : 'dialogs.trustProject.title')}
      description={t('dialogs.trustProject.body', {
        file,
        count: connections.length,
        names: connections.join(', '),
      })}
      initialFocus="reject"
      onCancel={onReject}
      actions={[
        { id: 'reject', label: t('dialogs.trustProject.reject'), onSelect: onReject },
        {
          id: 'trust',
          label: t('dialogs.trustProject.trust'),
          variant: 'primary',
          onSelect: onTrust,
        },
      ]}
    >
      <dl className={styles.details}>
        <dt>{t('dialogs.trustProject.hosts')}</dt>
        <dd>{hosts.length > 0 ? <code>{hosts.join(', ')}</code> : none}</dd>
        <dt>{t('dialogs.trustProject.files')}</dt>
        <dd>{files.length > 0 ? <code>{files.join(', ')}</code> : none}</dd>
      </dl>
    </Dialog>
  );
}

/** D-02: opening a PROD connection for the first time in a session. Default: "Cancel". */
export function ProdConnectDialog({
  open,
  name,
  timeoutSeconds,
  onConnect,
  onCancel,
}: {
  open: boolean;
  name: string;
  timeoutSeconds: number;
  onConnect: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={open}
      testId="dialog-prod-connect"
      title={t('dialogs.prodConnect.title')}
      description={t('dialogs.prodConnect.body', { name, seconds: timeoutSeconds })}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[
        { id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel },
        {
          id: 'connect',
          label: t('dialogs.prodConnect.connect'),
          variant: 'primary',
          onSelect: onConnect,
        },
      ]}
    />
  );
}

/** D-06: closing a changed script. Default: "Cancel"; "Discard" is destructive. */
export function UnsavedScriptDialog({
  open,
  name,
  onSave,
  onDiscard,
  onCancel,
}: {
  open: boolean;
  name: string;
  onSave: () => void;
  onDiscard: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={open}
      testId="dialog-unsaved-script"
      title={t('dialogs.unsavedScript.title')}
      description={t('dialogs.unsavedScript.body', { name })}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[
        { id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel },
        {
          id: 'discard',
          label: t('dialogs.unsavedScript.discard'),
          variant: 'dangerOutline',
          onSelect: onDiscard,
        },
        {
          id: 'save',
          label: t('dialogs.unsavedScript.save'),
          variant: 'primary',
          onSelect: onSave,
        },
      ]}
    />
  );
}

/** D-08: quitting Studio while queries run. Default: "Cancel"; "Quit" cancels the queries. */
export function QuitDialog({
  open,
  running,
  onQuit,
  onCancel,
}: {
  open: boolean;
  running: number;
  onQuit: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={open}
      testId="dialog-quit"
      title={t('dialogs.quit.title', { count: running })}
      description={t('dialogs.quit.body')}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[
        { id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel },
        { id: 'quit', label: t('dialogs.quit.quit'), variant: 'danger', onSelect: onQuit },
      ]}
    />
  );
}
