/**
 * The page without a session (STU-003): the sign-in link was used, expired or invalid, the tab has no session, the
 * server does not answer, or the session ended while the tab was open (review S-1: the server restarted or a program
 * on another local port replaced the session cookie). In every case the way back is to run `tabrion-studio` again.
 */
import { useEffect } from 'react';
import { Trans, useTranslation } from 'react-i18next';

import type { AppSession } from '../App';
import { Lockup, TRANS_COMPONENTS } from '../views/Views';
import styles from './SessionNotice.module.css';

export function SessionNotice({ state }: { state: Exclude<AppSession, 'ok'> }) {
  const { t } = useTranslation();
  const title = t(state === 'ended' ? 'session.endedTitle' : 'session.title');
  useEffect(() => {
    document.title = t('app.title', { page: title });
  }, [t, title]);
  return (
    <main className={styles.notice}>
      <div className={styles.card}>
        <Lockup className={styles.lockup} />
        <h1 className={styles.title}>{title}</h1>
        <p role="alert" data-testid="session-status" className={styles.text}>
          <Trans i18nKey={`session.${state}`} components={TRANS_COMPONENTS} />
        </p>
      </div>
    </main>
  );
}
