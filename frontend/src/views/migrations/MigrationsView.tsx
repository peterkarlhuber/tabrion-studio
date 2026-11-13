/**
 * The migration status view (STU-015, 03 section 2 F, 08 section 4): the code trust of the project's Alembic files and,
 * for a chosen profile, the status of every migration format the server finds. Studio only reads; it never migrates.
 *
 * - **Code trust (threat T-16):** reading the Alembic revision graph imports the project's revision files, which runs
 *   their code. The server does it only after the user trusted exactly these files; this view shows the state and
 *   opens the trust dialog (default "Don't trust"), or takes the trust back.
 * - **Status:** "Show status" opens the profile's read-only connection (`POST /api/connections`; D-02 first for a
 *   PROD profile the user has not confirmed in this page session, STU-008) and reads
 *   `GET /api/connections/{id}/migrations`. The opened connection becomes the header's connection.
 *
 * Every value from the project or the database (names, paths, revision ids) is escaped (`lib/visible.ts`, U-1).
 */
import type { TFunction } from 'i18next';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  fetchCodeTrust,
  fetchMigrations,
  openConnection,
  revokeCodeTrust,
  trustCode,
  type AlembicStatus,
  type CodeTrust,
  type LevuvoFormat,
  type LevuvoStatus,
  type MigrationStatus,
} from '../../api/migrations';
import type { Problem } from '../../api/problem';
import { PROD_CONFIRMATION_REQUIRED } from '../../api/connections';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import { ProdConnectDialog, TrustCodeDialog } from '../../components/dialogs/StudioDialogs';
import { EnvBadge } from '../../components/status/Status';
import { Button } from '../../components/ui/Button';
import { capped, visible } from '../../lib/visible';
import { useConnectionSession } from '../../workspace';
import connectionStyles from '../connections/Connections.module.css';
import { problemText } from '../connections/messages';
import { prodTimeoutSeconds } from '../connections/useConnect';
import viewStyles from '../Views.module.css';
import styles from './Migrations.module.css';

/** The fixed issue codes of an Alembic layout Studio cannot trust (`config/code_trust.py`); others show their code. */
const ISSUES = new Set([
  'config_invalid',
  'location_outside',
  'location_missing',
  'too_many_files',
  'file_name',
  'unreadable',
  'changed',
]);

function issueReason(t: TFunction, issue: string | null): string {
  const name = (issue ?? '').replace(/^migrations\./, '');
  return ISSUES.has(name)
    ? t(`migrations.issue.${name}`)
    : t('migrations.issue.other', { code: visible(issue ?? '') });
}

function Ids({ ids, empty }: { ids: string[]; empty: string }) {
  if (ids.length === 0) return <>{empty}</>;
  return (
    <ul className={styles.ids}>
      {ids.map((id, index) => (
        <li key={index}>
          <code>{visible(id)}</code>
        </li>
      ))}
    </ul>
  );
}

function AlembicResult({ status }: { status: AlembicStatus }) {
  const { t } = useTranslation();
  const read = status.revisions === 'read';
  let summary: string;
  if (!read) {
    summary =
      status.problem !== null
        ? problemText(t, status.problem)
        : status.codeTrust === 'invalid'
          ? t('migrations.status.invalidFolder')
          : t('migrations.status.notTrusted');
  } else if (status.isCurrent === true) {
    summary = t('migrations.status.upToDate');
  } else {
    summary = t('migrations.status.pending', { count: status.pending?.length ?? 0 });
  }
  return (
    <section
      className={styles.format}
      aria-labelledby="migrations-alembic"
      data-testid="migrations-alembic"
    >
      <h3 id="migrations-alembic" className={styles.formatTitle}>
        {t('migrations.status.alembic')}
      </h3>
      <p
        className={read ? connectionStyles.status : connectionStyles.warning}
        data-testid="migrations-summary"
      >
        {summary}
      </p>
      <dl className={connectionStyles.details}>
        <dt>{t('migrations.status.current')}</dt>
        <dd>
          {status.current === null ? (
            t('migrations.status.noTable')
          ) : (
            <Ids ids={status.current} empty={t('migrations.status.none')} />
          )}
        </dd>
        <dt>{t('migrations.status.heads')}</dt>
        <dd>
          {status.heads === null ? (
            t('migrations.status.unknown')
          ) : (
            <Ids ids={status.heads} empty={t('migrations.status.none')} />
          )}
        </dd>
        <dt>{t('migrations.status.pendingLabel')}</dt>
        <dd>
          {status.pending === null ? (
            t('migrations.status.unknown')
          ) : (
            <Ids ids={status.pending} empty={t('migrations.status.none')} />
          )}
        </dd>
      </dl>
      {status.unknown !== null && status.unknown.length > 0 ? (
        <div className={connectionStyles.warning} data-testid="migrations-unknown">
          <p>{t('migrations.status.unknownRevisions')}</p>
          <Ids ids={status.unknown} empty="" />
        </div>
      ) : null}
    </section>
  );
}

