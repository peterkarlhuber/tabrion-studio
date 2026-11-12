/**
 * The DDL and policies tabs of the explorer and SQLite's row count on request (STU-022, 03 section 2 B, 08 section
 * 4.3). Each reads one GET route of the explorer's open connection and nothing else: no connection is opened here, no
 * statement is written by the page, and there is no action that changes the database (D-02, STU-026 stays locked).
 *
 * - Every text from the server (DDL, notes, names, policy expressions) is escaped (`lib/visible.ts`, U-1). The DDL
 *   keeps its line breaks, every other control character shows as `\uXXXX`, and it is cut after MAX_DDL_CHARS with a
 *   visible note; "Copy DDL" copies the exact text (copying is not a write).
 * - Each panel is keyed per connection and object by its parent, and drops an answer that arrives after it is gone;
 *   an answer for another connection or object is refused by the API (`fetchDdl`, I-1).
 */
import type { TFunction } from 'i18next';
import { Copy, Hash } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  fetchDdl,
  fetchPolicies,
  fetchRowCount,
  type Ddl,
  type Policies,
  type RowCount as RowCountValue,
} from '../../api/explorer';
import type { Problem } from '../../api/problem';
import { Button } from '../../components/ui/Button';
import { capped, visible } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { problemText } from '../connections/messages';
import styles from './Definitions.module.css';
import explorerStyles from './Explorer.module.css';
import { CAP_TEXT, cutText, MAX_DETAIL_ROWS, Value } from './ObjectDetail';

/** How much of a DDL is shown, in characters of the server's text; "Copy DDL" always copies all of it. */
export const MAX_DDL_CHARS = 100_000;
/** How long a policy expression (USING, WITH CHECK) is shown, in escaped characters. */
export const CAP_EXPRESSION = 1000;

interface Target {
  fetcher: typeof fetch;
  /** The explorer's open connection: the panel reads on it and never opens one. */
  connectionId: string;
  schema: string;
  name: string;
}

type Loaded<T> =
  { status: 'loading' } | { status: 'problem'; problem: Problem } | { status: 'ready'; value: T };

/** One read when mounted; an answer after unmounting (another object, tab or connection) is dropped. */
function useRead<T>(
  read: () => Promise<{ ok: true; value: T } | { ok: false; problem: Problem }>,
): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ status: 'loading' });
  const once = useRef(read);
  useEffect(() => {
    let live = true;
    void once.current().then((answer) => {
      if (!live) return;
      setState(
        answer.ok
          ? { status: 'ready', value: answer.value }
          : { status: 'problem', problem: answer.problem },
      );
    });
    return () => {
      live = false;
    };
  }, []);
  return state;
}

function Pending<T>({ state, loading }: { state: Loaded<T>; loading: string }) {
  const { t } = useTranslation();
  if (state.status === 'loading') return <p className={connectionStyles.hint}>{loading}</p>;
  if (state.status === 'problem') {
    return (
      <p role="alert" className={connectionStyles.problem} data-testid="definition-problem">
        {problemText(t, state.problem)}
      </p>
    );
  }
  return null;
}

/** The DDL as shown: escaped per line (line breaks kept), cut after MAX_DDL_CHARS. */
export function shownDdl(ddl: string): { text: string; cut: boolean } {
  const chars = Array.from(ddl);
  const cut = chars.length > MAX_DDL_CHARS;
  const kept = cut ? chars.slice(0, MAX_DDL_CHARS).join('') : ddl;
  return { text: kept.split('\n').map(visible).join('\n'), cut };
}

