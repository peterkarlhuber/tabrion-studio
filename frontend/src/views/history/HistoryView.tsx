/**
 * The query history (STU-020; 03 section 2 C, 08 sections 2 and 5): the statements run in the SQL editor, newest
 * first, per profile. An entry shows its statement with the `:name` placeholders and their names; the values a
 * query ran with are never stored (08 section 5, "Parameter nie in Historie"), so "Open in editor" opens the
 * statement in a new tab and the parameter panel asks for the values again. Nothing runs by itself.
 *
 * Profile names and statements are shown as text: invisible and bidi characters as visible escapes (`lib/visible`).
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  deleteHistory,
  fetchHistory,
  type HistoryEntry,
  type HistoryPage,
} from '../../api/history';
import type { Problem } from '../../api/problem';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import { EnvBadge } from '../../components/status/Status';
import { Button } from '../../components/ui/Button';
import { currentLocale } from '../../i18n';
import clsx from '../../lib/clsx';
import { visible } from '../../lib/visible';
import { navigate } from '../../router';
import connectionStyles from '../connections/Connections.module.css';
import { problemText } from '../connections/messages';
import { handToEditor } from '../editor/handoff';
import { formatCount, formatMs } from '../editor/format';
import viewStyles from '../Views.module.css';
import { DeleteHistoryDialog } from './HistoryDialogs';
import styles from './History.module.css';

const PAGE = 50;
const SEARCH_DELAY_MS = 250;
/** How much of a statement an entry shows; "Open in editor" opens all of it. */
const PREVIEW_CHARS = 2_000;

/** The statement as text: each line through `visible`, so nothing invisible can make it read differently. */
export function shownSql(sql: string): string {
  const preview = sql.length > PREVIEW_CHARS ? `${sql.slice(0, PREVIEW_CHARS)} …` : sql;
  return preview
    .split(/\r\n|\n/)
    .map((line) => visible(line))
    .join('\n');
}

