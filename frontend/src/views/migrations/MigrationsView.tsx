/**
 * The migration status view (STU-015, 03 section 2 F, 08 section 4): the code trust of the project's Alembic files and,
 * for a chosen profile, the status of every migration format the server finds. Studio only reads; it never migrates.
 *
 * - **Code trust (threat T-16):** reading the Alembic revision graph imports the project's revision files, which runs
 *   their code. The server does it only after the user trusted exactly these files; this view shows the state and
 *   opens the trust dialog (default "Don't trust"), or takes the trust back.
 * - **Status:** "Show status" opens the profile's read-only connection (`POST /api/connections`; D-02 first for a
 *   PROD profile) and reads `GET /api/connections/{id}/migrations`.
 *
 * Every value from the project or the database (names, paths, revision ids) is escaped (`lib/visible.ts`, U-1).
 */
import type { TFunction } from 'i18next';
import { useCallback, useEffect, useId, useState } from 'react';
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
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import { ProdConnectDialog, TrustCodeDialog } from '../../components/dialogs/StudioDialogs';
import { EnvBadge } from '../../components/status/Status';
import { Button } from '../../components/ui/Button';
import { visible } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { problemText } from '../connections/messages';
import viewStyles from '../Views.module.css';
import styles from './Migrations.module.css';

/** 03 section 5.3: production profiles get a 15 s statement timeout (D-02 names it). */
const PROD_TIMEOUT_S = 15;

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

/** A Levuvo version table (STU-024): its state, version and the steps not yet applied or not known. */
function LevuvoResult({ status }: { status: LevuvoStatus }) {
  const { t } = useTranslation();
  const open = status.steps.filter((step) => step.status !== 'applied');
  const where = [status.table, status.namespace].filter((part): part is string => part !== null);
  return (
    <section className={styles.format} data-testid="migrations-levuvo">
      <h3 className={styles.formatTitle}>
        {LEVUVO_TITLES[status.format]}
        {where.length > 0 ? (
          <>
            {' '}
            <code>{visible(where.join(' / '))}</code>
          </>
        ) : null}
      </h3>
      <p
        className={status.state === 'current' ? connectionStyles.status : connectionStyles.warning}
        data-testid="migrations-levuvo-state"
      >
        {status.state === 'unreadable' && status.problem !== null
          ? `${t('migrations.levuvo.unreadable')} ${problemText(t, status.problem)}`
          : t(`migrations.levuvo.${status.state}`)}
      </p>
      <dl className={connectionStyles.details}>
        <dt>{t('migrations.status.current')}</dt>
        <dd>{status.current === null ? t('migrations.status.unknown') : String(status.current)}</dd>
      </dl>
      {open.length > 0 ? (
        <ul className={styles.ids}>
          {open.map((step, index) => (
            <li key={index}>
              <code>{visible(`${step.version ?? '?'} ${step.name ?? ''}`.trim())}</code>{' '}
              {STEP_STATUSES.has(step.status)
                ? `(${t(`migrations.levuvo.steps.${step.status}`)})`
                : `(${t('migrations.levuvo.steps.other', { status: visible(step.status) })})`}
            </li>
          ))}
        </ul>
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
  /** The environment of the profile whose status is shown (PROD is marked there until TD-011 fills the shell). */
  const [statusEnv, setStatusEnv] = useState<ProfileItem['environment'] | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<'trust' | 'prod' | null>(null);
  const [stale, setStale] = useState(false);
  const [message, setMessage] = useState('');

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
    setBusy(true);
    setProblem(null);
    setStatus(null);
    const opened = await openConnection(fetcher, profile.id);
    const result = opened.ok ? await fetchMigrations(fetcher, opened.value) : opened;
    setBusy(false);
    if (result.ok) {
      setStatus(result.value);
      setStatusEnv(profile.environment);
    } else setProblem(result.problem);
  };

  /** Read the shown status again on its open connection: never opens one, so D-02 cannot be skipped (L-1). */
  const reread = async () => {
    if (status === null) return;
    setBusy(true);
    const result = await fetchMigrations(fetcher, status.connectionId);
    setBusy(false);
    if (result.ok) setStatus(result.value);
    else {
      setStatus(null);
      setProblem(result.problem);
    }
  };

  const trustNow = async (digest: string) => {
    const result = await trustCode(fetcher, digest);
    const stillStale = !result.ok && result.problem.code === 'studio.trust_stale';
    setStale(stillStale);
    const next = await loadTrust();
    if (result.ok) {
      setDialog(null);
      setMessage(t('migrations.code.trustedNow'));
      void reread();
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
    const result = await revokeCodeTrust(fetcher);
    await loadTrust();
    setMessage(result.ok ? t('migrations.code.revokedNow') : problemText(t, result.problem));
    if (result.ok) void reread();
  };

  return (
    <div className={viewStyles.page}>
      <div className={viewStyles.pageHeader}>
        <h1 className={viewStyles.title}>{t('migrations.title')}</h1>
        <p className={viewStyles.lead}>{t('migrations.lead')}</p>
      </div>
      <p role="status" className={connectionStyles.status} data-testid="migrations-message">
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
              onClick={() => (profile?.environment === 'prod' ? setDialog('prod') : void show())}
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
          timeoutSeconds={PROD_TIMEOUT_S}
          onCancel={() => setDialog(null)}
          onConnect={() => {
            setDialog(null);
            void show();
          }}
        />
      ) : null}
    </div>
  );
}
