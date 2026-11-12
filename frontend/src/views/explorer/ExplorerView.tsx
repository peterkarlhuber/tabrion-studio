/**
 * The explorer page (STU-010, 03 section 2 B, 08 section 4.3): the object tree of a connection's database, its filter
 * and the details of the selected object. Studio only reads here: there is no action that changes the database
 * (STU-026 stays locked), and no request but `POST /api/connections` (the read-only connection), the explorer's
 * GET routes and the data viewer's (the "Data" tab, STU-013). "SELECT … LIMIT 100 in the SQL editor" hands the
 * statement to the editor (`editor/handoff.ts`), which runs nothing by itself.
 *
 * - **Connecting:** like the migration and diagnostics pages, the page has its own profile picker until the
 *   connection workspace exists (TD-011, TD-041). "Open" opens the profile's read-only connection; a PROD profile
 *   gets D-02 first unless the user confirmed it in this page session (the shared confirmation of STU-008,
 *   `useConnectionSession`, bound to the profile's target). The opened connection becomes the header's connection.
 *   "Refresh" reads the catalog again on the open connection and never opens one.
 * - **Stale answers:** a profile switch, a new open or a refresh counts up `request`, a new selection counts up
 *   `detailRequest`; an answer is shown only for the latest of each (review L-9 and F-1 of #32).
 * - **Focus** never falls back to the document: the buttons stay mounted while they work (`aria-disabled`), and
 *   following a relationship moves the focus to the target's tree row (C-2 of stu-014-ui-review.md).
 * - **U-1:** every database name and text is escaped and capped (`lib/visible.ts`).
 */
import {
  Copy,
  Database,
  Eye,
  FileText,
  Folder,
  Globe,
  Hash,
  Layers,
  Puzzle,
  RefreshCw,
  Rows3,
  Shapes,
  SquareTerminal,
  Table2,
} from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import {
  fetchCatalog,
  fetchExtensions,
  fetchSqliteInfo,
  fetchTable,
  OBJECT_KINDS,
  type Catalog,
  type CatalogObject,
  type Extension,
  type ObjectKind,
  type SqliteInfo,
  type TableDetail,
} from '../../api/explorer';
import { openConnection } from '../../api/migrations';
import { OFFLINE, type Problem } from '../../api/problem';
import { PROD_CONFIRMATION_REQUIRED } from '../../api/connections';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import { ProdConnectDialog } from '../../components/dialogs/StudioDialogs';
import { EnvBadge } from '../../components/status/Status';
import { Button } from '../../components/ui/Button';
import { useKeyboardPlatform } from '../../keyboard/platform';
import { matches as matchesShortcut, SHORTCUTS } from '../../keyboard/shortcuts';
import { visible } from '../../lib/visible';
import { navigate } from '../../router';
import { handToEditor, selectStatement } from '../editor/handoff';
import connectionStyles from '../connections/Connections.module.css';
import { problemText } from '../connections/messages';
import { prodTimeoutSeconds } from '../connections/useConnect';
import { useConnectionSession } from '../../workspace';
import viewStyles from '../Views.module.css';
import styles from './Explorer.module.css';
import {
  cutText,
  DATA_KINDS,
  ExtensionsView,
  FileView,
  kindName,
  TableView,
  Value,
  type TabId,
} from './ObjectDetail';
import { ObjectTree, type FocusTarget } from './ObjectTree';
import {
  buildRows,
  EXTENSIONS_ID,
  FILE_ID,
  filtering,
  initialExpanded,
  LARGE_CATALOG,
  NO_FILTER,
  objectId,
  pathTo,
  type Filter,
  type TreeRow,
} from './tree';

/** The connection whose catalog is shown; name and environment are kept (the profile list may change). */
interface Opened {
  connectionId: string;
  /** The profile the connection was opened for (the SQL editor gets it with "SELECT … LIMIT 100"). */
  profileId: string;
  name: string;
  environment: ProfileItem['environment'];
}

type Selection =
  { type: 'object'; object: CatalogObject } | { type: 'extensions' } | { type: 'file' };

type Detail =
  | { status: 'loading' }
  | { status: 'problem'; problem: Problem }
  | { status: 'table'; detail: TableDetail }
  | { status: 'extensions'; extensions: Extension[] }
  | { status: 'file'; info: SqliteInfo };

