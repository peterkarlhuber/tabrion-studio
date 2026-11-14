/**
 * The audit log of write and admin actions (STU-XW-2): whether it verifies, whether records are pending (review N-4:
 * a record after an action that could not be written is kept and shown here, never lost silently), and the newest
 * records. Read only; shown inside the write profile section, so only while a write area is on.
 */
import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchAudit, type AuditRecord, type AuditState } from '../../api/write';
import connectionStyles from '../connections/Connections.module.css';
import explorerStyles from '../explorer/Explorer.module.css';
import styles from '../Views.module.css';

function field(record: AuditRecord, key: string): string {
  const value = record.fields[key];
  return typeof value === 'string' ? value : '';
}

export function AuditStatus({ fetcher }: { fetcher: typeof fetch }) {
  const { t } = useTranslation();
  const id = useId();
  const [state, setState] = useState<AuditState | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    void fetchAudit(fetcher).then((next) => {
      if (alive) setState(next);
    });
    return () => {
      alive = false;
    };
  }, [fetcher]);
  if (state === undefined) return null;
  const verified = state?.verification?.ok === true;
  return (
    <section aria-labelledby={`${id}-title`} data-testid="write-audit">
      <h3 id={`${id}-title`} className={styles.cardTitle}>
        {t('write.audit.title')}
      </h3>
      {state === null ? (
        <p role="alert" className={connectionStyles.error}>
          {t('write.audit.unreadable')}
        </p>
      ) : verified && state.available ? (
        <p>{t('write.audit.verified', { count: state.verification?.records ?? 0 })}</p>
      ) : (
        <p role="alert" className={connectionStyles.error}>
          {t('write.audit.broken')}
        </p>
      )}
      {state !== null && state.pending > 0 ? (
        <div role="alert" className={connectionStyles.warning}>
          <p>{t('write.audit.pending', { count: state.pending })}</p>
        </div>
      ) : null}
      {state !== null && state.records.length > 0 ? (
        <div className={explorerStyles.gridScroll}>
          <table className={explorerStyles.grid}>
            <thead>
              <tr>
                <th scope="col">{t('write.audit.time')}</th>
                <th scope="col">{t('write.audit.event')}</th>
                <th scope="col">{t('write.audit.profile')}</th>
                <th scope="col">{t('write.audit.result')}</th>
              </tr>
            </thead>
            <tbody>
              {state.records.map((record) => (
                <tr key={record.seq}>
                  <td>{record.time}</td>
                  <td>{record.event}</td>
                  <td>{field(record, 'profile')}</td>
                  <td>{field(record, 'result') || field(record, 'reason')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
