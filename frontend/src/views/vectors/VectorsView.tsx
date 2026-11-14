/**
 * Vector data (STU-055, `docs/architecture/vector-inspection.md`): read-only facts about vector search in a database.
 *
 * - **Inspect:** opens the profile's read-only connection (`POST /api/connections`; a PROD profile asks D-02 first
 *   through the shared gate `useConnect`) and reads `GET /api/connections/{id}/vectors`. "Inspect again" uses the
 *   shown connection and never opens one, so D-02 cannot be skipped. An answer that arrives after a newer request or a
 *   profile switch is dropped.
 * - **Extension:** pgvector with its versions as the server reports them; sqlite-vec is "not determinable", since it
 *   is a module of the application's process, not part of the file, and Studio loads no extension.
 * - **Columns:** each vector column with its type, dimensions, stored embeddings (exact, "more than", or an estimate
 *   marked as such) and its indexes. "ANN index" only when the server says so and lists a valid ANN index; everything
 *   else is "Exact search (no ANN index)".
 * - **Identities:** the embedding model identities the database's metadata names (Orkexa's vector spaces).
 * - **Hints:** missing extension, no ANN index, unreadable tables, incomplete metadata, in en/de from their params.
 *
 * Names and identity values come from the database: shown escaped and capped (`lib/visible.ts`).
 */
import { CircleCheck, Info, TriangleAlert, Waypoints } from 'lucide-react';
import type { TFunction } from 'i18next';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PROD_CONFIRMATION_REQUIRED } from '../../api/connections';
import { openConnection } from '../../api/migrations';
import type { Problem } from '../../api/problem';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import {
  fetchVectors,
  type Count,
  type Identity,
  type VectorColumn,
  type VectorHint,
  type VectorReport,
} from '../../api/vectors';
import { EnvBadge } from '../../components/status/Status';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import clsx from '../../lib/clsx';
import { capped, visible } from '../../lib/visible';
import { useReconnected } from '../../workspace';
import connectionStyles from '../connections/Connections.module.css';
import { problemText } from '../connections/messages';
import { useConnect } from '../connections/useConnect';
import viewStyles from '../Views.module.css';
import styles from './Vectors.module.css';

/** How many rows each list shows (the server caps at 200; a broken one could send more). */
export const MAX_ROWS = 200;
export const MAX_HINTS = 50;
/** The longest name or value shown; a longer one is cut with a visible "…". */
export const MAX_NAME_LENGTH = 120;

/** Hint codes the page words; any other is shown with the generic text and its code. */
export const HINT_CODES = [
  'sqlite_vec_undeterminable',
  'vec0_module',
  'vec0_shadow_missing',
  'legacy_table_missing',
  'legacy_dimension_mismatch',
  'no_vector_data',
  'exact_search',
  'index_invalid',
  'dimensions_undeclared',
  'table_unreadable',
  'metadata_unreadable',
  'indexes_unreadable',
  'catalog_unreadable',
  'count_estimated',
  'count_timeout',
  'counts_skipped',
  'columns_capped',
  'space_building',
  'identity_incomplete',
  'dimension_mismatch',
  'pgvector_available',
  'pgvector_missing',
  'pgvector_update',
  'pgvector_other_schema',
  'extension_unknown',
] as const;
const WORDED = new Set<string>(HINT_CODES);
/** Hints the column table already says ("Exact search (no ANN index)"); listing each again would be noise. */
const SHOWN_IN_TABLE = new Set(['exact_search']);

type Phase = 'idle' | 'opening' | 'reading';

function cut(value: string, limit = MAX_NAME_LENGTH): string {
  const [shown, wasCut] = capped(visible(value), limit);
  return wasCut ? `${shown}…` : shown;
}

function number(language: string, value: number): string {
  return new Intl.NumberFormat(language).format(value);
}

export function countText(
  t: TFunction,
  language: string,
  count: Count | null,
  max: number,
): string {
  if (count === null) return t('vectors.count.none');
  if (count.value !== null && count.kind === 'exact') return number(language, count.value);
  if (count.kind === 'more_than')
    return t('vectors.count.moreThan', { count: max, value: number(language, max) });
  if (count.value !== null && count.kind === 'estimate') {
    return t('vectors.count.estimate', { value: number(language, count.value) });
  }
  if (count.kind === 'timeout') return t('vectors.count.timeout');
  if (count.kind === 'unreadable') return t('vectors.count.unreadable');
  if (count.kind === 'skipped') return t('vectors.count.skipped');
  return t('vectors.count.unknown');
}