type Phase = 'idle' | 'opening' | 'loading';

/** A connection that is gone: the page forgets it (and the header too, if it shows it); "Open" opens a new one. */
const GONE = new Set(['studio.connection_not_found', 'studio.connection_stale']);

const KIND_ICONS: Record<ObjectKind, ReactNode> = {
  table: <Table2 />,
  view: <Eye />,
  materialized_view: <Layers />,
  sequence: <Hash />,
  foreign_table: <Globe />,
  other: <Shapes />,
};

function selectionId(selection: Selection | null): string | null {
  if (selection === null) return null;
  if (selection.type === 'object') return objectId(selection.object.schema, selection.object.name);
  return selection.type === 'extensions' ? EXTENSIONS_ID : FILE_ID;
}

/** "HH:MM:SS" of a valid time; the parser has already refused one `Date` cannot show. */
function timeText(locale: string, ms: number): string {
  try {
    return new Intl.DateTimeFormat(locale, { timeStyle: 'medium' }).format(new Date(ms));
  } catch {
    return '';
  }
}

export function ExplorerView({ fetcher = fetch }: { fetcher?: typeof fetch }) {
  const { t, i18n } = useTranslation();
  const platform = useKeyboardPlatform();
  const selectId = useId();
  const filterId = useId();
  const kindId = useId();
  const [profiles, setProfiles] = useState<ProfileItem[] | null>(null);
  const [selected, setSelected] = useState('');
  const [opened, setOpened] = useState<Opened | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [sqliteInfo, setSqliteInfo] = useState<SqliteInfo | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [message, setMessage] = useState('');
  /** The profile D-02 asks about (as the server listed it when Open was pressed), or null. */
  const [dialog, setDialog] = useState<ProfileItem | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [filter, setFilter] = useState<Filter>(NO_FILTER);
  const [selection, setSelectionState] = useState<Selection | null>(null);
  /** The selection for handlers that finish later (a refresh): never the one of the render that started them. */
  const selectionRef = useRef<Selection | null>(null);
  const setSelection = (next: Selection | null) => {
    selectionRef.current = next;
    setSelectionState(next);
  };
  const [detail, setDetail] = useState<Detail | null>(null);
  const [tab, setTab] = useState<TabId>('columns');
  const [focused, setFocused] = useState<string | null>(null);
  const [focusTarget, setFocusTarget] = useState<FocusTarget | null>(null);
  /** Counts opens, refreshes and profile switches; a catalog answer is shown only for the latest. */
  const request = useRef(0);
  /** Counts selections (and everything that counts up `request`); a detail answer is shown only for the latest. */
  const detailRequest = useRef(0);
  const filterInput = useRef<HTMLInputElement>(null);
  const profileSelect = useRef<HTMLSelectElement>(null);
  const problemRef = useRef<HTMLParagraphElement>(null);
  /** Set when Open found its profile gone: after the next render the focus goes to the picker (or the problem). */
  const refocus = useRef(false);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const openButton = useRef<HTMLButtonElement>(null);
  /** An Open press whose profile list is still being read; a second press meanwhile does nothing. */
  const checking = useRef(false);
  const connections = useConnectionSession();

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
    };
  }, [fetcher]);

  // After Open found its profile gone: the picker (or, with no profile left, the problem) takes the focus.
  useEffect(() => {
    if (!refocus.current || profiles === null) return;
    refocus.current = false;
    (profileSelect.current ?? problemRef.current)?.focus();
  }, [profiles, selected]);

  // Leaving the page drops every answer still on its way.
  useEffect(
    () => () => {
      request.current += 1;
      detailRequest.current += 1;
    },
    [],
  );

  // ⌘P / Ctrl+P: the explorer filter (08 section 7); before a catalog is open, the profile picker.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return;
      if (document.querySelector('dialog[open]') !== null) return;
      if (!matchesShortcut(event, SHORTCUTS.explorerFilter, platform)) return;
      event.preventDefault();
      (filterInput.current ?? profileSelect.current)?.focus();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [platform]);

  const profile = profiles?.find((item) => item.id === selected) ?? null;
  const busy = phase !== 'idle';

  const find = useCallback(
    (schema: string, name: string): CatalogObject | null =>
      catalog?.objects.find((item) => item.schema === schema && item.name === name) ?? null,
    [catalog],
  );

  /** Load the details of `next` on the open connection `target`. */
  const loadDetail = async (target: Opened, next: Selection) => {
    const mine = ++detailRequest.current;
    setSelection(next);
    setDetail({ status: 'loading' });
    let result: Detail;
    if (next.type === 'object') {
      const answer = await fetchTable(
        fetcher,
        target.connectionId,
        next.object.schema,
        next.object.name,
      );
      result = answer.ok
        ? { status: 'table', detail: answer.value }
        : { status: 'problem', problem: answer.problem };
    } else if (next.type === 'extensions') {
      const answer = await fetchExtensions(fetcher, target.connectionId);
      result = answer.ok
        ? { status: 'extensions', extensions: answer.value }
        : { status: 'problem', problem: answer.problem };
    } else {
      const answer = await fetchSqliteInfo(fetcher, target.connectionId);
      result = answer.ok
        ? { status: 'file', info: answer.value }
        : { status: 'problem', problem: answer.problem };
      if (answer.ok && mine === detailRequest.current) setSqliteInfo(answer.value);
    }
    if (mine !== detailRequest.current) return;
    setDetail(result);
  };

  /** The connection `target` is gone: the page drops it (and the header too, if it shows it). */
  const forget = (target: Opened) => {
    request.current += 1;
    detailRequest.current += 1;
    setPhase('idle');
    setOpened(null);
    setCatalog(null);
    setSelection(null);
    setDetail(null);
    // The workspace (and the focused Refresh button) goes away: the focus moves to Open, which stays mounted
    // (L-2 of stu-010-ui-review.md).
    openButton.current?.focus();
    connections.setActive((current) =>
      current?.connectionId === target.connectionId ? null : current,
    );
  };

  /** The data tab found the connection gone (STU-013): the page drops it and says why. */
  const dataGone = (problem: Problem) => {
    if (opened === null) return;
    setProblem(problem);
    setMessage('');
    forget(opened);
  };

  /** Read the catalog of `target` for the request `mine`; `refresh` reads it again on the server too. */
  const loadCatalog = async (mine: number, target: Opened, refresh: boolean) => {
    setPhase('loading');
    setMessage(t('explorer.loading'));
    const result = await fetchCatalog(fetcher, target.connectionId, refresh);
    if (mine !== request.current) return;
    if (!result.ok) {
      setPhase('idle');
      setProblem(result.problem);
      setMessage('');
      if (GONE.has(result.problem.code)) forget(target);
      return;
    }
    const next = result.value;
    let info: SqliteInfo | null = null;
    if (next.engine === 'sqlite') {
      const facts = await fetchSqliteInfo(fetcher, target.connectionId);
      if (mine !== request.current) return;
      info = facts.ok ? facts.value : null;
    }
    setPhase('idle');
    setOpened(target);
    setCatalog(next);
    setSqliteInfo(info);
    setMessage(t('explorer.loaded', { count: next.objects.length }));
    if (!refresh) {
      detailRequest.current += 1;
      setExpanded(initialExpanded(next));
      setFilter(NO_FILTER);
      setSelection(null);
      setDetail(null);
      setFocused(null);
      return;
    }
    // A refresh keeps the selection when it is still there and reads its details again.
    const selection = selectionRef.current;
    if (selection?.type === 'object') {
      const still = next.objects.find(
        (item) => item.schema === selection.object.schema && item.name === selection.object.name,
      );
      if (still === undefined) {
        detailRequest.current += 1;
        setSelection(null);
        setDetail(null);
        setMessage(
          `${t('explorer.loaded', { count: next.objects.length })} ${t('explorer.selectionGone')}`,
        );
      } else void loadDetail(target, { type: 'object', object: still });
    } else if (selection !== null) void loadDetail(target, selection);
  };

  /**
   * "Open": read the profile list again first, so that the PROD check and D-02 use the profile as it is now, not as
   * it was when the page loaded (a profile changed to `prod` on disk since; TD-038). If the list cannot be read, the
   * loaded profile decides. A profile switch meanwhile drops the press.
   */
  const start = async () => {
    if (profile === null || busy || checking.current) return;
    checking.current = true;
    const asked = request.current;
    const loaded = profile;
    setPhase('opening');
    const list = await fetchProfiles(fetcher);
    checking.current = false;
    if (asked !== request.current) return;
    // Fail closed: without the current list the PROD check cannot be made, so nothing is opened.
    if (list === null) {
      setPhase('idle');
      setProblem(OFFLINE);
      return;
    }
    setProfiles(list.profiles);
    const item = list.profiles.find((entry) => entry.id === loaded.id);
    if (item === undefined) {
      // The page switches to the first remaining profile, as a pick in the picker would: its value matches an
      // option, and the catalog of the deleted profile is no longer shown. The picker then takes the focus.
      switchProfile(list.profiles[0]?.id ?? '');
      setProblem({ code: 'studio.profile_not_found', kind: 'not_found', status: 404, logId: null });
      refocus.current = true;
      return;
    }
    if (item.environment === 'prod' && !connections.prodConfirmed(item)) {
      setPhase('idle');
      setDialog(item);
    } else void open(item);
  };

  const open = async (profile: ProfileItem) => {
    const target = { profileId: profile.id, name: profile.name, environment: profile.environment };
    const mine = ++request.current;
    detailRequest.current += 1;
    setPhase('opening');
    setProblem(null);
    setOpened(null);
    setCatalog(null);
    setSelection(null);
    setDetail(null);
    setMessage(t('explorer.opening'));
    const connecting = connections.beginConnect();
    // The environment this page confirmed for (D-02 on the server, TD-038).
    const result = await openConnection(fetcher, profile.id, profile.environment);
    if (mine !== request.current) return;
    // Only the newest connect request of the page sets the header (L-4 of stu-008-connect-review.md).
    if (result.ok && connections.isLatestConnect(connecting)) {
      connections.setActive({
        name: visible(profile.name),
        environment: profile.environment,
        status: 'connected',
        connectionId: result.value,
        profileId: profile.id,
      });
    }
    if (!result.ok) {
      setPhase('idle');
      setProblem(result.problem);
      setMessage('');
      // The profile became PROD after this page read it: read the profiles again, so the next try asks D-02.
      if (result.problem.code === PROD_CONFIRMATION_REQUIRED) {
        void fetchProfiles(fetcher).then((list) => {
          if (list !== null) setProfiles(list.profiles);
        });
      }
      return;
    }
    await loadCatalog(mine, { ...target, connectionId: result.value }, false);
  };

  /** Read the catalog again on the open connection: never opens one, so D-02 cannot be skipped. */
  const refresh = async () => {
    if (opened === null || busy) return;
    const mine = ++request.current;
    setProblem(null);
    await loadCatalog(mine, opened, true);
  };

  const switchProfile = (id: string) => {
    request.current += 1;
    detailRequest.current += 1;
    setSelected(id);
    setPhase('idle');
    setProblem(null);
    setMessage('');
    setOpened(null);
    setCatalog(null);
    setSqliteInfo(null);
    setSelection(null);
    setDetail(null);
    setFilter(NO_FILTER);
    setFocused(null);
  };

  const select = (next: Selection) => {
    if (opened === null) return;
    void loadDetail(opened, next);
  };

  /** A relationship's target: open its groups, select it and put the focus on its row. */
  const follow = (object: CatalogObject) => {
    if (catalog === null) return;
    const id = objectId(object.schema, object.name);
    setExpanded((current) => new Set([...current, ...pathTo(catalog, object)]));
    setFilter((current) =>
      filtering(current) &&
      !buildRows(catalog, new Set(), current).rows.some((row) => row.id === id)
        ? NO_FILTER
        : current,
    );
    setFocused(id);
    setFocusTarget((current) => ({ id, seq: (current?.seq ?? 0) + 1 }));
    select({ type: 'object', object });
  };

  const tree = useMemo(
    () => (catalog === null ? null : buildRows(catalog, expanded, filter)),
    [catalog, expanded, filter],
  );

  // The row asked for is not rendered (a group beyond MAX_GROUP_ITEMS): the focus goes to the details instead.
  useEffect(() => {
    if (focusTarget === null || tree === null) return;
    if (!tree.rows.some((row) => row.id === focusTarget.id)) detailHeading.current?.focus();
  }, [focusTarget, tree]);

  const rowText = (row: TreeRow): string => {
    switch (row.type) {
      case 'schema':
        return row.schema ?? '';
      case 'group':
        return t(`explorer.groups.${row.kind ?? 'other'}`);
      case 'object':
        return row.object?.name ?? '';
      case 'extensions':
        return t('explorer.tree.extensions');
      default:
        return t('explorer.tree.file');
    }
  };

  const rowContent = (row: TreeRow): ReactNode => {
    switch (row.type) {
      case 'schema':
        return (
          <>
            <Value value={row.schema ?? ''} />{' '}
            <span className={styles.count}>
              {row.count === 0 ? t('explorer.tree.empty') : `(${row.count ?? 0})`}
            </span>
          </>
        );
      case 'group':
        return (
          <>
            {t(`explorer.groups.${row.kind ?? 'other'}`)}{' '}
            <span className={styles.count}>({row.count ?? 0})</span>
          </>
        );
      case 'object':
        return <Value value={row.object?.name ?? ''} />;
      case 'extensions':
        return t('explorer.tree.extensions');
      default:
        return t('explorer.tree.file');
    }
  };

  const rowIcon = (row: TreeRow): ReactNode => {
    switch (row.type) {
      case 'schema':
        return <Folder />;
      case 'group':
      case 'object':
        return KIND_ICONS[row.kind ?? 'other'];
      case 'extensions':
        return <Puzzle />;
      default:
        return <FileText />;
    }
  };

  const onSelectRow = (row: TreeRow) => {
    if (row.type === 'object' && row.object !== undefined) {
      select({ type: 'object', object: row.object });
    } else if (row.type === 'extensions') select({ type: 'extensions' });
    else if (row.type === 'file') select({ type: 'file' });
  };

  /** "Show data": the data tab of the selected object, with the focus on it (08 section 4.3 "Show data"). */
  const showData = () => {
    setTab('data');
    window.setTimeout(
      () =>
        document
          .querySelector<HTMLElement>(
            '[data-testid="explorer-detail"] [role="tab"][data-tab="data"]',
          )
          ?.focus(),
      0,
    );
  };

  /** "SELECT … LIMIT 100 in the SQL editor" (08 section 4.3): a new editor tab on this profile; nothing runs. */
  const toEditor = (object: CatalogObject) => {
    if (opened === null || catalog === null) return;
    handToEditor({
      profileId: opened.profileId,
      title: cutText(object.name),
      sql: selectStatement(catalog.engine, object.schema, object.name),
    });
    navigate('/sql');
  };

  /** "Copy name": the qualified name as the database spells it (not escaped: it goes to the clipboard as text). */
  const copyName = async (object: CatalogObject) => {
    const qualified =
      catalog?.engine === 'sqlite' && object.schema === 'main'
        ? object.name
        : `${object.schema}.${object.name}`;
    try {
      await navigator.clipboard.writeText(qualified);
      setMessage(t('explorer.actions.copied'));
    } catch {
      setMessage(t('explorer.actions.copyFailed'));
    }
  };

  const kinds: ObjectKind[] =
    catalog === null ? [] : catalog.engine === 'sqlite' ? ['table', 'view'] : [...OBJECT_KINDS];
  if (catalog?.objects.some((item) => item.kind === 'other')) kinds.push('other');

  let detailTitle: ReactNode = null;
  if (selection?.type === 'object') {
    detailTitle = <Value value={`${selection.object.schema}.${selection.object.name}`} />;
  } else if (selection?.type === 'extensions') detailTitle = t('explorer.extensions.title');
  else if (selection?.type === 'file') detailTitle = t('explorer.file.title');

  let detailBody: ReactNode = null;
  if (detail?.status === 'loading') {
    detailBody = <p className={connectionStyles.hint}>{t('explorer.detail.loading')}</p>;
  } else if (detail?.status === 'problem') {
    detailBody = (
      <p role="alert" className={connectionStyles.problem} data-testid="detail-problem">
        {problemText(t, detail.problem)}
      </p>
    );
  } else if (detail?.status === 'table' && catalog !== null) {
    detailBody = (
      <TableView
        detail={detail.detail}
        engine={catalog.engine}
        sqliteInfo={sqliteInfo}
        tab={tab}
        onTab={setTab}
        find={find}
        onFollow={follow}
        fetcher={fetcher}
        connectionId={opened?.connectionId ?? ''}
        onGone={dataGone}
      />
    );
  } else if (detail?.status === 'extensions') {
    detailBody = <ExtensionsView extensions={detail.extensions} />;
  } else if (detail?.status === 'file') {
    detailBody = <FileView info={detail.info} />;
  }

  const builtAt = catalog?.builtAt ?? null;
  const objectCount = catalog?.objects.length ?? 0;

  return (
    <div className={styles.page}>
      <div className={viewStyles.pageHeader}>
        <h1 className={viewStyles.title}>{t('explorer.title')}</h1>
        <p className={viewStyles.lead}>{t('explorer.lead')}</p>
      </div>
      <section
        className={viewStyles.card}
        aria-labelledby="explorer-connection"
        data-testid="explorer-connection"
      >
        <h2 id="explorer-connection" className={viewStyles.cardTitle}>
          {t('explorer.connection')}
        </h2>
        {profiles !== null && profiles.length === 0 ? (
          <p className={connectionStyles.hint}>{t('explorer.noProfiles')}</p>
        ) : (
          <div className={styles.picker}>
            <label htmlFor={selectId} className={connectionStyles.label}>
              {t('explorer.profile')}
            </label>
            <select
              id={selectId}
              ref={profileSelect}
              data-explorer-profile=""
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
              ref={openButton}
              disabled={profile === null}
              aria-disabled={busy || undefined}
              onClick={() => {
                void start();
              }}
              data-testid="explorer-open"
            >
              {phase === 'opening' ? t('explorer.openingShort') : t('explorer.open')}
            </Button>
          </div>
        )}
        <p
          role="status"
          className={message === '' ? 'tb-sr-only' : connectionStyles.status}
          data-testid="explorer-message"
        >
          {message}
        </p>
        {problem !== null ? (
          <p
            ref={problemRef}
            tabIndex={-1}
            role="alert"
            className={connectionStyles.problem}
            data-testid="explorer-problem"
          >
            {problemText(t, problem)}
          </p>
        ) : null}
      </section>
      {catalog !== null && opened !== null && tree !== null ? (
        <div className={styles.workspace} data-testid="explorer-workspace">
          <section className={styles.treePanel} aria-labelledby="explorer-objects" aria-busy={busy}>
            <div className={styles.treeHead}>
              <h2 id="explorer-objects" className={styles.panelTitle}>
                <Database aria-hidden="true" className={styles.titleIcon} />
                <span className={styles.panelName}>{cutText(opened.name)}</span>
                <EnvBadge environment={opened.environment} size="sm" />
              </h2>
              <Button
                size="sm"
                icon={<RefreshCw />}
                aria-disabled={busy || undefined}
                onClick={() => void refresh()}
                data-testid="explorer-refresh"
              >
                {t('explorer.refresh')}
              </Button>
            </div>
            {builtAt !== null ? (
              <p className={connectionStyles.hint} data-testid="explorer-built">
                {t(catalog.cached ? 'explorer.builtCached' : 'explorer.built', {
                  time: timeText(i18n.language, builtAt),
                })}
              </p>
            ) : null}
            <div className={styles.filter} role="search">
              <label htmlFor={filterId} className="tb-sr-only">
                {t('explorer.filter.label')}
              </label>
              <input
                id={filterId}
                ref={filterInput}
                type="search"
                className={connectionStyles.input}
                placeholder={t('explorer.filter.placeholder')}
                value={filter.query}
                autoComplete="off"
                spellCheck={false}
                data-testid="explorer-filter"
                data-explorer-filter=""
                onChange={(event) => setFilter({ ...filter, query: event.target.value })}
              />
              <label htmlFor={kindId} className="tb-sr-only">
                {t('explorer.filter.kind')}
              </label>
              <select
                id={kindId}
                className={connectionStyles.input}
                value={filter.kind}
                data-testid="explorer-kind"
                onChange={(event) =>
                  setFilter({ ...filter, kind: event.target.value as Filter['kind'] })
                }
              >
                <option value="all">{t('explorer.filter.all')}</option>
                {kinds.map((kind) => (
                  <option key={kind} value={kind}>
                    {t(`explorer.groups.${kind}`)}
                  </option>
                ))}
              </select>
            </div>
            <p
              role="status"
              className={filtering(filter) ? connectionStyles.hint : 'tb-sr-only'}
              data-testid="explorer-matches"
            >
              {filtering(filter)
                ? t('explorer.filter.matches', { count: tree.matched, total: objectCount })
                : ''}
            </p>
            {objectCount === 0 ? (
              <p className={connectionStyles.hint} data-testid="explorer-empty">
                {t('explorer.tree.noObjects')}
              </p>
            ) : null}
            {objectCount >= LARGE_CATALOG ? (
              <p className={connectionStyles.hint} data-testid="explorer-large">
                {t('explorer.tree.large', { count: objectCount })}
              </p>
            ) : null}
            {tree.rows.length > 0 ? (
              <ObjectTree
                rows={tree.rows}
                label={t('explorer.tree.label', { name: cutText(opened.name) })}
                selected={selectionId(selection)}
                focused={focused}
                focusTarget={focusTarget}
                icon={rowIcon}
                content={rowContent}
                text={rowText}
                onFocusRow={setFocused}
                onToggle={(id, isOpen) =>
                  setExpanded((current) => {
                    const next = new Set(current);
                    if (isOpen) next.add(id);
                    else next.delete(id);
                    return next;
                  })
                }
                onSelect={onSelectRow}
              />
            ) : filtering(filter) ? (
              <p className={connectionStyles.hint} data-testid="explorer-no-match">
                {t('explorer.filter.none')}
              </p>
            ) : null}
            {tree.truncated ? (
              <p className={connectionStyles.hint} data-testid="explorer-truncated">
                {t('explorer.tree.truncated')}
              </p>
            ) : null}
          </section>
          <section
            className={styles.detailPanel}
            aria-labelledby="explorer-detail"
            aria-busy={detail?.status === 'loading'}
            data-testid="explorer-detail"
          >
            <div className={styles.detailHead}>
              <h2
                id="explorer-detail"
                ref={detailHeading}
                tabIndex={-1}
                className={styles.panelTitle}
              >
                {detailTitle ?? t('explorer.detail.title')}
              </h2>
              {selection?.type === 'object' ? (
                <span className={styles.kind}>{kindName(t, selection.object.kind)}</span>
              ) : null}
            </div>
            {selection?.type === 'object' && DATA_KINDS.includes(selection.object.kind) ? (
              <div className={styles.objectActions} data-testid="explorer-object-actions">
                <Button
                  size="sm"
                  icon={<Rows3 />}
                  onClick={showData}
                  disabled={detail?.status !== 'table'}
                  data-testid="explorer-show-data"
                >
                  {t('explorer.actions.data')}
                </Button>
                <Button
                  size="sm"
                  icon={<SquareTerminal />}
                  onClick={() => toEditor(selection.object)}
                  data-testid="explorer-to-editor"
                >
                  {t('explorer.actions.select')}
                </Button>
                <Button
                  size="sm"
                  icon={<Copy />}
                  onClick={() => void copyName(selection.object)}
                  data-testid="explorer-copy-name"
                >
                  {t('explorer.actions.copy')}
                </Button>
              </div>
            ) : null}
            {selection === null ? (
              <p className={connectionStyles.hint}>{t('explorer.detail.choose')}</p>
            ) : (
              detailBody
            )}
          </section>
        </div>
      ) : null}
      {dialog !== null ? (
        <ProdConnectDialog
          open
          name={visible(dialog.name)}
          timeoutSeconds={prodTimeoutSeconds(dialog)}
          onCancel={() => setDialog(null)}
          onConnect={() => {
            connections.confirmProd(dialog);
            setDialog(null);
            void open(dialog);
          }}
        />
      ) : null}
    </div>
  );
}
