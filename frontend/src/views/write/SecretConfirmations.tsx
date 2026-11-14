/**
 * The I-109 j confirmation of a write or admin secret (STU-XW-4, dialog D-15, threat TX-07). Studio sends the secret
 * of a PostgreSQL write or admin profile only to a server the user confirmed together with the secret's reference.
 * The server reads the pair from the user's own file (never from this page), answers with a confirmation hash, and
 * takes the confirmation only with that hash and the host typed exactly; it refuses every unlock of an unconfirmed
 * pair regardless of what this page shows. SQLite write profiles have no secret and are not listed here.
 */
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import type { Problem } from '../../api/problem';
import {
  confirmSecretPair,
  fetchWriteProfiles,
  previewSecretPair,
  type SecretPairPreview,
  type WriteProfileItem,
} from '../../api/write';
import { Button } from '../../components/ui/Button';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { problemText } from '../connections/messages';
import styles from '../Views.module.css';

export function SecretConfirmations({
  fetcher,
  refresh,
}: {
  fetcher: typeof fetch;
  refresh?: unknown;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [items, setItems] = useState<WriteProfileItem[]>([]);
  const [open, setOpen] = useState<{ item: WriteProfileItem; preview: SecretPairPreview } | null>(
    null,
  );
  const [typed, setTyped] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [round, setRound] = useState(0);

  useEffect(() => {
    let alive = true;
    void fetchWriteProfiles(fetcher).then((list) => {
      if (alive) setItems(list.filter((item) => item.confirmed !== null));
    });
    return () => {
      alive = false;
    };
  }, [fetcher, refresh, round]);

  const review = useCallback(
    async (item: WriteProfileItem) => {
      setProblem(null);
      setTyped('');
      const result = await previewSecretPair(fetcher, item.id);
      if (result.ok) setOpen({ item, preview: result.value });
      else setProblem(result.problem);
    },
    [fetcher],
  );

  const confirm = async (event: FormEvent) => {
    event.preventDefault();
    if (open === null) return;
    const result = await confirmSecretPair(fetcher, open.preview.confirmation_hash, typed);
    if (result.ok) {
      setOpen(null);
      setTyped('');
      setRound((value) => value + 1);
    } else {
      setProblem(result.problem);
    }
  };

  if (items.length === 0) return null;
  const pair = open?.preview.pair;
  return (
    <section aria-labelledby={`${id}-title`} data-testid="secret-confirmations">
      <h3 id={`${id}-title`} className={styles.cardTitle}>
        {t('write.pair.title')}
      </h3>
      <p className={styles.lead}>{t('write.pair.lead')}</p>
      <ul>
        {items.map((item) => (
          <li key={item.id}>
            {item.name}: {item.confirmed ? t('write.pair.confirmed') : t('write.pair.unconfirmed')}{' '}
            <Button type="button" onClick={() => void review(item)}>
              {t('write.pair.review')}
            </Button>
          </li>
        ))}
      </ul>
      {open !== null && pair !== undefined ? (
        <form
          className={connectionStyles.form}
          onSubmit={(event) => void confirm(event)}
          aria-labelledby={`${id}-dialog`}
          data-testid="secret-pair"
        >
          <h4 id={`${id}-dialog`}>{t('write.pair.dialogTitle', { name: open.item.name })}</h4>
          <dl>
            <dt>{t('write.pair.server')}</dt>
            <dd>
              {pair.host}:{pair.port}
              {pair.database !== null ? ` / ${pair.database}` : ''}
            </dd>
            <dt>{t('write.pair.tls')}</dt>
            <dd>{pair.sslmode ?? '-'}</dd>
            <dt>{t('write.pair.role')}</dt>
            <dd>{pair.role ?? '-'}</dd>
            <dt>{t('write.pair.reference')}</dt>
            <dd>
              {t(`write.pair.sources.${pair.reference.source}`, {
                defaultValue: pair.reference.source,
              })}
              {pair.reference.target !== '' ? ` ${pair.reference.target}` : ''}
            </dd>
          </dl>
          {pair.foreign ? (
            <p role="note" className={connectionStyles.warning}>
              {t('write.pair.foreign')}
            </p>
          ) : null}
          <Field label={t('write.pair.typeHost', { host: open.preview.confirm_text })}>
            {(props) => (
              <input
                {...props}
                className={connectionStyles.input}
                value={typed}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setTyped(event.target.value)}
              />
            )}
          </Field>
          <div className={connectionStyles.actions}>
            <Button type="button" onClick={() => setOpen(null)}>
              {t('write.pair.cancel')}
            </Button>
            <Button type="submit" variant="primary" disabled={typed !== open.preview.confirm_text}>
              {t('write.pair.confirm')}
            </Button>
          </div>
        </form>
      ) : null}
      {problem !== null ? (
        <p role="alert" className={connectionStyles.error}>
          {problemText(t, problem)}
        </p>
      ) : null}
    </section>
  );
}