/** A hint in the page's language; its params are database names and numbers, shown escaped. */
export function hintText(t: TFunction, hint: VectorHint): string {
  const params: Record<string, string> = {};
  for (const [key, value] of Object.entries(hint.params)) {
    if (typeof value === 'string') params[key] = cut(value);
    else if (typeof value === 'number' && Number.isFinite(value)) params[key] = String(value);
    else params[key] = t('vectors.unknownValue');
  }
  if (!WORDED.has(hint.code)) return t('vectors.hints.other', { code: cut(hint.code, 40) });
  if (hint.code === 'counts_skipped' || hint.code === 'columns_capped') {
    return t(`vectors.hints.${hint.code}`, { ...params, count: Number(hint.params.count) || 0 });
  }
  return t(`vectors.hints.${hint.code}`, params);
}

function ExtensionStatus({ report }: { report: VectorReport }) {
  const { t } = useTranslation();
  const extension = report.extension;
  const sqlite = report.engine === 'sqlite';
  const name = sqlite ? 'sqlite-vec' : 'pgvector';
  let detail: string;
  if (sqlite) detail = t('vectors.extension.notDeterminable');
  else if (extension.state === 'installed') {
    detail = t('vectors.extension.installed', {
      version: cut(extension.installedVersion ?? '?', 40),
      schema: cut(extension.schema ?? '?', 64),
    });
  } else if (extension.state === 'available') {
    detail = t('vectors.extension.available', {
      version: cut(extension.availableVersion ?? '?', 40),
    });
  } else if (extension.state === 'missing') detail = t('vectors.extension.missing');
  else if (extension.state === 'other_schema') {
    detail = t('vectors.extension.otherSchema', {
      version: cut(extension.installedVersion ?? '?', 40),
      schema: cut(extension.schema ?? '?', 64),
    });
  } else detail = t('vectors.extension.unknown');
  const good = !sqlite && extension.state === 'installed';
  return (
    <section className={styles.section} aria-labelledby="vectors-extension">
      <h3 id="vectors-extension" className={styles.sectionTitle}>
        {t('vectors.extension.title')}
      </h3>
      <p
        className={good ? connectionStyles.status : connectionStyles.warning}
        data-testid="vectors-extension"
        data-state={sqlite ? 'not_determinable' : extension.state}
      >
        <span className={styles.icon} aria-hidden="true">
          {good ? <CircleCheck /> : <Info />}
        </span>
        <strong className={styles.extensionName}>{name}</strong> {detail}
        {extension.updateAvailable ? (
          <span>
            {' '}
            {t('vectors.extension.update', {
              version: cut(extension.availableVersion ?? '?', 40),
            })}
          </span>
        ) : null}
      </p>
    </section>
  );
}

function SearchLabel({ column }: { column: VectorColumn }) {
  const { t } = useTranslation();
  const ann = column.search === 'ann';
  const method = column.indexes.find((index) => index.ann && index.state === 'valid')?.method;
  return (
    <span
      className={clsx(styles.badge, ann ? styles.ann : styles.exact)}
      data-testid="vectors-search"
      data-search={column.search}
    >
      {ann ? t('vectors.search.ann', { method: cut(method ?? '', 20) }) : t('vectors.search.exact')}
    </span>
  );
}

function IndexList({ column }: { column: VectorColumn }) {
  const { t, i18n } = useTranslation();
  if (column.indexes.length === 0) return <>{t('vectors.indexes.none')}</>;
  return (
    <ul className={styles.plainList}>
      {column.indexes.slice(0, 20).map((index) => (
        <li key={index.name} data-testid="vectors-index" data-state={index.state}>
          <code>{cut(index.name, 64)}</code>{' '}
          {t('vectors.indexes.line', {
            method: cut(index.method, 20),
            state: t(
              `vectors.indexState.${['valid', 'invalid', 'building'].includes(index.state) ? index.state : 'unknown'}`,
            ),
          })}
          {index.operatorClasses.length > 0
            ? ` · ${cut(index.operatorClasses.join(', '), 80)}`
            : ''}
          {index.options.length > 0 ? ` · ${cut(index.options.join(', '), 80)}` : ''}
          {index.sizeBytes !== null
            ? ` · ${t('vectors.indexes.size', { size: number(i18n.language, Math.ceil(index.sizeBytes / 1024)) })}`
            : ''}
          {index.createdWith !== null
            ? ` · ${t('vectors.indexes.createdWith', { version: cut(index.createdWith, 40) })}`
            : ''}
        </li>
      ))}
    </ul>
  );
}

