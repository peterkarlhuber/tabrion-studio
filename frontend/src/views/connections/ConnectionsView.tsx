/**
 * The connection manager (STU-008, 08 section 4.2): the profiles grouped by project and user with a filter on the
 * left, the selected profile's details on the right, and the "New connection" assistant.
 *
 * Only the user's own profiles can be created, edited and deleted; project profiles come from the project's
 * `.tabrion/studio.toml`, which Studio never writes. Names and paths come from files, so they are shown escaped
 * (`visible`). Testing and connecting need the connection registry (STU-009) and are shown disabled until then.
 */
import { Database, Plus } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { Problem } from '../../api/problem';
import {
  deleteProfile,
  fetchKeyringAvailable,
  fetchProfiles,
  forgetSecret,
  storeSecret,
  type ProfileItem,
  type ProfileList,
} from '../../api/profiles';
import { EnvBadge } from '../../components/status/Status';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import { MAX_ENTRIES, visible } from '../../lib/visible';
import { DeleteProfileDialog, PasswordDialog } from './ConnectionDialogs';
import { ConnectionWizard, type WizardResult } from './ConnectionWizard';
import { problemText } from './messages';
import styles from './Connections.module.css';
import viewStyles from '../Views.module.css';

type Open =
  | { kind: 'none' }
  | { kind: 'wizard'; item: ProfileItem | null }
  | { kind: 'delete'; item: ProfileItem; problem: Problem | null }
  | { kind: 'password'; item: ProfileItem; problem: Problem | null };

const NO_ESCAPE = { interpolation: { escapeValue: false } };

function SecretLine({ item }: { item: ProfileItem }) {
  const { t } = useTranslation();
  const ref = item.password;
  let source: string;
  if (ref === null) source = t('connections.secret.none');
  else if (ref.source === 'keyring') source = t('connections.secret.keyring');
  else if (ref.source === 'env')
    source = t('connections.secret.env', { name: visible(ref.target), ...NO_ESCAPE });
  else source = t('connections.secret.file', { path: visible(ref.target), ...NO_ESCAPE });
  const state =
    item.secret.hasSecret === true
      ? t('connections.secret.set')
      : item.secret.hasSecret === false
        ? t('connections.secret.missing')
        : t('connections.secret.unknown');
  return (
    <>
      {source} ({state})
    </>
  );
}

function Details({
  item,
  onEdit,
  onDelete,
  onPassword,
  onForget,
}: {
  item: ProfileItem;
  onEdit: () => void;
  onDelete: () => void;
  onPassword: () => void;
  onForget: () => void;
}) {
  const { t } = useTranslation();
  const name = visible(item.name);
  const storable = item.password === null || item.password.source === 'keyring';
  return (
    <section
      className={styles.panel}
      aria-label={t('connections.detail.label', { name, ...NO_ESCAPE })}
      data-testid="connection-detail"
    >
      <div className={styles.detailHeader}>
        <h2 className={styles.detailTitle}>{name}</h2>
        <EnvBadge environment={item.environment} />
      </div>
      <dl className={styles.details}>
        <dt>{t('connections.detail.engine')}</dt>
        <dd>{t(`connections.engine.${item.engine}`)}</dd>
        {item.engine === 'sqlite' ? (
          <>
            <dt>{t('connections.detail.file')}</dt>
            <dd>
              <code>{visible(item.path ?? '')}</code>
            </dd>
          </>
        ) : (
          <>
            <dt>{t('connections.detail.server')}</dt>
            <dd>
              <code>
                {visible(item.host ?? '')}:{item.port ?? 5432}
              </code>
            </dd>
            <dt>{t('connections.detail.database')}</dt>
            <dd>
              <code>{visible(item.database ?? '')}</code>
            </dd>
            {item.user !== undefined ? (
              <>
                <dt>{t('connections.detail.user')}</dt>
                <dd>
                  <code>{visible(item.user)}</code>
                </dd>
              </>
            ) : null}
            <dt>{t('connections.detail.tls')}</dt>
            <dd>{item.sslmode ?? t('connections.detail.tlsAuto')}</dd>
            <dt>{t('connections.detail.password')}</dt>
            <dd>
              <SecretLine item={item} />
            </dd>
          </>
        )}
        <dt>{t('connections.detail.access')}</dt>
        <dd>
          {t('connections.detail.readOnly')}
          {item.read_only_role ? ` · ${t('connections.detail.readOnlyRole')}` : ''}
        </dd>
      </dl>
      <div className={styles.actions}>
        <Button disabled>{t('connections.detail.connect')}</Button>
        <Button disabled>{t('connections.detail.test')}</Button>
        {item.source === 'user' ? (
          <>
            <Button onClick={onEdit}>{t('connections.detail.edit')}</Button>
            <Button variant="dangerOutline" onClick={onDelete}>
              {t('connections.detail.delete')}
            </Button>
          </>
        ) : null}
        {item.engine === 'postgresql' && storable ? (
          <>
            <Button onClick={onPassword}>{t('connections.detail.setPassword')}</Button>
            <Button variant="ghost" onClick={onForget}>
              {t('connections.detail.forgetPassword')}
            </Button>
          </>
        ) : null}
      </div>
      <p className={styles.hint}>{t('connections.detail.needsRegistry')}</p>
      {item.source === 'project' ? (
        <p className={styles.hint}>{t('connections.projectHint')}</p>
      ) : null}
    </section>
  );
}