const LEVUVO_TITLES: Record<LevuvoFormat, string> = {
  nodestro: 'Nodestro',
  axentra: 'Axentra',
  orkexa: 'Orkexa',
};

const STEP_STATUSES = new Set(['pending', 'unknown', 'changed']);

/** How many open steps a version table lists before "and N more" (review I-6: a table can hold 10 000 rows). */
export const MAX_LEVUVO_STEPS = 20;

/**
 * Why a Levuvo version table could not be read (STU-024 sends these codes for one table, review L-8); any other code
 * falls back to the general problem text.
 */
const UNREADABLE_REASONS: Record<string, string> = {
  'db.permission': 'permission',
  'studio.migrations_table': 'table',
  'studio.statement_refused': 'refused',
  'db.timeout': 'timeout',
};

function unreadableText(t: TFunction, problem: Problem | null): string {
  if (problem === null) return t('migrations.levuvo.unreadable');
  const reason = UNREADABLE_REASONS[problem.code];
  return `${t('migrations.levuvo.unreadable')} ${
    reason === undefined ? problemText(t, problem) : t(`migrations.levuvo.reason.${reason}`)
  }`;
}

/** `capped(value)` as one string, with "…" when it was cut (review F-5). */
function cutText(value: string): string {
  const [shown, cut] = capped(value);
  return cut ? `${shown}…` : shown;
}

/** An untrusted value escaped and cut to MAX_ENTRY_LENGTH with a visible marker (U-1c, review I-6). */
function Capped({ value }: { value: string }) {
  const [shown, cut] = capped(value);
  return (
    <>
      <code>{shown}</code>
      {cut ? <span aria-hidden="true">…</span> : null}
    </>
  );
}

/** A Levuvo version table (STU-024): its state, version and the steps not yet applied or not known. */
function LevuvoResult({ status }: { status: LevuvoStatus }) {
  const { t } = useTranslation();
  const open = status.steps.filter((step) => step.status !== 'applied');
  const listed = open.slice(0, MAX_LEVUVO_STEPS);
  const where = [status.table, status.namespace].filter((part): part is string => part !== null);
  return (
    <section className={styles.format} data-testid="migrations-levuvo">
      <h3 className={styles.formatTitle}>
        {LEVUVO_TITLES[status.format]}
        {where.length > 0 ? (
          <>
            {' '}
            <Capped value={where.join(' / ')} />
          </>
        ) : null}
      </h3>
      <p
        className={status.state === 'current' ? connectionStyles.status : connectionStyles.warning}
        data-testid="migrations-levuvo-state"
      >
        {status.state === 'unreadable'
          ? unreadableText(t, status.problem)
          : t(`migrations.levuvo.${status.state}`)}
      </p>
      <dl className={connectionStyles.details}>
        <dt>{t('migrations.status.current')}</dt>
        <dd>{status.current === null ? t('migrations.status.unknown') : String(status.current)}</dd>
      </dl>
      {listed.length > 0 ? (
        <ul className={styles.ids} data-testid="migrations-levuvo-steps">
          {listed.map((step, index) => (
            <li key={index}>
              <Capped value={`${step.version ?? '?'} ${step.name ?? ''}`.trim()} />{' '}
              {STEP_STATUSES.has(step.status)
                ? `(${t(`migrations.levuvo.steps.${step.status}`)})`
                : `(${t('migrations.levuvo.steps.other', { status: cutText(step.status) })})`}
            </li>
          ))}
        </ul>
      ) : null}
      {open.length > listed.length ? (
        <p className={connectionStyles.hint} data-testid="migrations-levuvo-more">
          {t('migrations.levuvo.moreSteps', { count: open.length - listed.length })}
        </p>
      ) : null}
    </section>
  );
}

function StatusResult({ status }: { status: MigrationStatus }) {
  const { t } = useTranslation();
  const others = status.formats.filter((format) => format.format === 'other').length;
  const shown = status.formats.filter((format) => format.format !== 'other');
  if (shown.length === 0 && others === 0) {
    return <p className={connectionStyles.hint}>{t('migrations.status.noFormats')}</p>;
  }
  return (
    <>
      {others > 0 ? (
        <p className={connectionStyles.warning} data-testid="migrations-others">
          {t('migrations.status.others', { count: others })}
        </p>
      ) : null}
      {shown.map((format, index) => {
        switch (format.format) {
          case 'sqlite_user_version':
            return (
              <section key={index} className={styles.format} data-testid="migrations-sqlite">
                <h3 className={styles.formatTitle}>{t('migrations.status.sqlite')}</h3>
                <dl className={connectionStyles.details}>
                  <dt>
                    <code>user_version</code>
                  </dt>
                  <dd>
                    {format.current === null
                      ? t('migrations.status.unknown')
                      : String(format.current)}
                  </dd>
                </dl>
              </section>
            );
          case 'alembic':
            return <AlembicResult key={index} status={format} />;
          default:
            return <LevuvoResult key={index} status={format} />;
        }
      })}
    </>
  );
}