function Columns({ report }: { report: VectorReport }) {
  const { t, i18n } = useTranslation();
  const listed = report.columns.slice(0, MAX_ROWS);
  return (
    <section className={styles.section} aria-labelledby="vectors-columns">
      <h3 id="vectors-columns" className={styles.sectionTitle}>
        {t('vectors.columns.title')}
      </h3>
      <p className={connectionStyles.hint}>{t('vectors.columns.rule')}</p>
      <div className={styles.scroll}>
        <table className={styles.grid} data-testid="vectors-columns">
          <thead>
            <tr>
              <th scope="col">{t('vectors.columns.column')}</th>
              <th scope="col">{t('vectors.columns.type')}</th>
              <th scope="col">{t('vectors.columns.dimensions')}</th>
              <th scope="col">{t('vectors.columns.search')}</th>
              <th scope="col">{t('vectors.columns.embeddings')}</th>
              <th scope="col">{t('vectors.columns.indexes')}</th>
            </tr>
          </thead>
          <tbody>
            {listed.map((column) => (
              <tr
                key={`${column.schema}.${column.table}.${column.column}`}
                data-testid="vectors-column"
              >
                <th scope="row">
                  <code>{cut(`${column.schema}.${column.table}.${column.column}`)}</code>
                  {column.layout === 'orkexa' ? (
                    <span className={styles.tag}>{t('vectors.columns.orkexa')}</span>
                  ) : null}
                </th>
                <td>
                  <code>{cut(column.type, 40)}</code>
                </td>
                <td>
                  {column.dimensions === null
                    ? t('vectors.columns.noDimensions')
                    : column.dimensions}
                </td>
                <td>
                  <SearchLabel column={column} />
                </td>
                <td data-testid="vectors-rows">
                  {countText(t, i18n.language, column.rows, report.exactCountMax)}
                </td>
                <td>
                  <IndexList column={column} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Identities({ report }: { report: VectorReport }) {
  const { t, i18n } = useTranslation();
  const listed: Identity[] = report.identities.slice(0, MAX_ROWS);
  const value = (text: string | null) =>
    text === null ? t('vectors.identities.missing') : cut(text);
  return (
    <section className={styles.section} aria-labelledby="vectors-identities">
      <h3 id="vectors-identities" className={styles.sectionTitle}>
        {t('vectors.identities.title')}
      </h3>
      {listed.length === 0 ? (
        <p className={connectionStyles.hint} data-testid="vectors-identities-none">
          {t('vectors.identities.none')}
        </p>
      ) : (
        <div className={styles.scroll}>
          <table className={styles.grid} data-testid="vectors-identities">
            <thead>
              <tr>
                <th scope="col">{t('vectors.identities.space')}</th>
                <th scope="col">{t('vectors.identities.model')}</th>
                <th scope="col">{t('vectors.identities.dimensions')}</th>
                <th scope="col">{t('vectors.identities.state')}</th>
                <th scope="col">{t('vectors.identities.embeddings')}</th>
              </tr>
            </thead>
            <tbody>
              {listed.map((identity) => (
                <tr
                  key={`${identity.schema}.${identity.table}.${identity.tenant ?? ''}.${identity.space}`}
                  data-testid="vectors-identity"
                >
                  <th scope="row">
                    <code>{cut(identity.space)}</code>
                    <div className={connectionStyles.hint}>
                      {identity.tenant !== null
                        ? t('vectors.identities.tenant', { tenant: cut(identity.tenant, 64) })
                        : null}{' '}
                      {identity.namespace !== null
                        ? t('vectors.identities.namespace', {
                            namespace: cut(identity.namespace, 64),
                          })
                        : null}
                    </div>
                  </th>
                  <td>
                    {t('vectors.identities.modelLine', {
                      provider: value(identity.provider),
                      model: value(identity.model),
                    })}
                    {identity.digest !== null ? (
                      <div className={connectionStyles.hint}>
                        {t('vectors.identities.digest', { digest: cut(identity.digest, 80) })}
                      </div>
                    ) : null}
                    {identity.normalized !== null ? (
                      <div className={connectionStyles.hint}>
                        {t(
                          identity.normalized
                            ? 'vectors.identities.normalized'
                            : 'vectors.identities.notNormalized',
                        )}
                      </div>
                    ) : null}
                    {!identity.complete ? (
                      <div className={connectionStyles.hint}>
                        {t('vectors.identities.incomplete')}
                      </div>
                    ) : null}
                  </td>
                  <td>{identity.dimensions ?? t('vectors.columns.noDimensions')}</td>
                  <td>
                    {identity.state === null
                      ? t('vectors.identities.missing')
                      : cut(identity.state, 32)}
                  </td>
                  <td>{countText(t, i18n.language, identity.embeddings, report.exactCountMax)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Hints({ hints }: { hints: VectorHint[] }) {
  const { t } = useTranslation();
  const listed = hints.filter((hint) => !SHOWN_IN_TABLE.has(hint.code)).slice(0, MAX_HINTS);
  if (listed.length === 0) return null;
  return (
    <section className={styles.section} aria-labelledby="vectors-hints">
      <h3 id="vectors-hints" className={styles.sectionTitle}>
        {t('vectors.hints.title')}
      </h3>
      <ul className={styles.hints} data-testid="vectors-hints">
        {listed.map((hint, index) => (
          <li
            key={index}
            className={clsx(styles.hint, styles[hint.severity])}
            data-testid="vectors-hint"
            data-code={hint.code}
          >
            <span className={styles.icon} aria-hidden="true">
              {hint.severity === 'warning' ? <TriangleAlert /> : <Info />}
            </span>
            <span className={styles.severity}>{t(`vectors.severity.${hint.severity}`)}</span>{' '}
            {hintText(t, hint)}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Report({
  report,
  name,
  environment,
}: {
  report: VectorReport;
  name: string;
  environment: ProfileItem['environment'];
}) {
  const { t } = useTranslation();
  const ann = report.columns.filter((column) => column.search === 'ann').length;
  return (
    <div className={styles.result} data-testid="vectors-report">
      <p className={styles.resultHead}>
        <EnvBadge environment={environment} size="sm" />
        <span className={styles.resultName}>{cut(name, 64)}</span>
        <span className={connectionStyles.hint} data-testid="vectors-summary">
          {t('vectors.summary', {
            count: report.columns.length,
            ann,
            exact: report.columns.length - ann,
          })}
        </span>
      </p>
      <ExtensionStatus report={report} />
      {report.columns.length === 0 ? (
        <EmptyState
          icon={<Waypoints />}
          title={t('vectors.emptyTitle')}
          description={t(
            report.engine === 'sqlite' ? 'vectors.emptySqlite' : 'vectors.emptyPostgres',
          )}
        />
      ) : (
        <Columns report={report} />
      )}
      {report.columns.length > 0 || report.identities.length > 0 ? (
        <Identities report={report} />
      ) : null}
      <Hints hints={report.hints} />
    </div>
  );
}

interface Shown {
  report: VectorReport;
  connectionId: string;
  name: string;
  environment: ProfileItem['environment'];
}

export function VectorsView({ fetcher = fetch }: { fetcher?: typeof fetch }) {
  const { t } = useTranslation();
  const selectId = useId();
  const gate = useConnect(fetcher);
  const [profiles, setProfiles] = useState<ProfileItem[] | null>(null);
  const [selected, setSelected] = useState('');
  const [shown, setShownState] = useState<Shown | null>(null);
  /** The shown report as of now, for a click that lands before the next render. */
  const shownNow = useRef<Shown | null>(null);
  const setShown = (next: Shown | null) => {
    shownNow.current = next;
    setShownState(next);
  };
  const [problem, setProblem] = useState<Problem | null>(null);
  const [phase, setPhaseState] = useState<Phase>('idle');
  const phaseNow = useRef<Phase>('idle');
  const setPhase = (next: Phase) => {
    phaseNow.current = next;
    setPhaseState(next);
  };
  const [message, setMessage] = useState('');
  /** Counts requests; an answer is shown only if no newer request or profile switch came since. */
  const request = useRef(0);
  // TD-034: "Reconnect" in the banner replaced the shown connection; "Inspect again" uses the new one.
  useReconnected((event) => {
    const current = shownNow.current;
    if (current?.connectionId === event.from) setShown({ ...current, connectionId: event.to });
  });

  useEffect(() => {
    let active = true;
    void fetchProfiles(fetcher).then((list) => {
      if (!active) return;
      const items = list?.profiles ?? [];
      setProfiles(items);
      setSelected((current) => current || (items[0]?.id ?? ''));
    });
    return () => {
      active = false;
      request.current += 1;
    };
  }, [fetcher]);

  const profile = profiles?.find((item) => item.id === selected) ?? null;

  const read = async (
    mine: number,
    connectionId: string,
    target: Omit<Shown, 'report' | 'connectionId'>,
  ) => {
    setPhase('reading');
    setMessage(t('vectors.reading'));
    const result = await fetchVectors(fetcher, connectionId);
    if (mine !== request.current) return;
    setPhase('idle');
    if (result.ok) {
      setShown({ report: result.value, connectionId, ...target });
      setMessage(t('vectors.done', { count: result.value.columns.length }));
    } else {
      setProblem(result.problem);
      setMessage('');
    }
  };

  const inspect = async () => {
    if (profile === null || phaseNow.current !== 'idle') return;
    const target = { name: profile.name, environment: profile.environment };
    const mine = ++request.current;
    setPhase('opening');
    setProblem(null);
    setShown(null);
    setMessage(t('vectors.opening'));
    const opened = await openConnection(fetcher, profile.id, profile.environment);
    if (mine !== request.current) return;
    if (!opened.ok) {
      setPhase('idle');
      setProblem(opened.problem);
      setMessage('');
      if (opened.problem.code === PROD_CONFIRMATION_REQUIRED) {
        void fetchProfiles(fetcher).then((list) => {
          if (list !== null) setProfiles(list.profiles);
        });
      }
      return;
    }
    await read(mine, opened.value, target);
  };

  /** Again on the shown connection: never opens one, so D-02 cannot be skipped. */
  const again = async () => {
    const current = shownNow.current;
    if (current === null || phaseNow.current !== 'idle') return;
    const mine = ++request.current;
    setProblem(null);
    await read(mine, current.connectionId, {
      name: current.name,
      environment: current.environment,
    });
  };

  const switchProfile = (id: string) => {
    request.current += 1;
    setPhase('idle');
    setSelected(id);
    setShown(null);
    setProblem(null);
    setMessage('');
  };

  const busy = phase !== 'idle';
  const canInspect = profile !== null && !busy;
  const canAgain = shown !== null && !busy;
  return (
    <div className={viewStyles.page}>
      <div className={viewStyles.pageHeader}>
        <h1 className={viewStyles.title}>{t('vectors.title')}</h1>
        <p className={viewStyles.lead}>{t('vectors.lead')}</p>
      </div>
      <section className={viewStyles.card} aria-labelledby="vectors-run" data-testid="vectors-card">
        <h2 id="vectors-run" className={viewStyles.cardTitle}>
          {t('vectors.cardTitle')}
        </h2>
        {profiles !== null && profiles.length === 0 ? (
          <p className={connectionStyles.hint}>{t('vectors.noProfiles')}</p>
        ) : (
          <div className={styles.picker}>
            <label htmlFor={selectId} className={connectionStyles.label}>
              {t('vectors.profile')}
            </label>
            <select
              id={selectId}
              className={connectionStyles.input}
              value={selected}
              disabled={profiles === null}
              onChange={(event) => switchProfile(event.target.value)}
            >
              {(profiles ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {`${visible(item.name)} (${item.environment.toUpperCase()})`}
                </option>
              ))}
            </select>
            <Button
              variant="primary"
              aria-disabled={!canInspect}
              onClick={() => {
                if (!canInspect || profile === null) return;
                gate.guard(profile, () => void inspect());
              }}
            >
              {phase === 'idle' ? t('vectors.inspect') : t('vectors.busy')}
            </Button>
            <Button aria-disabled={!canAgain} onClick={() => canAgain && void again()}>
              {t('vectors.again')}
            </Button>
          </div>
        )}
        <p className={connectionStyles.hint}>{t('vectors.readOnly')}</p>
        <p
          role="status"
          className={message === '' ? 'tb-sr-only' : styles.message}
          data-testid="vectors-message"
        >
          {message}
        </p>
        {problem !== null ? (
          <p role="alert" className={connectionStyles.problem} data-testid="vectors-problem">
            {problemText(t, problem)}
          </p>
        ) : null}
        <div aria-busy={busy} data-testid="vectors-result">
          {shown !== null ? (
            <Report report={shown.report} name={shown.name} environment={shown.environment} />
          ) : null}
        </div>
      </section>
      {gate.dialog}
    </div>
  );
}
