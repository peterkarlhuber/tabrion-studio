/**
 * The detail of the selected tree row (STU-010, 08 section 4.3): an object's header and its tabs (columns,
 * constraints, indexes, relationships and, on SQLite, the table's SQLite facts), the PostgreSQL extensions, or the
 * SQLite file. Everything only reads; there is no action that changes the database (STU-026 stays locked). The DDL
 * and policies tabs and SQLite's row count on request (STU-022) are in `Definitions.tsx`.
 *
 * Every value from the database (names, types, defaults, check texts, comments, versions) is escaped and capped with a
 * visible "…" (`lib/visible.ts`, U-1); lists are cut after MAX_DETAIL_ROWS with "and N more".
 */
import type { TFunction } from 'i18next';
import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type {
  CatalogObject,
  Engine,
  Extension,
  ObjectKind,
  SqliteInfo,
  TableDetail,
} from '../../api/explorer';
import { capped } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { DdlView, PoliciesView, RowCount } from './Definitions';
import styles from './Explorer.module.css';

/** How many rows of one list (columns, indexes, keys, extensions) are shown. */
export const MAX_DETAIL_ROWS = 500;
/** Caps of database text, in escaped characters (U-1c). */
export const CAP_NAME = 64;
export const CAP_TYPE = 64;
export const CAP_DEFAULT = 160;
export const CAP_TEXT = 300;

/** An untrusted value, escaped and cut with a visible marker; the marker is not read twice. */
export function Value({ value, cap = CAP_NAME }: { value: string; cap?: number }) {
  const [shown, cut] = capped(value, cap);
  return (
    <>
      {shown}
      {cut ? <span aria-hidden="true">…</span> : null}
    </>
  );
}

/** `capped()` as one string with "…" when it was cut. */
export function cutText(value: string, cap = CAP_NAME): string {
  const [shown, cut] = capped(value, cap);
  return cut ? `${shown}…` : shown;
}

function Code({ value, cap }: { value: string; cap?: number }) {
  return (
    <code>
      <Value value={value} cap={cap} />
    </code>
  );
}

function Names({ names }: { names: string[] }) {
  const { t } = useTranslation();
  if (names.length === 0) return <>{t('explorer.detail.none')}</>;
  const shown = names.slice(0, MAX_DETAIL_ROWS);
  return (
    <>
      {shown.map((name, index) => (
        <span key={index}>
          {index > 0 ? ', ' : null}
          <Code value={name} />
        </span>
      ))}
      {names.length > shown.length
        ? ` ${t('explorer.detail.more', { count: names.length - shown.length })}`
        : null}
    </>
  );
}

function More({ total }: { total: number }) {
  const { t } = useTranslation();
  if (total <= MAX_DETAIL_ROWS) return null;
  return (
    <p className={connectionStyles.hint} data-testid="detail-more">
      {t('explorer.detail.more', { count: total - MAX_DETAIL_ROWS })}
    </p>
  );
}

