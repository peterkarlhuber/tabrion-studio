/**
 * The app shell (STU-005, 08 section 2): header with the status displays, activity bar, main area with the open
 * tabs, status bar. With a PROD connection the shell gets a 3 px red frame at the top, every tab and the header show
 * the PROD badge and the window title starts with "[PROD]" (STU-051).
 */
import {
  CircleHelp,
  Command as CommandIcon,
  Database,
  GitBranch,
  History,
  House,
  ListTree,
  Settings,
  SquareTerminal,
  Stethoscope,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState, type MouseEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { IconButton } from '../components/ui/IconButton';
import { AccessModeChip, ConnectionSwitcher, EnvBadge, TxChip } from '../components/status/Status';
import type { Health } from '../heartbeat';
import { currentLocale } from '../i18n';
import { buildCommands } from '../keyboard/commands';
import { CommandPalette } from '../keyboard/CommandPalette';
import { useKeyboardPlatform, useKeyNames } from '../keyboard/platform';
import { focusRegion } from '../keyboard/regions';
import { ShortcutHelp } from '../keyboard/ShortcutHelp';
import { ariaShortcut, SHORTCUTS, shortcutText } from '../keyboard/shortcuts';
import { useShortcuts } from '../keyboard/useShortcuts';
import clsx from '../lib/clsx';
import { navigate, type Route } from '../router';
import { useTheme } from '../theme';
import { ENV_LABEL, useWorkspace, type Workspace } from '../workspace';
import styles from './AppShell.module.css';

export type ServerState =
  { kind: 'loading' } | { kind: 'ok'; health: Health } | { kind: 'offline' };

const NAV: { path: Route; key: string; icon: ReactNode }[] = [
  { path: '/', key: 'nav.start', icon: <House /> },
  { path: '/connections', key: 'nav.connections', icon: <Database /> },
  { path: '/explorer', key: 'nav.explorer', icon: <ListTree /> },
  { path: '/sql', key: 'nav.sql', icon: <SquareTerminal /> },
  { path: '/history', key: 'nav.history', icon: <History /> },
  { path: '/migrations', key: 'nav.migrations', icon: <GitBranch /> },
  { path: '/diagnostics', key: 'nav.diagnostics', icon: <Stethoscope /> },
];

/** The window title: `[PROD] name – Tabrion Studio` with a connection, else the page title. */
export function windowTitle(
  workspace: Workspace,
  page: string | null,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const connection = workspace.connection;
  if (connection !== null) {
    return t('app.connectionTitle', {
      env: ENV_LABEL[connection.environment],
      name: connection.name,
    });
  }
  return page === null ? t('app.name') : t('app.title', { page });
}

function NavLink({
  path,
  current,
  label,
  icon,
}: {
  path: Route;
  current: string;
  label: string;
  icon: ReactNode;
}) {
  const active = path === current;
  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return;
    event.preventDefault();
    navigate(path);
  };
  return (
    <a
      href={path}
      onClick={onClick}
      className={clsx(styles.navLink, active && styles.navActive)}
      aria-label={label}
      aria-current={active ? 'page' : undefined}
      data-tooltip={label}
    >
      <span aria-hidden="true">{icon}</span>
    </a>
  );
}

