/** Deleting a user profile and entering a password (STU-008). The safe option has the default focus (08 section 6). */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { Problem } from '../../api/problem';
import { Dialog } from '../../components/ui/Dialog';
import { visible } from '../../lib/visible';
import { Field } from './Field';
import { problemText } from './messages';
import styles from './Connections.module.css';

export function DeleteProfileDialog({
  name,
  problem,
  handWritten,
  onDelete,
  onCancel,
}: {
  name: string;
  problem: Problem | null;
  /** The file was written by hand: deleting rewrites it without its comments, so the dialog says so. */
  handWritten: boolean;
  onDelete: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open
      testId="dialog-delete-profile"
      title={t('connections.delete.title', {
        name: visible(name),
        interpolation: { escapeValue: false },
      })}
      description={t('connections.delete.body')}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[
        { id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel },
        {
          id: 'delete',
          label: t('connections.delete.confirm'),
          variant: 'danger',
          onSelect: onDelete,
        },
      ]}
    >
      {handWritten ? <p className={styles.warning}>{t('connections.wizard.handWritten')}</p> : null}
      {problem !== null ? (
        <p className={styles.problem} role="alert">
          {problemText(t, problem)}
        </p>
      ) : null}
    </Dialog>
  );
}

export function PasswordDialog({
  name,
  keyringAvailable,
  keyringAllowed,
  problem,
  onSave,
  onCancel,
}: {
  name: string;
  keyringAvailable: boolean;
  /** Only a `keyring:` profile can keep its password in the keyring. */
  keyringAllowed: boolean;
  problem: Problem | null;
  onSave: (secret: string, store: 'keyring' | 'session') => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const canKeyring = keyringAvailable && keyringAllowed;
  const [secret, setSecret] = useState('');
  const [store, setStore] = useState<'keyring' | 'session'>(canKeyring ? 'keyring' : 'session');
  return (
    <Dialog
      open
      testId="dialog-password"
      title={t('connections.password.title', {
        name: visible(name),
        interpolation: { escapeValue: false },
      })}
      description={t('connections.password.body')}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[
        { id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel },
        {
          id: 'save',
          label: t('connections.password.save'),
          variant: 'primary',
          onSelect: () => onSave(secret, store),
        },
      ]}
    >
      <div className={styles.form}>
        <Field label={t('connections.wizard.password')}>
          {(props) => (
            <input
              {...props}
              className={styles.input}
              type="password"
              value={secret}
              autoComplete="new-password"
              onChange={(event) => setSecret(event.target.value)}
            />
          )}
        </Field>
        {keyringAllowed ? (
          <fieldset className={styles.fieldset}>
            <legend className={styles.label}>{t('connections.password.store')}</legend>
            <div className={styles.choicesColumn}>
              <label className={styles.choice}>
                <input
                  type="radio"
                  name="store"
                  value="keyring"
                  checked={store === 'keyring'}
                  disabled={!canKeyring}
                  onChange={() => setStore('keyring')}
                />
                {t(
                  keyringAvailable
                    ? 'connections.password.keyring'
                    : 'connections.password.keyringMissing',
                )}
              </label>
              <label className={styles.choice}>
                <input
                  type="radio"
                  name="store"
                  value="session"
                  checked={store === 'session'}
                  onChange={() => setStore('session')}
                />
                {t('connections.password.session')}
              </label>
            </div>
          </fieldset>
        ) : null}
        {problem !== null ? (
          <p className={styles.problem} role="alert">
            {problemText(t, problem)}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