/** A table that scrolls sideways on its own on a narrow screen; the scroll area takes the focus (axe). */
function Grid({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a region that scrolls must take the focus (axe scrollable-region-focusable, WCAG 2.1.1)
    <div className={styles.gridScroll} role="region" aria-label={caption} tabIndex={0}>
      <table className={styles.grid}>
        <caption className="tb-sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

function yesNo(t: TFunction, value: boolean | null): string {
  if (value === null) return t('explorer.detail.unknown');
  return value ? t('explorer.detail.yes') : t('explorer.detail.no');
}

/** "Go to" a related object: only for one in the catalog. */
export type Follow = (object: CatalogObject) => void;

function Related({
  schema,
  table,
  find,
  onFollow,
}: {
  schema: string;
  table: string;
  find: (schema: string, name: string) => CatalogObject | null;
  onFollow: Follow;
}) {
  const { t } = useTranslation();
  const target = find(schema, table);
  const label = cutText(`${schema}.${table}`);
  if (target === null) return <code>{label}</code>;
  return (
    <button
      type="button"
      className={styles.link}
      onClick={() => onFollow(target)}
      aria-label={t('explorer.detail.goTo', { name: label })}
    >
      <code>{label}</code>
    </button>
  );
}

function ColumnsTab({ detail }: { detail: TableDetail }) {
  const { t } = useTranslation();
  const primary = new Set(detail.primaryKey?.columns ?? []);
  const foreign = new Set(detail.foreignKeys.flatMap((key) => key.columns));
  const shown = detail.columns.slice(0, MAX_DETAIL_ROWS);
  if (detail.columns.length === 0) {
    return <p className={connectionStyles.hint}>{t('explorer.detail.noColumns')}</p>;
  }
  return (
    <>
      <Grid caption={t('explorer.tabs.columns')}>
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">{t('explorer.columns.name')}</th>
            <th scope="col">{t('explorer.columns.type')}</th>
            <th scope="col">{t('explorer.columns.nullable')}</th>
            <th scope="col">{t('explorer.columns.default')}</th>
            <th scope="col">{t('explorer.columns.comment')}</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((column, index) => (
            <tr key={index} data-testid="detail-column">
              <td>{index + 1}</td>
              <th scope="row">
                <Code value={column.name} />
                {primary.has(column.name) ? (
                  <span className={styles.badge} title={t('explorer.columns.primaryKey')}>
                    <span aria-hidden="true">PK</span>
                    <span className="tb-sr-only">{t('explorer.columns.primaryKey')}</span>
                  </span>
                ) : null}
                {foreign.has(column.name) ? (
                  <span className={styles.badge} title={t('explorer.columns.foreignKey')}>
                    <span aria-hidden="true">FK</span>
                    <span className="tb-sr-only">{t('explorer.columns.foreignKey')}</span>
                  </span>
                ) : null}
              </th>
              <td>
                {column.type === null ? (
                  t('explorer.detail.unknown')
                ) : (
                  <Code value={column.type} cap={CAP_TYPE} />
                )}
              </td>
              <td>{yesNo(t, column.nullable)}</td>
              <td>
                {column.default === null ? '' : <Code value={column.default} cap={CAP_DEFAULT} />}
              </td>
              <td>
                {column.comment === null ? '' : <Value value={column.comment} cap={CAP_TEXT} />}
              </td>
            </tr>
          ))}
        </tbody>
      </Grid>
      <More total={detail.columns.length} />
    </>
  );
}

function ConstraintsTab({
  detail,
  find,
  onFollow,
}: {
  detail: TableDetail;
  find: (schema: string, name: string) => CatalogObject | null;
  onFollow: Follow;
}) {
  const { t } = useTranslation();
  const primary = detail.primaryKey;
  const unique = detail.uniqueConstraints.slice(0, MAX_DETAIL_ROWS);
  const checks = detail.checkConstraints.slice(0, MAX_DETAIL_ROWS);
  const keys = detail.foreignKeys.slice(0, MAX_DETAIL_ROWS);
  return (
    <div className={styles.sections}>
      <section aria-labelledby="explorer-pk">
        <h4 id="explorer-pk" className={styles.subTitle}>
          {t('explorer.constraints.primaryKey')}
        </h4>
        {primary === null || primary.columns.length === 0 ? (
          <p className={connectionStyles.hint}>{t('explorer.constraints.noPrimaryKey')}</p>
        ) : (
          <p data-testid="detail-pk">
            {primary.name !== null ? (
              <>
                <Code value={primary.name} />:{' '}
              </>
            ) : null}
            <Names names={primary.columns} />
          </p>
        )}
      </section>
      <section aria-labelledby="explorer-fk">
        <h4 id="explorer-fk" className={styles.subTitle}>
          {t('explorer.constraints.foreignKeys')}
        </h4>
        {keys.length === 0 ? (
          <p className={connectionStyles.hint}>{t('explorer.detail.none')}</p>
        ) : (
          <ul className={styles.plainList}>
            {keys.map((key, index) => (
              <li key={index} data-testid="detail-fk">
                {key.name !== null ? (
                  <>
                    <Code value={key.name} />:{' '}
                  </>
                ) : null}
                <Names names={key.columns} /> →{' '}
                <Related
                  schema={key.referredSchema}
                  table={key.referredTable}
                  find={find}
                  onFollow={onFollow}
                />{' '}
                (<Names names={key.referredColumns} />)
              </li>
            ))}
          </ul>
        )}
        <More total={detail.foreignKeys.length} />
      </section>
      <section aria-labelledby="explorer-unique">
        <h4 id="explorer-unique" className={styles.subTitle}>
          {t('explorer.constraints.unique')}
        </h4>
        {unique.length === 0 ? (
          <p className={connectionStyles.hint}>{t('explorer.detail.none')}</p>
        ) : (
          <ul className={styles.plainList}>
            {unique.map((item, index) => (
              <li key={index} data-testid="detail-unique">
                {item.name !== null ? (
                  <>
                    <Code value={item.name} />:{' '}
                  </>
                ) : null}
                <Names names={item.columns} />
              </li>
            ))}
          </ul>
        )}
        <More total={detail.uniqueConstraints.length} />
      </section>
      <section aria-labelledby="explorer-check">
        <h4 id="explorer-check" className={styles.subTitle}>
          {t('explorer.constraints.check')}
        </h4>
        {checks.length === 0 ? (
          <p className={connectionStyles.hint}>{t('explorer.detail.none')}</p>
        ) : (
          <ul className={styles.plainList}>
            {checks.map((item, index) => (
              <li key={index} data-testid="detail-check">
                {item.name !== null ? (
                  <>
                    <Code value={item.name} />:{' '}
                  </>
                ) : null}
                {item.sqltext === null ? (
                  t('explorer.detail.unknown')
                ) : (
                  <Code value={item.sqltext} cap={CAP_TEXT} />
                )}
              </li>
            ))}
          </ul>
        )}
        <More total={detail.checkConstraints.length} />
      </section>
    </div>
  );
}

function IndexesTab({ detail }: { detail: TableDetail }) {
  const { t } = useTranslation();
  if (detail.indexes.length === 0) {
    return <p className={connectionStyles.hint}>{t('explorer.indexes.none')}</p>;
  }
  return (
    <>
      <Grid caption={t('explorer.tabs.indexes')}>
        <thead>
          <tr>
            <th scope="col">{t('explorer.indexes.name')}</th>
            <th scope="col">{t('explorer.indexes.columns')}</th>
            <th scope="col">{t('explorer.indexes.unique')}</th>
          </tr>
        </thead>
        <tbody>
          {detail.indexes.slice(0, MAX_DETAIL_ROWS).map((index, position) => (
            <tr key={position} data-testid="detail-index">
              <th scope="row">
                {index.name === null ? t('explorer.detail.unnamed') : <Code value={index.name} />}
              </th>
              <td>
                <Names names={index.columns} />
              </td>
              <td>{yesNo(t, index.unique)}</td>
            </tr>
          ))}
        </tbody>
      </Grid>
      <More total={detail.indexes.length} />
    </>
  );
}

function RelationshipsTab({
  detail,
  find,
  onFollow,
}: {
  detail: TableDetail;
  find: (schema: string, name: string) => CatalogObject | null;
  onFollow: Follow;
}) {
  const { t } = useTranslation();
  const outgoing = detail.foreignKeys.slice(0, MAX_DETAIL_ROWS);
  const incoming = detail.referencedBy.slice(0, MAX_DETAIL_ROWS);
  return (
    <div className={styles.sections}>
      <section aria-labelledby="explorer-references">
        <h4 id="explorer-references" className={styles.subTitle}>
          {t('explorer.relationships.references')}
        </h4>
        {outgoing.length === 0 ? (
          <p className={connectionStyles.hint}>{t('explorer.detail.none')}</p>
        ) : (
          <ul className={styles.plainList}>
            {outgoing.map((key, index) => (
              <li key={index} data-testid="detail-references">
                <Names names={key.columns} /> →{' '}
                <Related
                  schema={key.referredSchema}
                  table={key.referredTable}
                  find={find}
                  onFollow={onFollow}
                />{' '}
                (<Names names={key.referredColumns} />)
              </li>
            ))}
          </ul>
        )}
        <More total={detail.foreignKeys.length} />
      </section>
      <section aria-labelledby="explorer-referenced">
        <h4 id="explorer-referenced" className={styles.subTitle}>
          {t('explorer.relationships.referencedBy')}
        </h4>
        {incoming.length === 0 ? (
          <p className={connectionStyles.hint}>{t('explorer.detail.none')}</p>
        ) : (
          <ul className={styles.plainList}>
            {incoming.map((key, index) => (
              <li key={index} data-testid="detail-referenced-by">
                <Related schema={key.schema} table={key.table} find={find} onFollow={onFollow} /> (
                <Names names={key.columns} />) → <Names names={key.referredColumns} />
              </li>
            ))}
          </ul>
        )}
        <More total={detail.referencedBy.length} />
      </section>
    </div>
  );
}

function SqliteTab({ name, info }: { name: string; info: SqliteInfo | null }) {
  const { t } = useTranslation();
  const facts = info?.tables.find((table) => table.name === name) ?? null;
  if (facts === null)
    return <p className={connectionStyles.hint}>{t('explorer.sqlite.noFacts')}</p>;
  return (
    <dl className={connectionStyles.details} data-testid="detail-sqlite">
      <dt>
        <code>STRICT</code>
      </dt>
      <dd>{yesNo(t, facts.strict)}</dd>
      <dt>
        <code>WITHOUT ROWID</code>
      </dt>
      <dd>{yesNo(t, facts.withoutRowid)}</dd>
    </dl>
  );
}

type TabId =
  'columns' | 'constraints' | 'indexes' | 'relationships' | 'sqlite' | 'ddl' | 'policies';

/** WAI-ARIA tabs with automatic activation: ← → Home End move between them. */
function Tabs({
  label,
  tabs,
  active,
  onChange,
  panel,
}: {
  label: string;
  tabs: { id: TabId; label: string }[];
  active: TabId;
  onChange: (id: TabId) => void;
  panel: ReactNode;
}) {
  const base = useId();
  const refs = useRef(new Map<TabId, HTMLButtonElement>());
  const current = tabs.some((tab) => tab.id === active) ? active : (tabs[0]?.id ?? 'columns');
  const go = (index: number) => {
    const tab = tabs[(index + tabs.length) % tabs.length];
    if (tab === undefined) return;
    onChange(tab.id);
    refs.current.get(tab.id)?.focus();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = tabs.findIndex((tab) => tab.id === current);
    const moves: Record<string, number> = {
      ArrowRight: index + 1,
      ArrowLeft: index - 1,
      Home: 0,
      End: tabs.length - 1,
    };
    const next = moves[event.key];
    if (next === undefined) return;
    event.preventDefault();
    go(next);
  };
  return (
    <>
      <div role="tablist" aria-label={label} className={styles.tabs}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            ref={(element) => {
              if (element === null) refs.current.delete(tab.id);
              else refs.current.set(tab.id, element);
            }}
            type="button"
            role="tab"
            id={`${base}-${tab.id}`}
            aria-selected={tab.id === current}
            aria-controls={`${base}-panel`}
            tabIndex={tab.id === current ? 0 : -1}
            className={styles.tab}
            onClick={() => onChange(tab.id)}
            onKeyDown={onKeyDown}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`${base}-panel`}
        aria-labelledby={`${base}-${current}`}
        tabIndex={0}
        className={styles.panel}
        data-testid="detail-panel"
      >
        {panel}
      </div>
    </>
  );
}

