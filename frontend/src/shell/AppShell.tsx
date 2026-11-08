/**
 * The app shell (STU-005, 08 section 2): header with the status displays, activity bar, main area with the open
 * tabs, status bar. With a PROD connection the shell gets a 3 px red frame at the top, every tab and the header show
 * the PROD badge and the window title starts with "[PROD]" (STU-051).
 */
import { Database, House, Settings } from 'lucide-react';
import { useEffect, type MouseEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { AccessModeChip, ConnectionSwitcher, EnvBadge, TxChip } from '../components/status/Status';
import type { Health } from '../heartbeat';
import { currentLocale } from '../i18n';
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
  children,
}: {
  path: string;
  /** The current view's title for the window title, null on the start page. */
  pageTitle: string | null;
  server: ServerState;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const { theme } = useTheme();
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
      <header className={styles.header}>
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
      </header>
      <div className={styles.body}>
        <nav className={styles.activity} aria-label={t('nav.label')}>
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
            <div className={styles.tabs} role="tablist" aria-label={t('tabs.label')}>
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
                  <span className={styles.tabTitle}>{tab.title}</span>
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
            role={activeTab ? 'tabpanel' : undefined}
            aria-labelledby={activeTab ? `tab-${activeTab.id}` : undefined}
          >
            {children}
          </main>
        </div>
      </div>
      <footer className={styles.statusbar} aria-label={t('statusbar.label')}>
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
    </div>
  );
}
