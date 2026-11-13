/**
 * The SQL editor (STU-012; 03 section 2 C, 08 sections 4.4, 5 and 7). Read-only like every query of Studio 1.0: the
 * page sends statements to the query engine (STU-011, `POST /api/queries`), which runs one reading statement at a
 * time in a read-only transaction. There is no write mode and no write control: the Data Editor and the write mode
 * of the editor (STU-026) stay locked behind G-S2.
 *
 * - **Tabs:** each editor tab has its own connection profile and text; the text is kept as a draft in this browser
 *   (`drafts.ts`). Closing a tab whose text changed since it was last saved asks D-06 (save as a `.sql` file,
 *   discard, cancel).
 * - **Run:** ⌘↵/Ctrl+Enter runs the selection or the statement under the cursor; ⇧⌘↵/Shift+Ctrl+Enter runs every
 *   statement of the tab in turn and stops at the first error or a cancel. The statements are split here
 *   (`sqlScan.ts`); the server checks each one again. A PROD profile asks D-02 before its first connection in this
 *   page.
 * - **Parameters:** the `:name` placeholders of the text get a typed value in the parameter panel; a missing value
 *   stops the run and gets the focus.
 * - **Results:** one result tab per statement that returned columns, and "Messages" with every statement (number,
 *   rows, duration, error; "Go to statement" selects it in the editor). The grid (`ResultGrid.tsx`) shows only the
 *   visible rows and holds at most 10 000 rows and 64 MB (`rowCache.ts`). A result cut at its row limit offers to
 *   load up to the `max_rows` setting. A profile change clears the tab's results (they belong to their profile).
 * - **Cancel:** ⌘./Ctrl+. or Escape in the results, or the button; "Canceled after 4.2 s".
 * - **Export (STU-021, TD-043):** "Export…" on a finished result opens the export dialog (`ExportDialog`) for that
 *   statement with the parameters it ran with, on the connection the result came from (D-02 through the shared guard
 *   first; the dialog never opens a connection). The server runs the statement again, read-only.
 */
import type { TFunction } from 'i18next';
import { Download, FileDown, Play, Plus, Square, SquareTerminal, X } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import { useTranslation } from 'react-i18next';

import { closeConnection, openConnection, PROD_CONFIRMATION_REQUIRED } from '../../api/connections';
import { OFFLINE, type Problem } from '../../api/problem';
import { fetchProfiles, fetchSettings, type ProfileItem } from '../../api/profiles';
import { fetchProject } from '../../api/project';
import {
  cancelQuery,
  followQuery,
  startQuery,
  type ParamValue,
  type QueryStatus,
} from '../../api/queries';
import { UnsavedScriptDialog } from '../../components/dialogs/StudioDialogs';
import { ExportDialog, exportProblemText } from '../../components/export/ExportDialog';
import { AccessModeChip, EnvBadge } from '../../components/status/Status';
import { Button } from '../../components/ui/Button';
import { IconButton } from '../../components/ui/IconButton';
import { currentLocale } from '../../i18n';
import { setEditorActions } from '../../keyboard/editorActions';
import { useKeyboardPlatform, useKeyNames } from '../../keyboard/platform';
import { ariaShortcut, matches, SHORTCUTS, shortcutText } from '../../keyboard/shortcuts';
import clsx from '../../lib/clsx';
import type { SaveFile } from '../../lib/download';
import { visible } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { problemText } from '../connections/messages';
import { useConnect } from '../connections/useConnect';
import { LOST_CODES } from '../../connectionWatch';
import { prodKey, useConnectionSession, useReconnected } from '../../workspace';
import { CellDetail, type OpenCell } from './CellDetail';
import { takeHandoff } from './handoff';
import { draftsKey, readDrafts, writeDrafts, type DraftTab } from './drafts';
import { formatCount, formatMs, formatSeconds } from './format';
import { ParamPanel, paramFieldId } from './ParamPanel';
import { paramValue, type ParamInput, type ParamProblem } from './params';
import { ResultGrid } from './ResultGrid';
import { RowCache } from './rowCache';
import { SqlEditor, type SqlEditorHandle } from './SqlEditor';
import {
  placeholders,
  splitStatements,
  statementAt,
  type Dialect,
  type Statement,
} from './sqlScan';
import styles from './Editor.module.css';

/** The query engine's default row limit (03 section 5.3: "1 000 preview"). */
export const DEFAULT_MAX_ROWS = 1_000;
/** The `max_rows` setting when the settings cannot be read (the server's default). */
const SETTING_MAX_ROWS = 10_000;
const DRAFT_SAVE_MS = 400;

type ResultState = 'pending' | 'running' | 'done' | 'failed' | 'canceled' | 'skipped';

export interface StatementResult {
  key: string;
  /** 1-based number of the statement in this run. */
  number: number;
  from: number;
  to: number;
  sql: string;
  state: ResultState;
  status: QueryStatus | null;
  problem: Problem | null;
  cache: RowCache | null;
  startedAt: number;
  /** The parameter values the statement ran with (the export runs it again with them). */
  params: Record<string, ParamValue>;
}

interface RunState {
  results: StatementResult[];
  running: boolean;
  /** The connection is being opened; no query has started yet. */
  connecting: boolean;
  /** The profile the results came from: a profile change clears them (M-1 of stu-012-review.md). */
  profileId: string;
  /** `messages` or a result's key. */
  view: string;
  /** The connection the results came from. */
  connectionId: string | null;
  /** What to read out after the run (08 section 9: a live region for the query state). */
  announce: string;
}

interface Controller {
  abort: AbortController;
  queryId: string | null;
  stopped: boolean;
}

function newTab(number: number, profileId: string, t: TFunction): DraftTab {
  return {
    id: `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`,
    title: t('editor.tabs.untitled', { number }),
    profileId,
    sql: '',
    savedSql: '',
  };
}

