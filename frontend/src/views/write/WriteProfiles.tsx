/**
 * Write and admin profiles (STU-XW-1, E-X8). Shown only while the server says a write area is on (its flag is set
 * by the owner of the Studio process in `write-areas.toml`; G-S2, G-S3, G-S4): with every area off, the section is
 * not rendered at all, and the server refuses its routes regardless.
 *
 * Creating a profile shows, at that moment, that Studio relies on the environment the user gives the profile and that
 * write credentials of a production database do not belong in Studio (review N-6 of PR #109); the profile is sent only
 * after the user confirmed that note, and the server refuses it without the confirmation.
 *
 * Below the form, the I-109 j confirmation of each PostgreSQL write or admin secret (STU-XW-4, D-15) and the audit
 * log's state (STU-XW-2): verified or not, and a warning while records are pending.
 */
import { useEffect, useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import type { Problem } from '../../api/problem';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import {
  ALL_OFF,
  createWriteProfile,
  fetchWriteAreas,
  openKinds,
  type WriteAreas,
  type WriteKind,
} from '../../api/write';
import { Button } from '../../components/ui/Button';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { problemText } from '../connections/messages';
import styles from '../Views.module.css';
import { AuditStatus } from './AuditStatus';
import { SecretConfirmations } from './SecretConfirmations';

export function WriteProfilesSection({ fetcher = fetch }: { fetcher?: typeof fetch }) {
  const [areas, setAreas] = useState<WriteAreas>(ALL_OFF);
  useEffect(() => {
    let alive = true;
    void fetchWriteAreas(fetcher).then((next) => {
      if (alive) setAreas(next);
    });
    return () => {
      alive = false;
    };
  }, [fetcher]);
  const kinds = openKinds(areas);
  if (kinds.length === 0) return null;
  return <WriteProfileForm fetcher={fetcher} kinds={kinds} />;
}

function WriteProfileForm({ fetcher, kinds }: { fetcher: typeof fetch; kinds: WriteKind[] }) {
  const { t } = useTranslation();
  const id = useId();
  const noteId = `${id}-note`;
  const [reads, setReads] = useState<ProfileItem[]>([]);
  const [digest, setDigest] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [kind, setKind] = useState<WriteKind>(kinds[0] ?? 'write');
  const [readProfile, setReadProfile] = useState('');
  const [user, setUser] = useState('');
  const [requirepeer, setRequirepeer] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    void fetchProfiles(fetcher).then((list) => {
      if (!alive || list === null) return;
      // Only the user's own profiles that are not PROD can be coupled (K-1, E-X7).
      setReads(list.profiles.filter((p) => p.source === 'user' && p.environment !== 'prod'));
      setDigest(list.userFile.digest);
    });
    return () => {
      alive = false;
    };
  }, [fetcher, saved]);

  const selected = reads.find((p) => p.name === readProfile);
  // libpq checks the server's OS user only on a Unix socket (a host that is a folder): N-2 of PR #116.
  const socket = selected?.engine === 'postgresql' && (selected.host ?? '').startsWith('/');
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!confirmed) return;
    setProblem(null);
    const result = await createWriteProfile(
      fetcher,
      {
        name,
        kind,
        read_profile: readProfile,
        ...(selected?.engine === 'postgresql' ? { user, password: 'keyring:' } : {}),
        ...(socket && requirepeer !== '' ? { requirepeer } : {}),
      },
      digest,
      confirmed,
    );
    if (result.ok) {
      setSaved((value) => !value);
      setName('');
      setConfirmed(false);
    } else {
      setProblem(result.problem);
    }
  };

  return (
    <section className={styles.card} aria-labelledby={`${id}-title`} data-testid="write-profiles">
      <h2 id={`${id}-title`} className={styles.cardTitle}>
        {t('write.title')}
      </h2>
      <p className={styles.lead}>{t('write.lead')}</p>
      <form className={connectionStyles.form} onSubmit={(event) => void submit(event)}>
        <Field label={t('write.name')}>
          {(props) => (
            <input
              {...props}
              className={connectionStyles.input}
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          )}
        </Field>
        <Field label={t('write.kind')}>
          {(props) => (
            <select
              {...props}
              className={connectionStyles.input}
              value={kind}
              onChange={(event) => setKind(event.target.value as WriteKind)}
            >
              {kinds.map((value) => (
                <option key={value} value={value}>
                  {t(`write.kinds.${value}`)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t('write.readProfile')} hint={t('write.readProfileHint')}>
          {(props) => (
            <select
              {...props}
              className={connectionStyles.input}
              value={readProfile}
              onChange={(event) => setReadProfile(event.target.value)}
              required
            >
              <option value="">{t('write.choose')}</option>
              {reads.map((p) => (
                <option key={p.id} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          )}
        </Field>
        {selected?.engine === 'postgresql' ? (
          <Field label={t('write.role')} hint={t('write.roleHint')}>
            {(props) => (
              <input
                {...props}
                className={connectionStyles.input}
                value={user}
                onChange={(event) => setUser(event.target.value)}
                required
              />
            )}
          </Field>
        ) : null}
        {socket ? (
          <Field label={t('write.requirepeer')} hint={t('write.requirepeerHint')}>
            {(props) => (
              <input
                {...props}
                className={connectionStyles.input}
                value={requirepeer}
                onChange={(event) => setRequirepeer(event.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
            )}
          </Field>
        ) : null}
        <div role="note" id={noteId} className={connectionStyles.warning}>
          <p>{t('write.prodNote')}</p>
          <label>
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
              aria-describedby={noteId}
            />{' '}
            {t('write.prodConfirm')}
          </label>
        </div>
        {problem !== null ? (
          <p role="alert" className={connectionStyles.error}>
            {problemText(t, problem)}
          </p>
        ) : null}
        <div className={connectionStyles.actions}>
          <Button type="submit" variant="primary" disabled={!confirmed}>
            {t('write.create')}
          </Button>
        </div>
      </form>
      <SecretConfirmations fetcher={fetcher} refresh={saved} />
      <AuditStatus fetcher={fetcher} />
    </section>
  );
}