const KIND_KEYS: Record<ObjectKind, string> = {
  table: 'explorer.kind.table',
  view: 'explorer.kind.view',
  materialized_view: 'explorer.kind.materialized_view',
  sequence: 'explorer.kind.sequence',
  foreign_table: 'explorer.kind.foreign_table',
  other: 'explorer.kind.other',
};

export function kindName(t: TFunction, kind: ObjectKind): string {
  return t(KIND_KEYS[kind]);
}

export function TableView({
  detail,
  engine,
  sqliteInfo,
  tab,
  onTab,
  find,
  onFollow,
  fetcher,
  connectionId,
}: {
  detail: TableDetail;
  engine: Engine;
  sqliteInfo: SqliteInfo | null;
  tab: TabId;
  onTab: (tab: TabId) => void;
  find: (schema: string, name: string) => CatalogObject | null;
  onFollow: Follow;
  fetcher: typeof fetch;
  /** The explorer's open connection: the tabs that read (DDL, policies) read on it and never open one. */
  connectionId: string;
}) {
  const { t, i18n } = useTranslation();
  // The STU-022 panels read on the explorer's connection; one panel per connection and object (stale answers dropped).
  const target = { fetcher, connectionId, schema: detail.schema, name: detail.name };
  // JSON, not a separator: names may hold any character (L-2 of stu-022-page-review.md).
  const targetKey = JSON.stringify([connectionId, detail.schema, detail.name]);
  if (detail.kind === 'sequence') {
    return (
      <>
        <p className={connectionStyles.hint}>{t('explorer.detail.sequence')}</p>
        <DdlView key={targetKey} {...target} />
      </>
    );
  }
  const tabs: { id: TabId; label: string }[] = [
    { id: 'columns', label: t('explorer.tabs.columns') },
    { id: 'constraints', label: t('explorer.tabs.constraints') },
    { id: 'indexes', label: t('explorer.tabs.indexes') },
    { id: 'relationships', label: t('explorer.tabs.relationships') },
    ...(engine === 'sqlite' ? [{ id: 'sqlite' as const, label: t('explorer.tabs.sqlite') }] : []),
    { id: 'ddl', label: t('explorer.tabs.ddl') },
    ...(engine === 'postgresql' && detail.kind === 'table'
      ? [{ id: 'policies' as const, label: t('explorer.tabs.policies') }]
      : []),
  ];
  const current = tabs.some((item) => item.id === tab) ? tab : 'columns';
  let panel: ReactNode;
  switch (current) {
    case 'constraints':
      panel = <ConstraintsTab detail={detail} find={find} onFollow={onFollow} />;
      break;
    case 'indexes':
      panel = <IndexesTab detail={detail} />;
      break;
    case 'relationships':
      panel = <RelationshipsTab detail={detail} find={find} onFollow={onFollow} />;
      break;
    case 'sqlite':
      panel = <SqliteTab name={detail.name} info={sqliteInfo} />;
      break;
    case 'ddl':
      panel = <DdlView key={targetKey} {...target} />;
      break;
    case 'policies':
      panel = <PoliciesView key={targetKey} {...target} />;
      break;
    default:
      panel = <ColumnsTab detail={detail} />;
  }
  return (
    <>
      {detail.rowEstimate !== null ? (
        <p className={connectionStyles.hint} data-testid="detail-estimate">
          {t('explorer.detail.estimate', {
            count: detail.rowEstimate,
            rows: new Intl.NumberFormat(i18n.language).format(detail.rowEstimate),
          })}
        </p>
      ) : null}
      {engine === 'sqlite' && (detail.kind === 'table' || detail.kind === 'view') ? (
        <RowCount key={targetKey} {...target} />
      ) : null}
      <Tabs
        label={t('explorer.tabs.label', { name: cutText(`${detail.schema}.${detail.name}`) })}
        tabs={tabs}
        active={current}
        onChange={onTab}
        panel={panel}
      />
    </>
  );
}