export function ConnectionsView({ fetcher }: { fetcher: typeof fetch }) {
  const { t } = useTranslation();
  const [list, setList] = useState<ProfileList | null | 'loading'>('loading');
  const [keyring, setKeyring] = useState(false);
  const [filter, setFilter] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState<Open>({ kind: 'none' });
  const [status, setStatus] = useState<{ text: string; problem: boolean } | null>(null);

  const load = useCallback(async () => {
    const [profiles, available] = await Promise.all([
      fetchProfiles(fetcher),
      fetchKeyringAvailable(fetcher),
    ]);
    setList(profiles);
    setKeyring(available);
    return profiles;
  }, [fetcher]);

  useEffect(() => {
    let active = true;
    void Promise.all([fetchProfiles(fetcher), fetchKeyringAvailable(fetcher)]).then(
      ([profiles, available]) => {
        if (!active) return;
        setList(profiles);
        setKeyring(available);
      },
    );
    return () => {
      active = false;
    };
  }, [fetcher]);

  const profiles = useMemo(
    () => (list !== null && list !== 'loading' ? list.profiles : []),
    [list],
  );
  const shown = profiles.filter((p) => p.name.toLowerCase().includes(filter.trim().toLowerCase()));
  const current = profiles.find((p) => p.id === selected) ?? shown[0] ?? null;
  const userFile =
    list !== null && list !== 'loading' ? list.userFile : { digest: null, handWritten: false };
  const userIssues = list !== null && list !== 'loading' ? list.issues : [];
  const close = () => setOpen({ kind: 'none' });

  const saved = (result: WizardResult) => {
    close();
    const name = visible(result.name);
    setStatus(
      result.secretProblem === null
        ? { text: t('connections.saved', { name, ...NO_ESCAPE }), problem: false }
        : {
            text: t('connections.secretNotSaved', {
              name,
              reason: problemText(t, result.secretProblem),
              ...NO_ESCAPE,
            }),
            problem: true,
          },
    );
    setSelected(`user:${result.name}`);
    void load();
  };

  const remove = async (item: ProfileItem) => {
    const result = await deleteProfile(fetcher, item.id, userFile.digest, userFile.handWritten);
    if (!result.ok) {
      setOpen({ kind: 'delete', item, problem: result.problem });
      if (result.problem.code === 'studio.config_stale') void load();
      return;
    }
    close();
    setSelected(null);
    setStatus({
      text: t('connections.deleted', { name: visible(item.name), ...NO_ESCAPE }),
      problem: false,
    });
    void load();
  };

  const password = async (item: ProfileItem, secret: string, store: 'keyring' | 'session') => {
    const result = await storeSecret(fetcher, item.id, secret, store);
    if (!result.ok) {
      setOpen({ kind: 'password', item, problem: result.problem });
      return;
    }
    close();
    setStatus({
      text: t('connections.secretSaved', { name: visible(item.name), ...NO_ESCAPE }),
      problem: false,
    });
    void load();
  };

  const forget = async (item: ProfileItem) => {
    const result = await forgetSecret(fetcher, item.id);
    setStatus(
      result.ok
        ? {
            text: t('connections.secretRemoved', { name: visible(item.name), ...NO_ESCAPE }),
            problem: false,
          }
        : { text: problemText(t, result.problem), problem: true },
    );
    void load();
  };

  const groups: { id: string; title: string; items: ProfileItem[] }[] = [
    {
      id: 'project',
      title: t('connections.groupProject'),
      items: shown.filter((p) => p.source === 'project'),
    },
    {
      id: 'user',
      title: t('connections.groupUser'),
      items: shown.filter((p) => p.source === 'user'),
    },
  ];

  return (
    <div className={viewStyles.page}>
      <div className={viewStyles.pageHeader}>
        <h1 className={viewStyles.title}>{t('connections.title')}</h1>
        <p className={viewStyles.lead}>{t('connections.lead')}</p>
      </div>
      <div className={styles.toolbar}>
        <input
          type="search"
          className={`${styles.input} ${styles.search}`}
          aria-label={t('connections.search')}
          placeholder={t('connections.search')}
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <Button
          variant="primary"
          icon={<Plus />}
          onClick={() => setOpen({ kind: 'wizard', item: null })}
        >
          {t('connections.new')}
        </Button>
      </div>
      <div aria-live="polite">
        {status !== null ? (
          <p
            className={status.problem ? styles.problem : styles.status}
            data-testid="connections-status"
          >
            {status.text}
          </p>
        ) : null}
      </div>
      {userIssues.some((issue) => issue.severity !== 'warning') ? (
        <div className={styles.warning} data-testid="profiles-issues">
          <p>{t('connections.userIssues')}</p>
          <ul className={styles.issues}>
            {userIssues.slice(0, MAX_ENTRIES).map((issue, index) => (
              <li key={index}>
                <code>{visible(String(issue.pointer))}</code>{' '}
                <code>{visible(String(issue.code))}</code>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {list === 'loading' ? (
        <p className={styles.hint}>{t('connections.loading')}</p>
      ) : list === null ? (
        <p className={styles.problem}>{t('connections.unavailable')}</p>
      ) : profiles.length === 0 ? (
        <EmptyState
          icon={<Database />}
          title={t('connections.emptyTitle')}
          description={<p>{t('connections.emptyBody')}</p>}
        />
      ) : (
        <div className={styles.layout}>
          <nav className={styles.panel} aria-label={t('connections.list')}>
            {shown.length === 0 ? <p className={styles.hint}>{t('connections.noMatch')}</p> : null}
            {groups
              .filter((group) => group.items.length > 0)
              .map((group) => (
                <div key={group.id} className={styles.group}>
                  <h2 className={styles.groupTitle} id={`group-${group.id}`}>
                    {group.title}
                  </h2>
                  <ul className={styles.items} aria-labelledby={`group-${group.id}`}>
                    {group.items.map((item) => (
                      <li key={item.id}>
                        <button
                          type="button"
                          className={styles.item}
                          aria-pressed={current?.id === item.id}
                          data-testid="connection-item"
                          onClick={() => setSelected(item.id)}
                        >
                          <span className={styles.itemName}>{visible(item.name)}</span>
                          <span className={styles.itemEngine}>
                            {t(`connections.engine.${item.engine}`)}
                          </span>
                          <EnvBadge environment={item.environment} size="sm" />
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
          </nav>
          {current !== null ? (
            <Details
              item={current}
              onEdit={() => setOpen({ kind: 'wizard', item: current })}
              onDelete={() => setOpen({ kind: 'delete', item: current, problem: null })}
              onPassword={() => setOpen({ kind: 'password', item: current, problem: null })}
              onForget={() => void forget(current)}
            />
          ) : null}
        </div>
      )}
      {open.kind === 'wizard' ? (
        <ConnectionWizard
          fetcher={fetcher}
          item={open.item}
          digest={userFile.digest}
          handWritten={userFile.handWritten}
          keyringAvailable={keyring}
          onSaved={saved}
          onStale={() => void load()}
          onCancel={close}
        />
      ) : null}
      {open.kind === 'delete' ? (
        <DeleteProfileDialog
          name={open.item.name}
          problem={open.problem}
          handWritten={userFile.handWritten}
          onDelete={() => void remove(open.item)}
          onCancel={close}
        />
      ) : null}
      {open.kind === 'password' ? (
        <PasswordDialog
          name={open.item.name}
          keyringAvailable={keyring}
          keyringAllowed={open.item.password?.source === 'keyring'}
          problem={open.problem}
          onSave={(secret, store) => void password(open.item, secret, store)}
          onCancel={close}
        />
      ) : null}
    </div>
  );
}
