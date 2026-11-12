/**
 * The connection manager (STU-008, 08 section 4.2): the profiles grouped by project and user with a filter on the
 * left, the selected profile's details on the right, and the "New connection" assistant.
 *
 * Only the user's own profiles can be created, edited and deleted; project profiles come from the project's
 * `.tabrion/studio.toml`, which Studio never writes. Names and paths come from files, so they are shown escaped
 * (`visible`).
 *
 * "Test connection" connects, reads the facts of 03 section 2 A and closes again (`POST /api/connection-tests`); the
 * result stays next to the profile. "Connect" opens the read-only connection (D-02 first for PROD, `useConnect`) and
 * makes it the header's connection; "Disconnect" closes it. When either needs a password that is not stored yet, the
 * password dialog opens and the action runs again after it was saved. A failure shows the page's own text with what
 * to check, never the server's message.
 */
import type { TFunction } from 'i18next';
import { Database, Plus } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type Ref } from 'react';
import { useTranslation } from 'react-i18next';

import {
  closeConnection,
  fetchOpenConnections,
  testConnection,
  type ConnectionTest,
  type OpenedConnection,
} from '../../api/connections';
import type { Problem } from '../../api/problem';
import {
  deleteProfile,
  fetchKeyringAvailable,
  fetchProfiles,
  forgetSecret,
  storeSecret,
  type ProfileItem,
  type ProfileList,
  type Result,
} from '../../api/profiles';
import { EnvBadge } from '../../components/status/Status';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import { MAX_ENTRIES, visible } from '../../lib/visible';
import { DeleteProfileDialog, PasswordDialog } from './ConnectionDialogs';
import { ConnectionWizard, type WizardResult } from './ConnectionWizard';
import { problemText } from './messages';
import { useConnect } from './useConnect';
import { useConnectionSession } from '../../workspace';
import styles from './Connections.module.css';
import viewStyles from '../Views.module.css';

type Action = 'test' | 'connect';

type Open =
  | { kind: 'none' }
  | { kind: 'wizard'; item: ProfileItem | null }
  | { kind: 'delete'; item: ProfileItem; problem: Problem | null }
  | { kind: 'password'; item: ProfileItem; problem: Problem | null; then?: Action };

/** The last test or connection attempt of a profile, shown in its details. */
type Outcome =
  | { kind: 'busy'; action: Action }
  | { kind: 'tested'; test: ConnectionTest }
  | { kind: 'connected' }
  | { kind: 'problem'; action: Action; problem: Problem };

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
      ? t(ref?.source === 'env' ? 'connections.secret.envSet' : 'connections.secret.set')
      : item.secret.hasSecret === false
        ? t('connections.secret.missing')
        : t('connections.secret.unknown');
  return (
    <>
      {source} ({state})
    </>
  );
}

/**
 * PostgreSQL's `server_version_num` ("160015") as people write it ("16.15"); the server answers the written version,
 * this only guards against the number (C-3 of the connect review).
 */
export function humanVersion(test: ConnectionTest): string {
  const version = test.serverVersion ?? '';
  if (test.engine !== 'postgresql' || !/^\d{5,6}$/.test(version)) return version;
  const number = Number(version);
  return `${Math.floor(number / 10000)}.${number % 10000}`;
}

/** One fact of a test: "role studio_ro"; a fact the server did not give is left out. */
function testFacts(t: TFunction, test: ConnectionTest): string[] {
  const facts: string[] = [];
  const { facts: f } = test;
  if (test.latencyMs !== null) facts.push(t('connections.test.latency', { ms: test.latencyMs }));
  if (test.serverVersion !== null) {
    facts.push(
      t(`connections.test.version.${test.engine ?? 'other'}`, {
        version: visible(humanVersion(test)),
        ...NO_ESCAPE,
      }),
    );
  }
  if (f.role !== null)
    facts.push(t('connections.test.role', { role: visible(f.role), ...NO_ESCAPE }));
  if (f.superuser !== null)
    facts.push(t(f.superuser ? 'connections.test.superuser' : 'connections.test.noSuperuser'));
  if (f.tls !== null) facts.push(t(f.tls ? 'connections.test.tlsOn' : 'connections.test.tlsOff'));
  if (f.foreignKeys !== null) {
    facts.push(
      t(f.foreignKeys ? 'connections.test.foreignKeysOn' : 'connections.test.foreignKeysOff'),
    );
  }
  if (f.journalMode !== null) {
    facts.push(t('connections.test.journal', { mode: visible(f.journalMode), ...NO_ESCAPE }));
  }
  if (f.encoding !== null) {
    facts.push(t('connections.test.encoding', { encoding: visible(f.encoding), ...NO_ESCAPE }));
  }
  if (f.timezone !== null) {
    facts.push(t('connections.test.timezone', { zone: visible(f.timezone), ...NO_ESCAPE }));
  }
  return facts;
}