export function DdlView({ fetcher, connectionId, schema, name }: Target) {
  const { t, i18n } = useTranslation();
  const state = useRead(() => fetchDdl(fetcher, connectionId, schema, name));
  const [copied, setCopied] = useState('');
  if (state.status !== 'ready')
    return <Pending state={state} loading={t('explorer.ddl.loading')} />;
  const ddl: Ddl = state.value;
  const shown = shownDdl(ddl.ddl);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(ddl.ddl);
      setCopied(t('explorer.ddl.copied'));
    } catch {
      setCopied(t('explorer.ddl.copyFailed'));
    }
  };
  return (
    <div className={explorerStyles.sections} data-testid="detail-ddl">
      {ddl.owner !== null ? (
        <p className={connectionStyles.hint} data-testid="ddl-owner">
          {t('explorer.ddl.owner', {
            owner: cutText(ddl.owner),
            interpolation: { escapeValue: false },
          })}
        </p>
      ) : null}
      {ddl.notes.length > 0 ? (
        <ul className={explorerStyles.plainList} data-testid="ddl-notes">
          {ddl.notes.slice(0, MAX_DETAIL_ROWS).map((note, index) => (
            <li key={index} className={connectionStyles.hint}>
              <Value value={note} cap={CAP_TEXT} />
            </li>
          ))}
        </ul>
      ) : null}
      {ddl.statements.length === 0 ? (
        <p className={connectionStyles.hint} data-testid="ddl-empty">
          {t('explorer.ddl.empty')}
        </p>
      ) : (
        <>
          <pre
            className={styles.ddl}
            role="region"
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a region that scrolls must take the focus (axe scrollable-region-focusable, WCAG 2.1.1)
            tabIndex={0}
            aria-label={t('explorer.ddl.label', {
              name: cutText(`${schema}.${name}`),
              interpolation: { escapeValue: false },
            })}
            data-testid="ddl-text"
          >
            {shown.text}
          </pre>
          {shown.cut ? (
            <p className={connectionStyles.warning} data-testid="ddl-cut">
              {t('explorer.ddl.cut', {
                chars: new Intl.NumberFormat(i18n.language).format(MAX_DDL_CHARS),
              })}
            </p>
          ) : null}
          <p className={connectionStyles.hint}>{t('explorer.ddl.escaped')}</p>
          <div className={connectionStyles.actions}>
            <Button size="sm" icon={<Copy />} onClick={() => void copy()} data-testid="ddl-copy">
              {t('explorer.ddl.copy')}
            </Button>
          </div>
          <p role="status" className={connectionStyles.hint} data-testid="ddl-copy-status">
            {copied}
          </p>
        </>
      )}
    </div>
  );
}

function Expression({ value }: { value: string | null }) {
  if (value === null) return null;
  const [shown, cut] = capped(value, CAP_EXPRESSION);
  return (
    <code>
      {shown}
      {cut ? <span aria-hidden="true">…</span> : null}
    </code>
  );
}

/** Why the policies do or do not bind the connection's role (the same order as PostgreSQL's own check). */
export function rlsReason(t: TFunction, policies: Policies): string {
  const role = cutText(policies.currentRole.name ?? '?');
  const options = { role, interpolation: { escapeValue: false } };
  if (!policies.rlsEnabled) return t('explorer.policies.reason.off');
  if (policies.currentRole.superuser) return t('explorer.policies.reason.superuser', options);
  if (policies.currentRole.bypassRls) return t('explorer.policies.reason.bypass', options);
  if (policies.currentRole.owner && !policies.rlsForced) {
    return t('explorer.policies.reason.owner', options);
  }
  if (policies.rlsApplies) return t('explorer.policies.reason.applies', options);
  return t('explorer.policies.reason.unknown', options);
}