function CodeTrustCard({
  trust,
  onReview,
  onRevoke,
}: {
  trust: CodeTrust | null;
  onReview: () => void;
  onRevoke: () => void;
}) {
  const { t } = useTranslation();
  let body: string;
  if (trust === null) body = t('migrations.code.unavailable');
  else if (trust.state === 'invalid')
    body = t('migrations.code.invalid', { reason: issueReason(t, trust.issue) });
  else body = t(`migrations.code.${trust.state}`, { count: trust.revisionFiles.length });
  return (
    <section className={viewStyles.card} aria-labelledby="migrations-code" data-testid="code-trust">
      <h2 id="migrations-code" className={viewStyles.cardTitle}>
        {t('migrations.code.title')}
      </h2>
      <p className={connectionStyles.hint} data-testid="code-trust-state">
        {body}
      </p>
      {trust !== null && (trust.state === 'untrusted' || trust.state === 'changed') ? (
        <div className={connectionStyles.actions}>
          <Button onClick={onReview}>{t('migrations.code.review')}</Button>
        </div>
      ) : null}
      {trust?.state === 'trusted' ? (
        <div className={connectionStyles.actions}>
          <Button onClick={onRevoke}>{t('migrations.code.revoke')}</Button>
        </div>
      ) : null}
    </section>
  );
}