export type { TabId };

export function ExtensionsView({ extensions }: { extensions: Extension[] }) {
  const { t } = useTranslation();
  if (extensions.length === 0) {
    return <p className={connectionStyles.hint}>{t('explorer.extensions.none')}</p>;
  }
  return (
    <>
      <Grid caption={t('explorer.extensions.title')}>
        <thead>
          <tr>
            <th scope="col">{t('explorer.extensions.name')}</th>
            <th scope="col">{t('explorer.extensions.version')}</th>
            <th scope="col">{t('explorer.extensions.schema')}</th>
            <th scope="col">{t('explorer.extensions.available')}</th>
          </tr>
        </thead>
        <tbody>
          {extensions.slice(0, MAX_DETAIL_ROWS).map((item, index) => (
            <tr key={index} data-testid="detail-extension">
              <th scope="row">
                <Code value={item.name} />
              </th>
              <td>
                {item.version === null ? (
                  t('explorer.detail.unknown')
                ) : (
                  <Code value={item.version} cap={CAP_TYPE} />
                )}
              </td>
              <td>{item.schema === null ? '' : <Code value={item.schema} />}</td>
              <td>
                {item.updateAvailable && item.defaultVersion !== null ? (
                  <span className={styles.update}>
                    {t('explorer.extensions.update', {
                      version: cutText(item.defaultVersion, CAP_TYPE),
                    })}
                  </span>
                ) : (
                  t('explorer.extensions.current')
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </Grid>
      <More total={extensions.length} />
    </>
  );
}

export function FileView({ info }: { info: SqliteInfo }) {
  const { t, i18n } = useTranslation();
  const number = (value: number | null) =>
    value === null
      ? t('explorer.detail.unknown')
      : new Intl.NumberFormat(i18n.language).format(value);
  return (
    <dl className={connectionStyles.details} data-testid="detail-file">
      <dt>
        <code>journal_mode</code>
      </dt>
      <dd>
        {info.journalMode === null ? (
          t('explorer.detail.unknown')
        ) : (
          <Code value={info.journalMode} />
        )}
      </dd>
      <dt>
        <code>user_version</code>
      </dt>
      <dd>{info.userVersion === null ? t('explorer.detail.unknown') : String(info.userVersion)}</dd>
      <dt>
        <code>page_size</code>
      </dt>
      <dd>{number(info.pageSize)}</dd>
      <dt>
        <code>page_count</code>
      </dt>
      <dd>{number(info.pageCount)}</dd>
      <dt>
        <code>foreign_keys</code>
      </dt>
      <dd>
        {info.foreignKeys === null
          ? t('explorer.detail.unknown')
          : info.foreignKeys
            ? t('explorer.file.on')
            : t('explorer.file.off')}
      </dd>
      <dt>{t('explorer.file.size')}</dt>
      <dd>
        {info.fileSize === null
          ? t('explorer.detail.unknown')
          : t('explorer.file.bytes', { count: info.fileSize, size: number(info.fileSize) })}
      </dd>
      <dt>{t('explorer.file.mode')}</dt>
      <dd>
        {info.fileMode === null ? t('explorer.detail.unknown') : <code>{info.fileMode}</code>}
      </dd>
    </dl>
  );
}