export function PoliciesView({ fetcher, connectionId, schema, name }: Target) {
  const { t } = useTranslation();
  const state = useRead(() => fetchPolicies(fetcher, connectionId, schema, name));
  if (state.status !== 'ready') {
    return <Pending state={state} loading={t('explorer.policies.loading')} />;
  }
  const value = state.value;
  const shown = value.policies.slice(0, MAX_DETAIL_ROWS);
  const caption = t('explorer.tabs.policies');
  return (
    <div className={explorerStyles.sections} data-testid="detail-policies">
      <dl className={connectionStyles.details} data-testid="policies-facts">
        <dt>{t('explorer.policies.rls')}</dt>
        <dd data-testid="policies-rls">
          {value.rlsEnabled ? t('explorer.policies.on') : t('explorer.policies.off')}
        </dd>
        <dt>{t('explorer.policies.forced')}</dt>
        <dd>{value.rlsForced ? t('explorer.detail.yes') : t('explorer.detail.no')}</dd>
        <dt>{t('explorer.policies.owner')}</dt>
        <dd>
          {value.owner === null ? (
            t('explorer.detail.unknown')
          ) : (
            <code>{cutText(value.owner)}</code>
          )}
        </dd>
        <dt>{t('explorer.policies.role')}</dt>
        <dd>
          {value.currentRole.name === null ? (
            t('explorer.detail.unknown')
          ) : (
            <code>{cutText(value.currentRole.name)}</code>
          )}
        </dd>
      </dl>
      <p
        className={value.rlsApplies ? connectionStyles.status : connectionStyles.hint}
        data-testid="policies-applies"
      >
        {rlsReason(t, value)}
      </p>
      {value.rlsEnabled && value.policies.length === 0 ? (
        <p className={connectionStyles.warning} data-testid="policies-deny">
          {t('explorer.policies.denyAll')}
        </p>
      ) : null}
      {value.policies.length === 0 ? (
        <p className={connectionStyles.hint}>{t('explorer.policies.none')}</p>
      ) : (
        <>
          <div
            className={explorerStyles.gridScroll}
            role="region"
            aria-label={caption}
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a region that scrolls must take the focus (axe scrollable-region-focusable, WCAG 2.1.1)
            tabIndex={0}
          >
            <table className={explorerStyles.grid}>
              <caption className="tb-sr-only">{caption}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('explorer.policies.name')}</th>
                  <th scope="col">{t('explorer.policies.type')}</th>
                  <th scope="col">{t('explorer.policies.command')}</th>
                  <th scope="col">{t('explorer.policies.roles')}</th>
                  <th scope="col">USING</th>
                  <th scope="col">WITH CHECK</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((policy, index) => (
                  <tr key={index} data-testid="detail-policy">
                    <th scope="row">
                      <code>
                        <Value value={policy.name} />
                      </code>
                    </th>
                    <td>
                      {policy.permissive
                        ? t('explorer.policies.permissive')
                        : t('explorer.policies.restrictive')}
                    </td>
                    <td>
                      <code>{policy.command}</code>
                    </td>
                    <td>
                      {policy.roles.slice(0, MAX_DETAIL_ROWS).map((role, position) => (
                        <span key={position}>
                          {position > 0 ? ', ' : null}
                          {role === 'public' ? (
                            t('explorer.policies.public')
                          ) : (
                            <code>
                              <Value value={role} />
                            </code>
                          )}
                        </span>
                      ))}
                      {policy.roles.length > MAX_DETAIL_ROWS
                        ? ` ${t('explorer.detail.more', { count: policy.roles.length - MAX_DETAIL_ROWS })}`
                        : null}
                    </td>
                    <td>
                      <Expression value={policy.using} />
                    </td>
                    <td>
                      <Expression value={policy.withCheck} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {value.policies.length > MAX_DETAIL_ROWS ? (
            <p className={connectionStyles.hint} data-testid="detail-more">
              {t('explorer.detail.more', { count: value.policies.length - MAX_DETAIL_ROWS })}
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}

type Counting =
  | { status: 'idle' }
  | { status: 'counting' }
  | { status: 'done'; value: RowCountValue }
  | { status: 'problem'; problem: Problem };

/** SQLite: `count(*)` on request (the server stops it after 10 s with `db.timeout`). */
export function RowCount({ fetcher, connectionId, schema, name }: Target) {
  const { t, i18n } = useTranslation();
  const [state, setState] = useState<Counting>({ status: 'idle' });
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const run = async () => {
    // aria-disabled keeps the button focusable while it counts; a second click starts nothing.
    if (state.status === 'counting') return;
    setState({ status: 'counting' });
    const answer = await fetchRowCount(fetcher, connectionId, schema, name);
    if (!live.current) return;
    setState(
      answer.ok
        ? { status: 'done', value: answer.value }
        : { status: 'problem', problem: answer.problem },
    );
  };
  const number = (value: number) => new Intl.NumberFormat(i18n.language).format(value);
  let result = '';
  if (state.status === 'counting') result = t('explorer.rowCount.counting');
  else if (state.status === 'done') {
    result =
      state.value.elapsedMs === null
        ? t('explorer.rowCount.result', {
            count: state.value.count,
            rows: number(state.value.count),
          })
        : t('explorer.rowCount.resultTimed', {
            count: state.value.count,
            rows: number(state.value.count),
            ms: number(state.value.elapsedMs),
          });
  } else if (state.status === 'problem') {
    result =
      state.problem.code === 'db.timeout'
        ? t('explorer.rowCount.timeout')
        : problemText(t, state.problem);
  }
  return (
    <div className={styles.count}>
      <Button
        size="sm"
        icon={<Hash />}
        onClick={() => void run()}
        aria-disabled={state.status === 'counting'}
        data-testid="row-count"
      >
        {t('explorer.rowCount.button')}
      </Button>
      <p
        role="status"
        className={state.status === 'problem' ? connectionStyles.problem : connectionStyles.hint}
        data-testid="row-count-result"
      >
        {result}
      </p>
    </div>
  );
}