function nextNumber(tabs: DraftTab[], t: TFunction): number {
  let n = tabs.length + 1;
  const titles = new Set(tabs.map((tab) => tab.title));
  while (titles.has(t('editor.tabs.untitled', { number: n }))) n += 1;
  return n;
}

function dialectOf(profile: ProfileItem | null): Dialect {
  return profile?.engine === 'sqlite' ? 'sqlite' : 'postgresql';
}

function dirty(tab: DraftTab): boolean {
  return tab.sql.trim() !== '' && tab.sql !== tab.savedSql;
}

/** A file name for "Save": the tab's title without characters file systems refuse. */
export function scriptFileName(title: string): string {
  // eslint-disable-next-line no-control-regex
  const name = title.replace(/[\u0000-\u001f\u007f<>:"/\\|?*]+/g, '_').trim() || 'query';
  return `${name.slice(0, 100)}.sql`;
}

function download(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/sql;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

/** The text of an export's problem: the export's own codes, else a statement's (the export checks it again). */
function exportOrQueryProblemText(t: TFunction, problem: Problem): string {
  return problem.code.startsWith('studio.export_') ||
    problem.code === 'studio.connection_not_found' ||
    problem.code === 'studio.connection_stale'
    ? exportProblemText(t, problem)
    : queryProblemText(t, problem);
}

/** The text of a statement's problem: the read-only refusals get the wording of 08 section 4.4. */
export function queryProblemText(t: TFunction, problem: Problem): string {
  switch (problem.code) {
    case 'db.read_only':
      return t('editor.problems.readOnly');
    case 'studio.statement_refused':
      return t('editor.problems.refused');
    case 'studio.statement_multiple':
      return t('editor.problems.multiple');
    case 'studio.statement_transaction':
      return t('editor.problems.transaction');
    case 'studio.statement_empty':
      return t('editor.problems.empty');
    case 'studio.query_not_found':
      return t('editor.problems.gone');
    case 'studio.query_limit':
      return t('editor.problems.limit');
    case 'studio.query_param_missing':
      return t('editor.problems.paramMissing');
    case 'studio.query_params_invalid':
      return t('editor.problems.paramsInvalid');
    case 'studio.query_too_large':
      return t('editor.problems.tooLarge');
    case 'studio.query_canceled':
    case 'db.canceled':
      return t('editor.problems.canceled');
    case 'db.timeout':
      return t('editor.problems.timeout');
    case 'db.syntax':
      return t('editor.problems.syntax');
    case 'db.undefined_object':
      return t('editor.problems.undefinedObject');
    case 'db.permission':
      return t('editor.problems.permission');
    case 'db.data':
      return t('editor.problems.data');
    default:
      return problem.code.startsWith('db.')
        ? t('editor.problems.database', {
            code: problem.code,
            interpolation: { escapeValue: false },
          })
        : problemText(t, problem);
  }
}

function summary(t: TFunction, result: StatementResult, locale: string): string {
  const status = result.status;
  switch (result.state) {
    case 'pending':
      return t('editor.result.pending');
    case 'skipped':
      return t('editor.result.skipped');
    case 'running':
      return t('editor.result.running');
    case 'canceled':
      return t('editor.result.canceled', {
        seconds: formatSeconds(status?.elapsedMs ?? Date.now() - result.startedAt, locale),
      });
    case 'failed':
      return result.problem === null
        ? t('editor.result.failed')
        : queryProblemText(t, result.problem);
    default: {
      if (status === null) return '';
      const rows = formatCount(status.rowCount, locale);
      const ms = formatMs(status.elapsedMs, locale);
      if (status.columns.length === 0) return t('editor.result.noRows', { ms });
      if (status.truncatedBy === 'rows') {
        return t('editor.result.cappedRows', { count: status.rowCount, rows, ms });
      }
      if (status.truncatedBy === 'bytes') {
        return t('editor.result.cappedBytes', { count: status.rowCount, rows, ms });
      }
      return t('editor.result.rows', { count: status.rowCount, rows, ms });
    }
  }
}

export function EditorView({
  fetcher = fetch,
  saveExport,
}: {
  fetcher?: typeof fetch;
  /** Where an exported file goes (the browser's download; tests pass their own). */
  saveExport?: SaveFile;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language || currentLocale();
  const platform = useKeyboardPlatform();
  const keyNames = useKeyNames();
  const prefix = useId().replace(/:/g, '');
  const editor = useRef<SqlEditorHandle>(null);
  const grid = useRef<HTMLDivElement>(null);

  const [profiles, setProfiles] = useState<ProfileItem[] | null>(null);
  const [maxRowsSetting, setMaxRowsSetting] = useState(SETTING_MAX_ROWS);
  const [tabs, setTabs] = useState<DraftTab[]>([]);
  const [activeId, setActiveId] = useState<string>('');
  /** The drafts' storage key (per project), known once the project's folder was read. */
  const [draftKey, setDraftKey] = useState<string | null>(null);
  /** Every tab id this page has had: a draft write keeps only the other pages' tabs (`drafts.ts`). */
  const known = useRef(new Set<string>());
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const [params, setParams] = useState<Record<string, Record<string, ParamInput>>>({});
  const [paramProblem, setParamProblem] = useState<{ name: string; problem: ParamProblem } | null>(
    null,
  );
  const [connectProblem, setConnectProblem] = useState<Problem | null>(null);
  const [notice, setNotice] = useState('');
  const [closing, setClosing] = useState<DraftTab | null>(null);
  const [detail, setDetail] = useState<OpenCell | null>(null);
  /** The result whose export dialog is open (TD-043). */
  const [exporting, setExporting] = useState<StatementResult | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [focusTab, setFocusTab] = useState<string | null>(null);
  const controllers = useRef(new Map<string, Controller>());
  // D-02 through the page's shared confirmations (STU-008 `useConnect`): a PROD database confirmed in the connection
  // manager is not asked again here, and a profile whose target changed asks again.
  const { guard: prodGuard, dialog: prodDialog } = useConnect(fetcher);
  /** The open connection of each profile used in this page (profile id to connection id). */
  const connections = useRef(new Map<string, string>());
  const { markLost } = useConnectionSession();
  // TD-034: "Reconnect" in the banner replaced a connection: the next run, "Load more" and the export use the new one.
  useReconnected(({ from, to }) => {
    for (const [profileId, id] of connections.current) {
      if (id === from) connections.current.set(profileId, to);
    }
    setRuns((all) => {
      if (!Object.values(all).some((run) => run.connectionId === from)) return all;
      return Object.fromEntries(
        Object.entries(all).map(([id, run]) => [
          id,
          run.connectionId === from ? { ...run, connectionId: to } : run,
        ]),
      );
    });
  });

  // Profiles, the drafts of this project and the row limit setting.
  useEffect(() => {
    let active = true;
    void Promise.all([fetchProfiles(fetcher), fetchProject(fetcher)]).then(([list, project]) => {
      if (!active) return;
      const items = list?.profiles ?? [];
      const key = draftsKey(project?.path ?? '');
      const drafts = readDrafts(key);
      // The drafts, or a first tab (on the first profile, or none).
      let first = drafts?.tabs ?? [newTab(1, items[0]?.id ?? '', i18n.t.bind(i18n))];
      let activeTab = drafts?.activeTab ?? first[0]?.id ?? '';
      // A statement from another page (the explorer's "SELECT … LIMIT 100"): a new tab, never run by itself.
      const handed = takeHandoff();
      if (handed !== null) {
        const created = {
          ...newTab(first.length + 1, handed.profileId, i18n.t.bind(i18n)),
          title: handed.title,
          sql: handed.sql,
        };
        first = drafts === null ? [created] : [...first, created];
        activeTab = created.id;
      }
      for (const item of first) known.current.add(item.id);
      setProfiles(items);
      setTabs(first);
      setActiveId(activeTab);
      setDraftKey(key);
    });
    void fetchSettings(fetcher).then((state) => {
      if (active && state !== null) setMaxRowsSetting(state.settings.maxRows);
    });
    return () => {
      active = false;
    };
  }, [fetcher, i18n]);

  // Drafts, a moment after the last change.
  useEffect(() => {
    if (tabs.length === 0 || draftKey === null) return undefined;
    const timer = setTimeout(
      () => writeDrafts({ tabs, activeTab: activeId }, draftKey, known.current),
      DRAFT_SAVE_MS,
    );
    return () => clearTimeout(timer);
  }, [tabs, activeId, draftKey]);

  // Cancel what still runs when the page goes (the results are not kept).
  useEffect(
    () => () => {
      for (const controller of controllers.current.values()) {
        controller.stopped = true;
        controller.abort.abort();
        if (controller.queryId !== null) void cancelQuery(fetcher, controller.queryId);
      }
    },
    [fetcher],
  );

  const tab = tabs.find((item) => item.id === activeId) ?? tabs[0] ?? null;
  const profile = profiles?.find((item) => item.id === tab?.profileId) ?? null;
  const dialect = dialectOf(profile);
  const run: RunState | undefined = tab ? runs[tab.id] : undefined;
  const running = run?.running ?? false;
  const tabParams = useMemo(() => (tab ? (params[tab.id] ?? {}) : {}), [params, tab]);
  const names = useMemo(() => (tab ? placeholders(tab.sql, dialect) : []), [tab, dialect]);

  // The running clock (08 section 4.4: "running time counter").
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, [running]);

  const updateTab = useCallback((id: string, patch: Partial<DraftTab>) => {
    setTabs((list) => list.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }, []);

  const patchRun = useCallback((tabId: string, patch: (run: RunState) => RunState) => {
    setRuns((all) => {
      const current = all[tabId];
      return current === undefined ? all : { ...all, [tabId]: patch(current) };
    });
  }, []);

  const patchResult = useCallback(
    (tabId: string, key: string, patch: Partial<StatementResult>) => {
      patchRun(tabId, (current) => ({
        ...current,
        results: current.results.map((result) =>
          result.key === key ? { ...result, ...patch } : result,
        ),
      }));
    },
    [patchRun],
  );

  /** A profile changed on the server: its results (rows of the old target or environment) and connection go. */
  const forgetProfile = useCallback((profileId: string) => {
    connections.current.delete(profileId);
    setRuns((all) => {
      const kept: Record<string, RunState> = {};
      for (const [tabId, state] of Object.entries(all)) {
        if (state.profileId === profileId) {
          for (const result of state.results) result.cache?.dispose();
        } else kept[tabId] = state;
      }
      return kept;
    });
    setDetail(null);
  }, []);

  /**
   * The connection of `item`, the profile as it was when the run passed D-02 (`start`). A known connection is used as
   * it is. Before a new one (the first, or again after `studio.connection_stale`), the profile is read again from the
   * server, because another page or an edit of `profiles.toml` may have changed it since this page read it (C-5 of
   * `stu-012-review.md`): when its environment or target changed, nothing is opened, the page takes the new profile
   * and the user runs again, which asks D-02 for a PROD profile. As a backstop the server's answer must name the
   * environment of `item`, the profile that passed D-02 (an answer without an environment counts as a mismatch, L-9);
   * else that connection is closed again and the run stops.
   */
  const connect = useCallback(
    async (item: ProfileItem): Promise<string | null> => {
      const known = connections.current.get(item.id);
      if (known !== undefined) return known;
      setConnectProblem(null);
      const list = await fetchProfiles(fetcher);
      if (list === null) {
        setConnectProblem(OFFLINE);
        return null;
      }
      setProfiles(list.profiles);
      const fresh = list.profiles.find((entry) => entry.id === item.id);
      if (fresh === undefined) {
        setNotice(t('editor.profileGone', { name: visible(item.name) }));
        return null;
      }
      if (prodKey(fresh) !== prodKey(item)) {
        forgetProfile(item.id);
        setNotice(
          t('editor.profileChanged', {
            name: visible(fresh.name),
            environment: fresh.environment.toUpperCase(),
          }),
        );
        return null;
      }
      // The environment that passed D-02 (D-02 on the server, TD-038).
      const opened = await openConnection(fetcher, fresh.id, item.environment);
      if (!opened.ok) {
        setConnectProblem(opened.problem);
        if (opened.problem.code === PROD_CONFIRMATION_REQUIRED) {
          // The profile became PROD between the page's read and the open: take it as it is now, so the next run asks
          // D-02.
          forgetProfile(item.id);
          const again = await fetchProfiles(fetcher);
          if (again !== null) setProfiles(again.profiles);
        }
        return null;
      }
      const environment = opened.value.environment;
      if (environment !== item.environment) {
        void closeConnection(fetcher, opened.value.connectionId);
        forgetProfile(item.id);
        const again = await fetchProfiles(fetcher);
        if (again !== null) setProfiles(again.profiles);
        setNotice(
          environment === null
            ? t('editor.environmentUnknown', { name: visible(item.name) })
            : t('editor.profileChanged', {
                name: visible(fresh.name),
                environment: environment.toUpperCase(),
              }),
        );
        return null;
      }
      connections.current.set(item.id, opened.value.connectionId);
      return opened.value.connectionId;
    },
    [fetcher, forgetProfile, t],
  );

  /** Run `statements` of `tabId` in turn on `connectionId`; stops at the first error or a cancel. */
  const execute = useCallback(
    async (
      tabId: string,
      item: ProfileItem,
      statements: Statement[],
      values: Record<string, ParamValue>,
      maxRows?: number,
      replaceKey?: string,
    ) => {
      // The run is registered before the first `await`, so a second run, a cancel and closing the tab all see it
      // while the connection opens (M-2 of stu-012-review.md).
      const controller: Controller = {
        abort: new AbortController(),
        queryId: null,
        stopped: false,
      };
      controllers.current.set(tabId, controller);
      setRuns((all) => ({
        ...all,
        [tabId]: {
          ...(all[tabId] ?? {
            results: [],
            view: 'messages',
            connectionId: null,
            announce: '',
            profileId: item.id,
          }),
          running: true,
          connecting: true,
        },
      }));
      const release = () => {
        if (controllers.current.get(tabId) === controller) controllers.current.delete(tabId);
      };
      let connectionId = await connect(item);
      if (connectionId === null || controller.stopped) {
        release();
        patchRun(tabId, (current) => ({ ...current, running: false, connecting: false }));
        // Canceled while connecting (a closed tab says nothing).
        if (connectionId !== null && !controller.abort.signal.aborted) {
          setNotice(t('editor.canceledBeforeStart'));
        }
        return;
      }
      const stamp = Date.now();
      const fresh: StatementResult[] = statements.map((statement, index) => ({
        key: replaceKey ?? `${stamp}-${index}`,
        number: index + 1,
        from: statement.from,
        to: statement.to,
        sql: statement.sql,
        state: 'pending',
        status: null,
        problem: null,
        cache: null,
        startedAt: stamp,
        params: Object.fromEntries(
          statement.params.filter((name) => name in values).map((name) => [name, values[name]]),
        ) as Record<string, ParamValue>,
      }));
      setDetail(null);
      setRuns((all) => {
        const previous = all[tabId];
        if (replaceKey !== undefined && previous !== undefined) {
          const old = previous.results.find((result) => result.key === replaceKey);
          old?.cache?.dispose();
          return {
            ...all,
            [tabId]: {
              ...previous,
              running: true,
              connecting: false,
              announce: '',
              results: previous.results.map((result) =>
                result.key === replaceKey
                  ? { ...(fresh[0] as StatementResult), number: result.number }
                  : result,
              ),
            },
          };
        }
        for (const result of previous?.results ?? []) result.cache?.dispose();
        return {
          ...all,
          [tabId]: {
            results: fresh,
            running: true,
            connecting: false,
            profileId: item.id,
            view: fresh[0]?.key ?? 'messages',
            connectionId,
            announce: '',
          },
        };
      });
      let last: StatementResult | null = null;
      for (const [index, statement] of statements.entries()) {
        const key = fresh[index]?.key as string;
        if (controller.stopped) {
          patchResult(tabId, key, { state: 'skipped' });
          continue;
        }
        const own: Record<string, ParamValue> = {};
        for (const name of statement.params) {
          if (name in values) own[name] = values[name] as ParamValue;
        }
        const startedAt = Date.now();
        patchResult(tabId, key, { state: 'running', startedAt });
        let started = await startQuery(fetcher, {
          connectionId,
          sql: statement.sql,
          params: own,
          maxRows,
        });
        // The server closed the connection (its profile changed, Studio restarted it): open it once more, through the
        // same checks as a first connection (`connect` reads the profile again; a changed one stops the run). Not for
        // PROD: a new PROD connection needs D-02 again (TD-034), so the run stops and the banner offers "Reconnect".
        if (
          !started.ok &&
          item.environment !== 'prod' &&
          (started.problem.code === 'studio.connection_not_found' ||
            started.problem.code === 'studio.connection_stale')
        ) {
          connections.current.delete(item.id);
          const again = await connect(item);
          if (again === null) {
            patchResult(tabId, key, { state: 'failed', problem: started.problem });
            controller.stopped = true;
            continue;
          }
          connectionId = again;
          patchRun(tabId, (current) => ({ ...current, connectionId }));
          started = await startQuery(fetcher, {
            connectionId,
            sql: statement.sql,
            params: own,
            maxRows,
          });
        }
        if (!started.ok) {
          patchResult(tabId, key, { state: 'failed', problem: started.problem });
          last = {
            ...(fresh[index] as StatementResult),
            state: 'failed',
            problem: started.problem,
          };
          controller.stopped = true;
          continue;
        }
        controller.queryId = started.value.queryId;
        // A cancel (or a closed tab) while the start request was on its way found no query id to cancel: the server
        // has the query now, so cancel it here, or it runs on until its timeout and the page shows it running.
        if (controller.stopped) void cancelQuery(fetcher, started.value.queryId);
        patchResult(tabId, key, { status: started.value });
        const final = await followQuery(
          fetcher,
          started.value.queryId,
          (status) => patchResult(tabId, key, { status }),
          controller.abort.signal,
        );
        controller.queryId = null;
        // TD-034: a query that failed because the database stopped answering ends in its stream, which the page's
        // fetcher does not see: report it here, so the banner offers "Reconnect".
        const failedCode = final.ok ? final.value.problem?.code : final.problem.code;
        if (failedCode !== undefined && LOST_CODES.has(failedCode))
          markLost(connectionId, failedCode);
        if (!final.ok) {
          patchResult(tabId, key, { state: 'failed', problem: final.problem });
          last = { ...(fresh[index] as StatementResult), state: 'failed', problem: final.problem };
          controller.stopped = true;
          continue;
        }
        const status = final.value;
        const state: ResultState = status.state === 'running' ? 'failed' : status.state;
        const cache =
          state === 'done' && status.columns.length > 0
            ? new RowCache(fetcher, status.queryId, status.rowCount)
            : null;
        const result: StatementResult = {
          ...(fresh[index] as StatementResult),
          state,
          status,
          problem: status.problem,
          cache,
          startedAt,
        };
        patchResult(tabId, key, { state, status, problem: status.problem, cache });
        last = result;
        if (state !== 'done') controller.stopped = true;
      }
      release();
      const announce = last === null ? '' : summary(t, last, locale);
      patchRun(tabId, (current) => {
        // Show the first result with rows, else the messages (also after an error in a script).
        const failed = current.results.some((result) => result.state === 'failed');
        const withRows = current.results.find((result) => result.cache !== null);
        const view =
          replaceKey !== undefined
            ? current.view
            : failed && current.results.length > 1
              ? 'messages'
              : (withRows?.key ?? (current.results.length > 1 ? 'messages' : current.view));
        return { ...current, running: false, view, announce };
      });
    },
    [connect, fetcher, locale, markLost, patchResult, patchRun, t],
  );

  /** Drop a tab's results (their row caches too). */
  const clearRun = useCallback((tabId: string) => {
    setRuns(({ [tabId]: gone, ...rest }) => {
      for (const result of gone?.results ?? []) result.cache?.dispose();
      return rest;
    });
    setDetail(null);
  }, []);

  /** Check the placeholders, connect (D-02 for PROD) and run. */
  const start = useCallback(
    (statements: Statement[], options: { maxRows?: number; replaceKey?: string } = {}) => {
      if (tab === null || running || controllers.current.has(tab.id)) return;
      if (profile === null) {
        setNotice(t('editor.noProfile'));
        return;
      }
      if (statements.length === 0) {
        setNotice(t('editor.noStatement'));
        return;
      }
      const values: Record<string, ParamValue> = {};
      for (const statement of statements) {
        for (const name of statement.params) {
          const checked = paramValue(tabParams[name]);
          if (!checked.ok) {
            setParamProblem({ name, problem: checked.problem });
            setNotice(t(`editor.params.problem.${checked.problem}`, { name }));
            // 08 section 5: the panel's field of the missing value gets the focus.
            window.setTimeout(
              () => document.getElementById(paramFieldId(prefix, name))?.focus(),
              0,
            );
            return;
          }
          values[name] = checked.value;
        }
      }
      setParamProblem(null);
      setNotice('');
      const go = () =>
        void execute(tab.id, profile, statements, values, options.maxRows, options.replaceKey);
      prodGuard(profile, go);
    },
    [execute, prefix, prodGuard, profile, running, t, tab, tabParams],
  );

  const runCurrent = useCallback(() => {
    if (tab === null) return;
    const selection = editor.current?.selection() ?? { from: 0, to: 0, head: 0 };
    if (selection.from !== selection.to) {
      const part = tab.sql.slice(selection.from, selection.to);
      start(
        splitStatements(part, dialect).map((statement) => ({
          ...statement,
          from: statement.from + selection.from,
          to: statement.to + selection.from,
        })),
      );
      return;
    }
    const statement = statementAt(splitStatements(tab.sql, dialect), selection.head);
    start(statement === null ? [] : [statement]);
  }, [dialect, start, tab]);

  const runScript = useCallback(() => {
    if (tab !== null) start(splitStatements(tab.sql, dialect));
  }, [dialect, start, tab]);

  const cancel = useCallback(() => {
    if (tab === null) return;
    const controller = controllers.current.get(tab.id);
    if (controller === undefined) return;
    // While the connection opens there is no query yet: `execute` sees `stopped` and starts none.
    controller.stopped = true;
    if (controller.queryId !== null) void cancelQuery(fetcher, controller.queryId);
  }, [fetcher, tab]);

  const addTab = useCallback(() => {
    const created = newTab(nextNumber(tabs, t), tab?.profileId ?? profiles?.[0]?.id ?? '', t);
    known.current.add(created.id);
    setTabs((list) => [...list, created]);
    setActiveId(created.id);
    // The new tab's editor takes the focus as soon as it exists.
    setFocusTab(created.id);
  }, [profiles, t, tab, tabs]);

  const removeTab = useCallback(
    (id: string) => {
      const controller = controllers.current.get(id);
      if (controller !== undefined) {
        controller.stopped = true;
        controller.abort.abort();
        if (controller.queryId !== null) void cancelQuery(fetcher, controller.queryId);
        controllers.current.delete(id);
      }
      for (const result of runs[id]?.results ?? []) result.cache?.dispose();
      setRuns(({ [id]: _gone, ...rest }) => rest);
      setParams(({ [id]: _gone, ...rest }) => rest);
      setTabs((list) => {
        const index = list.findIndex((item) => item.id === id);
        const rest = list.filter((item) => item.id !== id);
        // The last tab closed: a new empty one takes its place.
        if (rest.length === 0) {
          const fresh = newTab(1, list[index]?.profileId ?? '', t);
          known.current.add(fresh.id);
          rest.push(fresh);
        }
        if (id === activeId) {
          const next = rest[Math.min(index, rest.length - 1)];
          setActiveId(next?.id ?? '');
        }
        return rest;
      });
    },
    [activeId, fetcher, runs, t],
  );

  const closeTab = useCallback(
    (id: string) => {
      const item = tabs.find((entry) => entry.id === id);
      if (item === undefined) return;
      if (dirty(item)) setClosing(item);
      else removeTab(id);
    },
    [removeTab, tabs],
  );

  const save = useCallback(
    (item: DraftTab | null = tab) => {
      if (item === null) return;
      download(scriptFileName(item.title), item.sql);
      updateTab(item.id, { savedSql: item.sql });
      setNotice(t('editor.saved', { name: scriptFileName(item.title) }));
    },
    [t, tab, updateTab],
  );

  // The palette's editor commands (08 section 1) and the page's own shortcuts (08 section 7).
  const latest = useRef({ runCurrent, runScript, cancel, addTab, closeTab, save, running, tab });
  useLayoutEffect(() => {
    latest.current = { runCurrent, runScript, cancel, addTab, closeTab, save, running, tab };
  });
  useEffect(() => {
    setEditorActions({
      run: () => latest.current.runCurrent(),
      runScript: () => latest.current.runScript(),
      cancel: () => latest.current.cancel(),
      newTab: () => latest.current.addTab(),
      closeTab: () => {
        const current = latest.current.tab;
        if (current !== null) latest.current.closeTab(current.id);
      },
      save: () => latest.current.save(latest.current.tab),
      running: () => latest.current.running,
    });
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return;
      if (document.querySelector('dialog[open]') !== null) return;
      const actions: [keyof typeof SHORTCUTS, () => void][] = [
        ['runScript', () => latest.current.runScript()],
        ['run', () => latest.current.runCurrent()],
        ['cancel', () => latest.current.cancel()],
        ['newTab', () => latest.current.addTab()],
        [
          'closeTab',
          () => {
            const current = latest.current.tab;
            if (current !== null) latest.current.closeTab(current.id);
          },
        ],
        ['save', () => latest.current.save(latest.current.tab)],
      ];
      for (const [id, action] of actions) {
        if (matches(event, SHORTCUTS[id], platform)) {
          event.preventDefault();
          action();
          return;
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      setEditorActions(null);
    };
  }, [platform]);

  const keys = (id: keyof typeof SHORTCUTS) => shortcutText(SHORTCUTS[id], platform, keyNames);
  const results = run?.results ?? [];
  const withColumns = results.filter((result) => result.cache !== null);
  const runningResult = results.find((result) => result.state === 'running') ?? null;
  // A result tab exists only for a statement that returned rows; everything else is in the messages.
  const shown = withColumns.find((result) => result.key === run?.view) ?? null;
  const view = shown === null ? 'messages' : shown.key;
  const detailColumn =
    detail !== null && shown?.status ? (shown.status.columns[detail.column] ?? null) : null;

  const statusLine = (() => {
    if (run?.connecting) return t('editor.result.connecting');
    if (runningResult !== null) {
      return t('editor.result.runningFor', {
        number: runningResult.number,
        count: results.length,
        seconds: formatSeconds(now - runningResult.startedAt, locale),
      });
    }
    if (results.length === 0) return '';
    if (results.length === 1) return summary(t, results[0] as StatementResult, locale);
    if (view === 'messages') {
      const failed = results.filter((result) => result.state === 'failed').length;
      return t('editor.result.statements', { count: results.length, failed });
    }
    return shown === null ? '' : summary(t, shown, locale);
  })();

  const onResultsKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    // 08 section 7: Escape in the results cancels the running query.
    if (event.key === 'Escape' && running && !event.defaultPrevented) {
      event.preventDefault();
      cancel();
    }
  };

  const loadMore = (result: StatementResult) => {
    // Only on the profile the result came from (L-1 of stu-012-review.md; a profile change clears the results).
    if (run?.profileId !== tab?.profileId) return;
    start(
      [
        {
          from: result.from,
          to: result.to,
          sql: result.sql,
          params: placeholders(result.sql, dialect),
        },
      ],
      { maxRows: maxRowsSetting, replaceKey: result.key },
    );
  };

  const resultTabs = [
    ...withColumns.map((result) => ({
      key: result.key,
      label: t('editor.result.tab', { number: result.number }),
    })),
    ...(results.length > 0 ? [{ key: 'messages', label: t('editor.result.messages') }] : []),
  ];

  const onResultTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = -1;
    if (event.key === 'ArrowRight') next = (index + 1) % resultTabs.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + resultTabs.length) % resultTabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = resultTabs.length - 1;
    if (next === -1 || tab === null) return;
    event.preventDefault();
    const target = resultTabs[next];
    if (target === undefined) return;
    patchRun(tab.id, (state) => ({ ...state, view: target.key }));
    setDetail(null);
    document.getElementById(`${prefix}-rtab-${target.key}`)?.focus();
  };

  if (profiles === null || tab === null) {
    return (
      <div className={styles.page}>
        <h1 className={styles.title}>{t('editor.title')}</h1>
        <p className={connectionStyles.hint}>{t('editor.loading')}</p>
      </div>
    );
  }

  return (
    <div className={clsx(styles.page, profile?.environment === 'prod' && styles.prodPage)}>
      <div className={styles.header}>
        <h1 className={styles.title}>{t('editor.title')}</h1>
        <ul className={styles.tabs} aria-label={t('editor.tabs.label')} data-testid="editor-tabs">
          {tabs.map((item) => {
            const itemProfile = profiles.find((entry) => entry.id === item.profileId);
            const selected = item.id === tab.id;
            return (
              <li key={item.id} className={clsx(styles.tab, selected && styles.tabActive)}>
                <button
                  type="button"
                  className={styles.tabButton}
                  aria-current={selected ? 'true' : undefined}
                  onClick={() => {
                    setActiveId(item.id);
                    setDetail(null);
                  }}
                  data-testid="editor-tab"
                >
                  <span className={styles.tabTitle}>
                    {item.title}
                    {dirty(item) ? (
                      <span className={styles.dirtyMark}>
                        <span aria-hidden="true"> •</span>
                        <span className="tb-sr-only">, {t('editor.tabs.unsaved')}</span>
                      </span>
                    ) : null}
                  </span>
                  {itemProfile ? (
                    <>
                      <span className="tb-sr-only">, </span>
                      <EnvBadge environment={itemProfile.environment} size="sm" />
                    </>
                  ) : null}
                  {runs[item.id]?.running ? (
                    <span className="tb-sr-only">, {t('editor.tabs.running')}</span>
                  ) : null}
                </button>
                <IconButton
                  label={t('editor.tabs.close', { name: item.title })}
                  icon={<X />}
                  className={styles.tabClose}
                  onClick={() => closeTab(item.id)}
                />
              </li>
            );
          })}
          <li>
            <IconButton
              label={t('editor.tabs.new', { keys: keys('newTab') })}
              icon={<Plus />}
              aria-keyshortcuts={ariaShortcut(SHORTCUTS.newTab, platform)}
              onClick={addTab}
              data-testid="editor-new-tab"
            />
          </li>
        </ul>
      </div>

      <div className={styles.toolbar} role="group" aria-label={t('editor.toolbar')}>
        <label htmlFor={`${prefix}-profile`} className={styles.toolbarLabel}>
          {t('editor.connection')}
        </label>
        <select
          id={`${prefix}-profile`}
          className={connectionStyles.input}
          value={tab.profileId}
          disabled={running}
          onChange={(event) => {
            updateTab(tab.id, { profileId: event.target.value });
            setConnectProblem(null);
            // Results stay with the profile they came from: PROD rows never show under another badge (M-1).
            clearRun(tab.id);
          }}
          data-testid="editor-profile"
        >
          {profiles.length === 0 ? <option value="">{t('editor.noProfiles')}</option> : null}
          {tab.profileId === '' && profiles.length > 0 ? (
            <option value="">{t('editor.chooseProfile')}</option>
          ) : null}
          {profiles.map((item) => (
            <option key={item.id} value={item.id}>
              {`${visible(item.name)} (${item.environment.toUpperCase()})`}
            </option>
          ))}
        </select>
        {profile !== null ? (
          <>
            <EnvBadge environment={profile.environment} size="sm" />
            <AccessModeChip environment={profile.environment} />
          </>
        ) : null}
        <span className={styles.toolbarSpacer} />
        <Button
          variant="primary"
          size="sm"
          icon={<Play />}
          disabled={running}
          onClick={runCurrent}
          aria-keyshortcuts={ariaShortcut(SHORTCUTS.run, platform)}
          title={keys('run')}
          data-testid="editor-run"
        >
          {t('editor.actions.run')}
        </Button>
        <Button
          size="sm"
          icon={<SquareTerminal />}
          disabled={running}
          onClick={runScript}
          aria-keyshortcuts={ariaShortcut(SHORTCUTS.runScript, platform)}
          title={keys('runScript')}
          data-testid="editor-run-script"
        >
          {t('editor.actions.runScript')}
        </Button>
        <Button
          size="sm"
          variant={running ? 'danger' : 'secondary'}
          icon={<Square />}
          disabled={!running}
          onClick={cancel}
          aria-keyshortcuts={ariaShortcut(SHORTCUTS.cancel, platform)}
          title={keys('cancel')}
          data-testid="editor-cancel"
        >
          {t('editor.actions.cancel')}
        </Button>
        <Button
          size="sm"
          icon={<Download />}
          onClick={() => save(tab)}
          aria-keyshortcuts={ariaShortcut(SHORTCUTS.save, platform)}
          title={keys('save')}
          data-testid="editor-save"
        >
          {t('editor.actions.save')}
        </Button>
      </div>

      <div className={styles.editorArea} data-region="editor">
        <SqlEditor
          key={tab.id}
          ref={editor}
          value={tab.sql}
          onChange={(value) => updateTab(tab.id, { sql: value })}
          dialect={dialect}
          label={t('editor.textLabel', { name: tab.title })}
          description={t('editor.shortcuts', {
            run: keys('run'),
            script: keys('runScript'),
            cancel: keys('cancel'),
          })}
          onRun={runCurrent}
          onRunScript={runScript}
          onCancel={cancel}
          focusOnCreate={focusTab === tab.id}
        />
      </div>

      {connectProblem !== null ? (
        <p role="alert" className={connectionStyles.problem} data-testid="editor-connect-problem">
          {problemText(t, connectProblem)}
        </p>
      ) : null}
      <p role="status" className={styles.notice} data-testid="editor-notice">
        {notice}
      </p>

      <ParamPanel
        prefix={prefix}
        names={names}
        values={tabParams}
        problem={paramProblem}
        onChange={(name, input) => {
          setParams((all) => ({ ...all, [tab.id]: { ...(all[tab.id] ?? {}), [name]: input } }));
          if (paramProblem?.name === name) setParamProblem(null);
        }}
      />

      {/* Escape anywhere in the results cancels a running query (08 section 7). */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <section
        className={styles.results}
        aria-labelledby={`${prefix}-results-title`}
        onKeyDown={onResultsKeyDown}
        data-testid="results"
        data-region="result"
      >
        <h2 id={`${prefix}-results-title`} className="tb-sr-only">
          {t('editor.result.title')}
        </h2>
        <div className={styles.statusRow}>
          {running ? (
            <progress className={styles.progress} aria-label={t('editor.result.progress')} />
          ) : null}
          <p className={styles.statusLine} data-testid="result-status">
            {statusLine}
          </p>
          {shown !== null &&
          view !== 'messages' &&
          shown.state === 'done' &&
          shown.status?.truncatedBy === 'rows' &&
          shown.status.maxRows < maxRowsSetting &&
          run?.profileId === tab.profileId &&
          !running ? (
            <Button size="sm" onClick={() => loadMore(shown)} data-testid="load-more">
              {t('editor.result.loadMore', { rows: formatCount(maxRowsSetting, locale) })}
            </Button>
          ) : null}
          {shown !== null &&
          view !== 'messages' &&
          shown.state === 'done' &&
          run?.connectionId != null &&
          run.profileId === tab.profileId &&
          profile !== null &&
          !running ? (
            <Button
              size="sm"
              icon={<FileDown />}
              // D-02 through the shared guard: confirmed when the result's connection was opened, so it passes at
              // once; the seam for a source whose connection was not opened in this page (the data viewer).
              onClick={() => prodGuard(profile, () => setExporting(shown))}
              data-testid="export-open"
            >
              {t('editor.actions.export')}
            </Button>
          ) : null}
        </div>
        {/* The final state of a run, read out once (08 section 9). */}
        <p className="tb-sr-only" role="status" aria-live="polite" data-testid="result-announce">
          {run?.announce ?? ''}
        </p>
        {resultTabs.length > 0 ? (
          <div role="tablist" aria-label={t('editor.result.tabs')} className={styles.resultTabs}>
            {resultTabs.map((item, index) => (
              <button
                key={item.key}
                id={`${prefix}-rtab-${item.key}`}
                type="button"
                role="tab"
                aria-selected={view === item.key}
                aria-controls={`${prefix}-rpanel`}
                tabIndex={view === item.key ? 0 : -1}
                className={styles.resultTab}
                onClick={() => {
                  patchRun(tab.id, (state) => ({ ...state, view: item.key }));
                  setDetail(null);
                }}
                onKeyDown={(event) => onResultTabKeyDown(event, index)}
                data-testid="result-tab"
              >
                {item.label}
              </button>
            ))}
          </div>
        ) : null}
        {resultTabs.length > 0 ? (
          <div
            id={`${prefix}-rpanel`}
            role="tabpanel"
            aria-labelledby={`${prefix}-rtab-${view}`}
            className={styles.resultPanel}
          >
            {view === 'messages' ? (
              <ol className={styles.messages} data-testid="messages">
                {results.map((result) => (
                  <li
                    key={result.key}
                    className={clsx(
                      styles.message,
                      result.state === 'failed' && styles.messageFailed,
                    )}
                    data-testid="message"
                  >
                    <span className={styles.messageNumber}>
                      {t('editor.result.statement', { number: result.number })}
                    </span>
                    <span className={styles.messageText}>{summary(t, result, locale)}</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => editor.current?.select(result.from, result.to)}
                    >
                      {t('editor.result.goTo')}
                      <span className="tb-sr-only"> {result.number}</span>
                    </Button>
                  </li>
                ))}
              </ol>
            ) : shown !== null && shown.cache !== null && shown.status !== null ? (
              <div className={styles.gridArea} ref={grid} data-region-entry="">
                {shown.cache.problem !== null ? (
                  <p role="alert" className={connectionStyles.problem}>
                    {queryProblemText(t, shown.cache.problem)}
                  </p>
                ) : null}
                <ResultGrid
                  key={shown.key}
                  cache={shown.cache}
                  columns={shown.status.columns}
                  label={t('editor.result.gridLabel', { number: shown.number })}
                  onOpen={(cell, value, cut, focus) => setDetail({ ...cell, value, cut, focus })}
                  onCopy={(copied) => setNotice(t(copied ? 'editor.copied' : 'editor.copyFailed'))}
                />
                {detail !== null && detailColumn !== null ? (
                  <CellDetail
                    cell={detail}
                    column={detailColumn}
                    locale={locale}
                    onClose={() => {
                      setDetail(null);
                      grid.current?.querySelector<HTMLElement>('[role="grid"]')?.focus();
                    }}
                    onCopy={(copied) =>
                      setNotice(t(copied ? 'editor.copied' : 'editor.copyFailed'))
                    }
                  />
                ) : null}
              </div>
            ) : null}
          </div>
        ) : (
          <p className={connectionStyles.hint}>{t('editor.result.empty')}</p>
        )}
      </section>

      {prodDialog}
      {exporting !== null && run?.connectionId != null ? (
        <ExportDialog
          fetcher={fetcher}
          connectionId={run.connectionId}
          source={{ kind: 'sql', sql: exporting.sql, params: exporting.params }}
          subject={t('editor.exportSubject', { number: exporting.number, tab: tab.title })}
          fetchedRows={exporting.status?.rowCount ?? null}
          capped={(exporting.status?.truncatedBy ?? null) !== null}
          describeProblem={exportOrQueryProblemText}
          save={saveExport}
          onDone={(message) => {
            setNotice(message);
            setExporting(null);
          }}
          onClose={() => setExporting(null)}
        />
      ) : null}
      {closing !== null ? (
        <UnsavedScriptDialog
          open
          name={closing.title}
          onCancel={() => setClosing(null)}
          onDiscard={() => {
            removeTab(closing.id);
            setClosing(null);
          }}
          onSave={() => {
            save(closing);
            removeTab(closing.id);
            setClosing(null);
          }}
        />
      ) : null}
    </div>
  );
}