export function MigrationsView({ fetcher = fetch }: { fetcher?: typeof fetch }) {
  const { t } = useTranslation();
  const selectId = useId();
  const [trust, setTrust] = useState<CodeTrust | null>(null);
  const [profiles, setProfiles] = useState<ProfileItem[] | null>(null);
  const [selected, setSelected] = useState('');
  const [status, setStatus] = useState<MigrationStatus | null>(null);
  /** The environment of the profile whose status is shown (marked next to it, and in the header). */
  const [statusEnv, setStatusEnv] = useState<ProfileItem['environment'] | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<'trust' | 'prod' | null>(null);
  const [stale, setStale] = useState(false);
  const [message, setMessage] = useState('');
  /** Counts status requests; a result is shown only if no newer request or profile switch came since (review L-9). */
  const request = useRef(0);
  const connections = useConnectionSession();

  const loadTrust = useCallback(async () => {
    const next = await fetchCodeTrust(fetcher);
    setTrust(next);
    return next;
  }, [fetcher]);

  useEffect(() => {
    let active = true;
    void fetchCodeTrust(fetcher).then((next) => {
      if (active) setTrust(next);
    });
    void fetchProfiles(fetcher).then((list) => {
      if (!active) return;
      const items = list?.profiles ?? [];
      setProfiles(items);
      setSelected((current) => current || (items[0]?.id ?? ''));
    });
    return () => {
      active = false;
    };
  }, [fetcher]);

  const profile = profiles?.find((item) => item.id === selected) ?? null;

  const show = async () => {
    if (profile === null) return;
    const mine = ++request.current;
    setBusy(true);
    setProblem(null);
    setStatus(null);
    const connecting = connections.beginConnect();
    // The environment this page confirmed for (D-02 on the server, TD-038).
    const opened = await openConnection(fetcher, profile.id, profile.environment);
    // L-3 of the connect review: a profile switch (or a newer request) since makes this one stale; it does not
    // become the header's connection. Neither does it when a newer connect started elsewhere (L-4).
    if (mine !== request.current) return;
    if (opened.ok && connections.isLatestConnect(connecting)) {
      connections.setActive({
        name: visible(profile.name),
        environment: profile.environment,
        status: 'connected',
        connectionId: opened.value,
        profileId: profile.id,
      });
    }
    const result = opened.ok ? await fetchMigrations(fetcher, opened.value) : opened;
    if (mine !== request.current) return;
    setBusy(false);
    if (result.ok) {
      setStatus(result.value);
      setStatusEnv(profile.environment);
    } else {
      setProblem(result.problem);
      // The profile became PROD after this page read it: read the profiles again, so the next try asks D-02.
      if (result.problem.code === PROD_CONFIRMATION_REQUIRED) {
        void fetchProfiles(fetcher).then((list) => {
          if (list !== null) setProfiles(list.profiles);
        });
      }
    }
  };

  /** Read the shown status again on its open connection: never opens one, so D-02 cannot be skipped (L-1). */
  const reread = async () => {
    if (status === null) return;
    const mine = ++request.current;
    setBusy(true);
    const result = await fetchMigrations(fetcher, status.connectionId);
    if (mine !== request.current) return;
    setBusy(false);
    if (result.ok) setStatus(result.value);
    else {
      setStatus(null);
      setProblem(result.problem);
    }
  };

  const trustNow = async (digest: string) => {
    // The reread below uses this render's status; skip it if a request or a profile switch came since (F-1).
    const mine = request.current;
    const result = await trustCode(fetcher, digest);
    const stillStale = !result.ok && result.problem.code === 'studio.trust_stale';
    setStale(stillStale);
    const next = await loadTrust();
    if (result.ok) {
      setDialog(null);
      setMessage(t('migrations.code.trustedNow'));
      if (mine === request.current) void reread();
    } else if (
      !stillStale ||
      next === null ||
      next.digest === null ||
      (next.state !== 'untrusted' && next.state !== 'changed')
    ) {
      // A refusal, or the files changed into a state that cannot be trusted (review L-4): say why.
      setDialog(null);
      setMessage(problemText(t, result.problem));
    }
  };

  const revoke = async () => {
    const mine = request.current;
    const result = await revokeCodeTrust(fetcher);
    await loadTrust();
    setMessage(result.ok ? t('migrations.code.revokedNow') : problemText(t, result.problem));
    if (result.ok && mine === request.current) void reread();
  };

  return (
    <div className={viewStyles.page}>
      <div className={viewStyles.pageHeader}>
        <h1 className={viewStyles.title}>{t('migrations.title')}</h1>
        <p className={viewStyles.lead}>{t('migrations.lead')}</p>
      </div>
      {/* STU-050: an empty message is no empty coloured box; the live region stays in place for the next one. */}
      <p
        role="status"
        className={message === '' ? 'tb-sr-only' : connectionStyles.status}
        data-testid="migrations-message"
      >
        {message}
      </p>
      <CodeTrustCard
        trust={trust}
        onReview={() => {
          setStale(false);
          setDialog('trust');
        }}
        onRevoke={() => void revoke()}
      />
      <section
        className={viewStyles.card}
        aria-labelledby="migrations-status"
        data-testid="migrations-status"
      >
        <h2 id="migrations-status" className={viewStyles.cardTitle}>
          {t('migrations.status.title')}
        </h2>
        {profiles !== null && profiles.length === 0 ? (
          <p className={connectionStyles.hint}>{t('migrations.status.noProfiles')}</p>
        ) : (
          <div className={styles.picker}>
            <label htmlFor={selectId} className={connectionStyles.label}>
              {t('migrations.status.profile')}
            </label>
            <select
              id={selectId}
              className={connectionStyles.input}
              value={selected}
              disabled={profiles === null}
              onChange={(event) => {
                request.current += 1;
                setBusy(false);
                setSelected(event.target.value);
                setStatus(null);
                setStatusEnv(null);
                setProblem(null);
              }}
            >
              {(profiles ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {`${visible(item.name)} (${item.environment.toUpperCase()})`}
                </option>
              ))}
            </select>
            <Button
              variant="primary"
              disabled={profile === null || busy}
              onClick={() =>
                profile?.environment === 'prod' && !connections.prodConfirmed(profile)
                  ? setDialog('prod')
                  : void show()
              }
            >
              {busy ? t('migrations.status.reading') : t('migrations.status.show')}
            </Button>
          </div>
        )}
        {problem !== null ? (
          <p role="alert" className={connectionStyles.problem} data-testid="migrations-problem">
            {problemText(t, problem)}
          </p>
        ) : null}
        <div aria-live="polite" aria-busy={busy} data-testid="migrations-result">
          {status !== null ? (
            <>
              {statusEnv !== null ? (
                <p className={styles.env}>
                  <EnvBadge environment={statusEnv} size="sm" />
                </p>
              ) : null}
              <StatusResult status={status} />
            </>
          ) : null}
        </div>
      </section>
      {dialog === 'trust' && trust !== null && trust.digest !== null ? (
        <TrustCodeDialog
          open
          configFile={trust.configFile ?? 'alembic.ini'}
          scriptLocation={trust.scriptLocation}
          revisionFiles={trust.revisionFiles}
          stale={stale}
          onReject={() => setDialog(null)}
          onTrust={() => void trustNow(trust.digest ?? '')}
        />
      ) : null}
      {dialog === 'prod' && profile !== null ? (
        <ProdConnectDialog
          open
          name={visible(profile.name)}
          timeoutSeconds={prodTimeoutSeconds(profile)}
          onCancel={() => setDialog(null)}
          onConnect={() => {
            connections.confirmProd(profile);
            setDialog(null);
            void show();
          }}
        />
      ) : null}
    </div>
  );
}