function OutcomeLine({ outcome }: { outcome: Outcome | undefined }) {
  const { t } = useTranslation();
  if (outcome === undefined) return null;
  if (outcome.kind === 'busy') {
    return (
      <p className={styles.hint}>
        {t(outcome.action === 'test' ? 'connections.test.running' : 'connections.connecting')}
      </p>
    );
  }
  if (outcome.kind === 'connected') {
    return <p className={styles.status}>{t('connections.connected')}</p>;
  }
  if (outcome.kind === 'problem') {
    return (
      <p className={styles.problem}>
        {t(outcome.action === 'test' ? 'connections.test.failed' : 'connections.connectFailed', {
          reason: problemText(t, outcome.problem),
          ...NO_ESCAPE,
        })}
      </p>
    );
  }
  const { test } = outcome;
  const facts = testFacts(t, test);
  return (
    <>
      <p className={test.ok ? styles.status : styles.problem}>
        <span aria-hidden="true">{test.ok ? '✓ ' : '✗ '}</span>
        {t(test.ok ? 'connections.test.ok' : 'connections.test.notOk')}
        {facts.length > 0 ? ` ${facts.join(' · ')}` : ''}
      </p>
      {test.facts.superuser === true || test.facts.bypassRls === true ? (
        <p className={styles.warning}>{t('connections.test.privileged')}</p>
      ) : null}
    </>
  );
}