export function AppShell({
  path,
  pageTitle,
  server,
  banner = null,
  children,
}: {
  path: string;
  /** Shown above the view, on every page: the "Connection lost" banner (TD-034). */
  banner?: ReactNode;
  /** The current view's title for the window title, null on the start page. */
  pageTitle: string | null;
  server: ServerState;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const { theme, preference, density } = useTheme();
  const platform = useKeyboardPlatform();
  const keyNames = useKeyNames();
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  const openPalette = useCallback(() => setPalette(true), []);
  const openHelp = useCallback(() => setHelp(true), []);
  const openHistory = useCallback(() => {
    navigate('/history');
    window.setTimeout(() => focusRegion('main'), 0);
  }, []);
  const shortcutHandlers = useMemo(
    () => ({ openPalette, openHelp, openHistory }),
    [openPalette, openHelp, openHistory],
  );
  useShortcuts(shortcutHandlers);
  const commands = useMemo(
    () =>
      palette ? buildCommands({ t, path, theme: preference, density, openHelp, platform }) : [],
    [palette, t, path, preference, density, openHelp, platform],
  );
  const workspace = useWorkspace();
  const connection = workspace.connection;
  const environment = connection?.environment;

  useEffect(() => {
    document.title = windowTitle(workspace, pageTitle, t);
  }, [workspace, pageTitle, t]);

  const activeTab = workspace.tabs.find((tab) => tab.id === workspace.activeTab);
  const mark =
    theme === 'dark'
      ? '/branding/tabrion-studio-mark-on-dark-128.png'
      : '/branding/tabrion-studio-mark-128.png';

  return (
    <div
      className={clsx(styles.shell, environment === 'prod' && styles.prod)}
      data-env={environment}
    >
      <a className="tb-skip-link" href="#main">
        {t('app.skipToContent')}
      </a>
      <header className={styles.header} data-region="header">
        <a
          href="/"
          className={styles.brand}
          onClick={(event) => {
            event.preventDefault();
            navigate('/');
          }}
        >
          <img src={mark} alt="" width={24} height={24} className={styles.mark} />
          <span className={styles.brandName}>{t('app.name')}</span>
        </a>
        <div className={styles.status} role="group" aria-label={t('header.label')}>
          <ConnectionSwitcher connection={connection} onOpen={() => navigate('/connections')} />
          {connection !== null ? (
            <>
              <EnvBadge environment={connection.environment} />
              <AccessModeChip environment={connection.environment} />
              <TxChip />
            </>
          ) : null}
        </div>
        <div className={styles.tools}>
          <IconButton
            label={t('keyboard.palette.open', {
              keys: shortcutText(SHORTCUTS.palette, platform, keyNames),
            })}
            icon={<CommandIcon />}
            aria-keyshortcuts={ariaShortcut(SHORTCUTS.palette, platform)}
            aria-haspopup="dialog"
            onClick={openPalette}
            data-testid="open-palette"
          />
          <IconButton
            label={t('keyboard.help.open', {
              keys: shortcutText(SHORTCUTS.help, platform, keyNames),
            })}
            icon={<CircleHelp />}
            aria-keyshortcuts={ariaShortcut(SHORTCUTS.help, platform)}
            aria-haspopup="dialog"
            onClick={openHelp}
            data-testid="open-help"
          />
        </div>
      </header>
      <div className={styles.body}>
        <nav className={styles.activity} aria-label={t('nav.label')} data-region="nav">
          <ul className={styles.navList}>
            {NAV.map((item) => (
              <li key={item.path}>
                <NavLink path={item.path} current={path} label={t(item.key)} icon={item.icon} />
              </li>
            ))}
          </ul>
          <ul className={styles.navList}>
            <li>
              <NavLink
                path="/settings"
                current={path}
                label={t('nav.settings')}
                icon={<Settings />}
              />
            </li>
          </ul>
        </nav>
        <div className={styles.workarea}>
          {workspace.tabs.length > 0 ? (
            <div
              className={styles.tabs}
              role="tablist"
              aria-label={t('tabs.label')}
              data-region="tabs"
            >
              {workspace.tabs.map((tab) => (
                <button
                  key={tab.id}
                  id={`tab-${tab.id}`}
                  type="button"
                  role="tab"
                  aria-selected={tab.id === workspace.activeTab}
                  aria-controls="main"
                  tabIndex={tab.id === workspace.activeTab ? 0 : -1}
                  className={styles.tab}
                  data-testid="workspace-tab"
                >
                  <span className={styles.tabTitle} title={tab.title}>
                    {tab.title}
                  </span>
                  <span className="tb-sr-only">, </span>
                  <EnvBadge environment={tab.environment} size="sm" />
                </button>
              ))}
            </div>
          ) : null}
          <main
            id="main"
            className={styles.main}
            tabIndex={-1}
            data-region="main"
            role={activeTab ? 'tabpanel' : undefined}
            aria-labelledby={activeTab ? `tab-${activeTab.id}` : undefined}
          >
            {banner}
            {children}
          </main>
        </div>
      </div>
      <footer
        className={styles.statusbar}
        aria-label={t('statusbar.label')}
        tabIndex={-1}
        data-region="statusbar"
      >
        <span role="status" data-testid="backend-status" className={styles.server}>
          <span
            className={clsx(
              styles.dot,
              server.kind === 'ok' && styles.dotOk,
              server.kind === 'offline' && styles.dotOff,
            )}
            aria-hidden="true"
          />
          {server.kind === 'loading' && t('statusbar.connecting')}
          {server.kind === 'ok' && t('statusbar.server', { version: server.health.version })}
          {server.kind === 'offline' && t('statusbar.offline')}
        </span>
        <span>{t('statusbar.language', { language: t(`languages.${currentLocale()}`) })}</span>
      </footer>
      <CommandPalette open={palette} commands={commands} onClose={() => setPalette(false)} />
      <ShortcutHelp open={help} onClose={() => setHelp(false)} />
    </div>
  );
}