export function HistoryView({ fetcher = fetch }: { fetcher?: typeof fetch }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language || currentLocale();
  const prefix = useId().replace(/:/g, '');
  const [profiles, setProfiles] = useState<ProfileItem[]>([]);
  const [profileId, setProfileId] = useState('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState<HistoryPage | null | 'loading'>('loading');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [notice, setNotice] = useState('');
  const [confirm, setConfirm] = useState<'all' | 'profile' | null>(null);
  const request = useRef(0);

  useEffect(() => {
    let active = true;
    void fetchProfiles(fetcher).then((list) => {
      if (active) setProfiles(list?.profiles ?? []);
    });
    return () => {
      active = false;
    };
  }, [fetcher]);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [search]);

  /** Read a page (`before`: the next one) and show it, unless a newer request was sent meanwhile. */
  const load = useCallback(
    (before?: number) => {
      const own = ++request.current;
      return fetchHistory(fetcher, {
        profileId: profileId || undefined,
        q: query || undefined,
        limit: PAGE,
        before,
      }).then((result) => {
        if (own !== request.current) return;
        if (!result.ok) {
          setProblem(result.problem);
          if (before === undefined) setPage(null);
          return;
        }
        setProblem(null);
        setPage((current) =>
          before !== undefined && current !== null && current !== 'loading'
            ? { ...result.value, entries: [...current.entries, ...result.value.entries] }
            : result.value,
        );
      });
    },
    [fetcher, profileId, query],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const names = useMemo(() => new Map(profiles.map((item) => [item.id, item])), [profiles]);
  const nameOf = (id: string) => {
    const item = names.get(id);
    return item === undefined ? visible(id) : visible(item.name);
  };
  const time = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'medium' });

  const remove = async (entry: HistoryEntry) => {
    const result = await deleteHistory(fetcher, { id: entry.id });
    if (!result.ok) {
      setNotice(problemText(t, result.problem));
      return;
    }
    setNotice(t('history.deletedOne'));
    setPage((current) =>
      current === null || current === 'loading'
        ? current
        : { ...current, entries: current.entries.filter((item) => item.id !== entry.id) },
    );
  };

  const removeMany = async () => {
    const which = confirm === 'profile' && profileId ? { profileId } : {};
    setConfirm(null);
    const result = await deleteHistory(fetcher, which);
    setNotice(
      result.ok ? t('history.deleted', { count: result.value }) : problemText(t, result.problem),
    );
    void load();
  };

  const open = (entry: HistoryEntry) => {
    handToEditor({
      profileId: names.has(entry.profileId) ? entry.profileId : '',
      title: t('history.tabTitle'),
      sql: entry.sql,
    });
    navigate('/sql');
  };

  const copy = async (entry: HistoryEntry) => {
    try {
      await navigator.clipboard.writeText(entry.sql);
      setNotice(t('editor.copied'));
    } catch {
      setNotice(t('editor.copyFailed'));
    }
  };

  const entries = page !== null && page !== 'loading' ? page.entries : [];
  const selected = names.get(profileId);

  return (
    <div className={viewStyles.page}>
      <div className={viewStyles.pageHeader}>
        <h1 className={viewStyles.title}>{t('history.title')}</h1>
        <p className={viewStyles.lead}>{t('history.lead')}</p>
      </div>
      {page !== null && page !== 'loading' && !page.enabled ? (
        <p className={connectionStyles.warning} data-testid="history-off">
          {t('history.off')}{' '}
          <a
            href="/settings"
            onClick={(event) => {
              event.preventDefault();
              navigate('/settings');
            }}
          >
            {t('history.offLink')}
          </a>
        </p>
      ) : null}
      <div className={styles.filters}>
        <label className={styles.filter} htmlFor={`${prefix}-profile`}>
          {t('history.profile')}
          <select
            id={`${prefix}-profile`}
            className={connectionStyles.input}
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
            data-testid="history-profile"
          >
            <option value="">{t('history.allProfiles')}</option>
            {profiles.map((item) => (
              <option key={item.id} value={item.id}>
                {`${visible(item.name)} (${item.environment.toUpperCase()})`}
              </option>
            ))}
          </select>
        </label>
        <label className={clsx(styles.filter, connectionStyles.search)} htmlFor={`${prefix}-q`}>
          {t('history.search')}
          <input
            id={`${prefix}-q`}
            type="search"
            className={connectionStyles.input}
            value={search}
            maxLength={200}
            onChange={(event) => setSearch(event.target.value)}
            data-testid="history-search"
          />
        </label>
        <Button
          variant="danger"
          disabled={entries.length === 0}
          onClick={() => setConfirm(profileId ? 'profile' : 'all')}
          data-testid="history-delete-all"
        >
          {profileId ? t('history.deleteProfile') : t('history.deleteAll')}
        </Button>
      </div>
      <p role="status" className={connectionStyles.hint} data-testid="history-notice">
        {notice}
      </p>
      {problem !== null ? (
        <p role="alert" className={connectionStyles.problem} data-testid="history-problem">
          {problemText(t, problem)}
        </p>
      ) : null}
      {page === 'loading' ? (
        <p className={connectionStyles.hint}>{t('history.loading')}</p>
      ) : entries.length === 0 && problem === null ? (
        <p className={connectionStyles.hint} data-testid="history-empty">
          {query || profileId ? t('history.noMatch') : t('history.empty')}
        </p>
      ) : (
        <ol className={styles.list} aria-label={t('history.list')} data-testid="history-list">
          {entries.map((entry) => {
            const headingId = `${prefix}-entry-${entry.id}`;
            return (
              <li
                key={entry.id}
                className={clsx(styles.entry, entry.environment === 'prod' && styles.prod)}
                aria-labelledby={headingId}
                data-testid="history-entry"
              >
                <p className={styles.meta} id={headingId}>
                  <span className={styles.profile}>{nameOf(entry.profileId)}</span>
                  <EnvBadge environment={entry.environment} size="sm" />
                  <span>{time.format(entry.finishedAt)}</span>
                  <span className={clsx(entry.state === 'failed' && styles.failed)}>
                    {entry.state === 'done'
                      ? t('history.state.done', {
                          rows: formatCount(entry.rowCount ?? 0, locale),
                          count: entry.rowCount ?? 0,
                        })
                      : t(`history.state.${entry.state}`)}
                  </span>
                  {entry.elapsedMs !== null ? (
                    <span>{formatMs(entry.elapsedMs, locale)}</span>
                  ) : null}
                </p>
                <pre className={styles.sql} data-testid="history-sql">
                  {shownSql(entry.sql)}
                </pre>
                {entry.sqlTruncated ? (
                  <p className={styles.params}>{t('history.truncated')}</p>
                ) : null}
                {entry.paramNames.length > 0 ? (
                  <p className={styles.params} data-testid="history-params">
                    {t('history.params', {
                      names: entry.paramNames.map((name) => `:${name}`).join(', '),
                      interpolation: { escapeValue: false },
                    })}
                  </p>
                ) : null}
                <div className={styles.actions}>
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => open(entry)}
                    data-testid="history-open"
                  >
                    {t('history.open')}
                    <span className="tb-sr-only"> ({time.format(entry.finishedAt)})</span>
                  </Button>
                  <Button size="sm" onClick={() => void copy(entry)}>
                    {t('history.copy')}
                    <span className="tb-sr-only"> ({time.format(entry.finishedAt)})</span>
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void remove(entry)}
                    data-testid="history-delete"
                  >
                    {t('history.delete')}
                    <span className="tb-sr-only"> ({time.format(entry.finishedAt)})</span>
                  </Button>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {page !== null && page !== 'loading' && page.nextBefore !== null ? (
        <div>
          <Button
            onClick={() => void load(page.nextBefore ?? undefined)}
            data-testid="history-more"
          >
            {t('history.more')}
          </Button>
        </div>
      ) : null}
      {confirm !== null ? (
        <DeleteHistoryDialog
          profileName={confirm === 'profile' && selected ? visible(selected.name) : undefined}
          onCancel={() => setConfirm(null)}
          onDelete={() => void removeMany()}
        />
      ) : null}
    </div>
  );
}