function Details({
  item,
  outcome,
  open,
  onEdit,
  onDelete,
  onPassword,
  onForget,
  onTest,
  onConnect,
  onDisconnect,
  connectRef,
}: {
  connectRef: Ref<HTMLButtonElement>;
  item: ProfileItem;
  outcome: Outcome | undefined;
  /** The profile's open connection, if it has one. */
  open: OpenedConnection | undefined;
  onEdit: () => void;
  onDelete: () => void;
  onPassword: () => void;
  onForget: () => void;
  onTest: () => void;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  const { t } = useTranslation();
  const name = visible(item.name);
  const storable = item.password === null || item.password.source === 'keyring';
  const busy = outcome?.kind === 'busy';
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
        <dt>{t('connections.detail.state')}</dt>
        <dd data-testid="connection-state">
          {open !== undefined ? t('connections.detail.open') : t('connections.detail.closed')}
        </dd>
      </dl>
      <div className={styles.actions}>
        {/* L-1 of the connect review: while busy the buttons stay focusable (aria-disabled), so the focus stays. */}
        {open !== undefined ? (
          <Button aria-disabled={busy} onClick={() => !busy && onDisconnect()}>
            {t('connections.detail.disconnect')}
          </Button>
        ) : null}
        <Button
          ref={connectRef}
          variant="primary"
          aria-disabled={busy}
          onClick={() => !busy && onConnect()}
        >
          {t(open !== undefined ? 'connections.detail.use' : 'connections.detail.connect')}
        </Button>
        <Button aria-disabled={busy} onClick={() => !busy && onTest()}>
          {t('connections.detail.test')}
        </Button>
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
      <div aria-live="polite" aria-busy={busy} data-testid="connection-outcome">
        <OutcomeLine outcome={outcome} />
      </div>
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
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const [opened, setOpened] = useState<OpenedConnection[]>([]);
  const { setActive, forgetProd, latestConnect, isLatestConnect } = useConnectionSession();
  const { guard, connect, dialog: prodDialog } = useConnect(fetcher);
  /** Where the focus goes when the button that had it goes away (Disconnect; L-1 of the connect review). */
  const connectButton = useRef<HTMLButtonElement>(null);

  const outcome = (id: string, next: Outcome) =>
    setOutcomes((current) => ({ ...current, [id]: next }));
  const forgetOutcome = (id: string) =>
    setOutcomes((current) =>
      Object.fromEntries(Object.entries(current).filter(([key]) => key !== id)),
    );

  /**
   * The open connections. One the server closed (its profile changed, was removed or is no longer trusted) is gone
   * from the list, and from the header too.
   */
  const syncOpen = useCallback(
    (list: OpenedConnection[] | null, stamp: number) => {
      if (list === null) return;
      setOpened(list);
      // A list read before a newer connect request started may miss that connection: it clears nothing.
      if (!isLatestConnect(stamp)) return;
      setActive((current) =>
        current?.connectionId !== undefined &&
        !list.some((item) => item.connectionId === current.connectionId)
          ? null
          : current,
      );
    },
    [setActive, isLatestConnect],
  );

  /** Read the open connections again (with the connect request they were read after). */
  const refreshOpen = useCallback(() => {
    const stamp = latestConnect();
    void fetchOpenConnections(fetcher).then((list) => syncOpen(list, stamp));
  }, [fetcher, syncOpen, latestConnect]);

  const load = useCallback(async () => {
    const stamp = latestConnect();
    const [profiles, available, open] = await Promise.all([
      fetchProfiles(fetcher),
      fetchKeyringAvailable(fetcher),
      fetchOpenConnections(fetcher),
    ]);
    setList(profiles);
    setKeyring(available);
    syncOpen(open, stamp);
    return profiles;
  }, [fetcher, syncOpen, latestConnect]);

  useEffect(() => {
    let active = true;
    const stamp = latestConnect();
    void Promise.all([
      fetchProfiles(fetcher),
      fetchKeyringAvailable(fetcher),
      fetchOpenConnections(fetcher),
    ]).then(([profiles, available, open]) => {
      if (!active) return;
      setList(profiles);
      setKeyring(available);
      syncOpen(open, stamp);
    });
    return () => {
      active = false;
    };
  }, [fetcher, syncOpen, latestConnect]);

  /** A missing password opens the password dialog; the action runs again after it was saved (08 section 5). */
  const askPassword = (item: ProfileItem, problem: Problem, then: Action): boolean => {
    const storable = item.password === null || item.password.source === 'keyring';
    if (problem.code !== 'studio.secret_missing' || item.engine !== 'postgresql' || !storable) {
      return false;
    }
    setOpen({ kind: 'password', item, problem: null, then });
    return true;
  };

  /** The test connects too: D-02 first for a PROD profile (C-2 of the connect review). */
  const runTest = (item: ProfileItem) => guard(item, () => void testNow(item));

  const testNow = async (item: ProfileItem) => {
    outcome(item.id, { kind: 'busy', action: 'test' });
    const result = await testConnection(fetcher, item.id);
    if (result.ok) {
      outcome(item.id, { kind: 'tested', test: result.value });
      return;
    }
    outcome(item.id, { kind: 'problem', action: 'test', problem: result.problem });
    askPassword(item, result.problem, 'test');
  };

  const connected = (result: Result<OpenedConnection>, item: ProfileItem) => {
    if (result.ok) {
      outcome(item.id, { kind: 'connected' });
      setStatus({
        text: t('connections.connectedTo', { name: visible(item.name), ...NO_ESCAPE }),
        problem: false,
      });
      refreshOpen();
      return;
    }
    outcome(item.id, { kind: 'problem', action: 'connect', problem: result.problem });
    // The header may have gone back to a connection that was closed meanwhile (L-4): read the open list again.
    refreshOpen();
    askPassword(item, result.problem, 'connect');
  };

  const runConnect = (item: ProfileItem) =>
    guard(item, () => {
      outcome(item.id, { kind: 'busy', action: 'connect' });
      // Confirmed now (or not PROD), so `connect` does not ask again.
      connect(item, connected);
    });

  const disconnect = async (item: ProfileItem, connection: OpenedConnection) => {
    const result = await closeConnection(fetcher, connection.connectionId);
    // Gone either way: a connection the server no longer knows is closed as well.
    setActive((current) => (current?.connectionId === connection.connectionId ? null : current));
    setStatus(
      result.ok || result.problem.code === 'studio.connection_not_found'
        ? {
            text: t('connections.disconnected', { name: visible(item.name), ...NO_ESCAPE }),
            problem: false,
          }
        : { text: problemText(t, result.problem), problem: true },
    );
    forgetOutcome(item.id);
    connectButton.current?.focus();
    refreshOpen();
  };

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
      result.secretKept
        ? { text: t('connections.savedSecretKept', { name, ...NO_ESCAPE }), problem: true }
        : result.secretProblem === null
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
    const id = `user:${result.name}`;
    // C-1 of the connect review: the profile may point elsewhere now, so D-02 asks again.
    forgetProd((profileId) => profileId === id || profileId === result.previousId);
    setSelected(id);
    void load().then((list) => {
      const item = list?.profiles.find((profile) => profile.id === id);
      if (result.test && item !== undefined) runTest(item);
    });
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
    forgetProd((profileId) => profileId === item.id);
    const kept = result.value.secret_removed === false;
    setStatus({
      text: t(kept ? 'connections.deletedSecretKept' : 'connections.deleted', {
        name: visible(item.name),
        ...NO_ESCAPE,
      }),
      problem: kept,
    });
    void load();
  };

  const password = async (item: ProfileItem, secret: string, store: 'keyring' | 'session') => {
    const result = await storeSecret(fetcher, item.id, secret, store);
    if (!result.ok) {
      setOpen({ kind: 'password', item, problem: result.problem });
      return;
    }
    const then = open.kind === 'password' ? open.then : undefined;
    close();
    setStatus({
      text: t('connections.secretSaved', { name: visible(item.name), ...NO_ESCAPE }),
      problem: false,
    });
    void load();
    if (then === 'test') runTest(item);
    if (then === 'connect') runConnect(item);
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
                            {opened.some((connection) => connection.profileId === item.id) ? (
                              <> · {t('connections.detail.open')}</>
                            ) : null}
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
              outcome={outcomes[current.id]}
              open={opened.find((connection) => connection.profileId === current.id)}
              connectRef={connectButton}
              onTest={() => runTest(current)}
              onConnect={() => runConnect(current)}
              onDisconnect={() => {
                const connection = opened.find((item) => item.profileId === current.id);
                if (connection !== undefined) void disconnect(current, connection);
              }}
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
      {prodDialog}
    </div>
  );
}
