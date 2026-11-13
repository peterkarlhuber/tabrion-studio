/**
 * The banner "Connection lost" with "Reconnect" (TD-034, 08 section 5 "error recovery"), above the view on every page,
 * one per lost connection (`ConnectionSession.lost`).
 *
 * "Reconnect" reads the profile list again (the profile as it is now decides, like "Open" in the explorer), then
 * connects through the page's shared gate (`useConnect`): D-02 first for a PROD profile, since a lost connection's
 * confirmation was forgotten. The server does not return a connection whose database no longer answers; it opens a
 * new one. When the new connection arrives, the session ends the loss and tells the views (`onReconnected`), and the
 * focus goes to the main area. Studio only reads, so nothing was lost with the connection.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { OFFLINE, type Problem } from '../api/problem';
import { fetchProfiles } from '../api/profiles';
import { EnvBadge } from '../components/status/Status';
import { Button } from '../components/ui/Button';
import { focusRegion } from '../keyboard/regions';
import { problemText } from '../views/connections/messages';
import { useConnect } from '../views/connections/useConnect';
import { useConnectionSession, type LostConnection } from '../workspace';
import styles from './ConnectionLost.module.css';

const PROFILE_GONE: Problem = {
  code: 'studio.profile_not_found',
  kind: 'not_found',
  status: 404,
  logId: null,
};

function reasonKey(code: string): string {
  if (code === 'studio.connection_stale') return 'connectionLost.stale';
  if (code === 'studio.connection_not_found') return 'connectionLost.closed';
  return 'connectionLost.database';
}

export function ConnectionLost({ fetcher }: { fetcher: typeof fetch }) {
  const { t } = useTranslation();
  const session = useConnectionSession();
  const gate = useConnect(fetcher);
  const [busy, setBusy] = useState<string | null>(null);
  const [problems, setProblems] = useState<Record<string, Problem>>({});

  if (session.lost.length === 0) return gate.dialog;

  const reconnect = async (item: LostConnection) => {
    if (busy !== null) return;
    setBusy(item.connectionId);
    setProblems(({ [item.connectionId]: _, ...rest }) => rest);
    const fail = (problem: Problem) => {
      setBusy(null);
      setProblems((current) => ({ ...current, [item.connectionId]: problem }));
    };
    const list = await fetchProfiles(fetcher);
    if (list === null) return fail(OFFLINE);
    const profile = list.profiles.find((entry) => entry.id === item.profileId);
    if (profile === undefined) return fail(PROFILE_GONE);
    gate.connect(
      profile,
      (result) => {
        if (!result.ok) return fail(result.problem);
        setBusy(null);
        // The banner (and its focused button) goes: the focus moves to the view.
        window.setTimeout(() => focusRegion('main'), 0);
      },
      () => setBusy(null),
    );
  };

  return (
    <>
      {session.lost.map((item) => {
        const problem = problems[item.connectionId];
        const working = busy === item.connectionId;
        return (
          <div
            key={item.connectionId}
            role="alert"
            className={styles.banner}
            data-testid="connection-lost"
          >
            <div className={styles.row}>
              <p className={styles.title}>{t('connectionLost.title', { name: item.name })}</p>
              {item.environment !== null ? (
                <EnvBadge environment={item.environment} size="sm" />
              ) : null}
              <Button
                size="sm"
                variant="primary"
                aria-disabled={busy !== null || undefined}
                onClick={() => void reconnect(item)}
                data-testid="connection-reconnect"
              >
                {working ? t('connectionLost.reconnecting') : t('connectionLost.reconnect')}
              </Button>
            </div>
            <p className={styles.text}>
              {t(reasonKey(item.code))} {t('connectionLost.nothingLost')}
            </p>
            {problem !== undefined ? (
              <p className={styles.text} data-testid="connection-reconnect-problem">
                {t('connectionLost.failed', { problem: problemText(t, problem) })}
              </p>
            ) : null}
          </div>
        );
      })}
      {gate.dialog}
    </>
  );
}
