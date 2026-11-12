/**
 * "Connect" (STU-008, 08 section 5 "Connect"): D-02 before the first PROD connection of a profile in this page
 * session, then `POST /api/connections`, which checks the connection before it answers. While it opens, the header
 * shows the profile as "connecting"; afterwards the open connection, or again what it showed before.
 */
import { useState, type ReactNode } from 'react';

import { openConnection, type OpenedConnection } from '../../api/connections';
import type { ProfileItem, Result } from '../../api/profiles';
import { ProdConnectDialog } from '../../components/dialogs/StudioDialogs';
import { visible } from '../../lib/visible';
import { useConnectionSession } from '../../workspace';

/** 03 section 5.3: a PROD profile's statement timeout is 15 s unless the profile sets its own (D-02 names it). */
export const PROD_TIMEOUT_MS = 15_000;

export function prodTimeoutSeconds(item: ProfileItem): number {
  return Math.round((item.statement_timeout_ms ?? PROD_TIMEOUT_MS) / 1000);
}

type Done = (result: Result<OpenedConnection>, item: ProfileItem) => void;

export function useConnect(fetcher: typeof fetch): {
  /** Connect to `item` (D-02 first where needed); `done` gets the answer, `cancelled` runs when D-02 is cancelled. */
  connect: (item: ProfileItem, done: Done, cancelled?: () => void) => void;
  /** D-02 while it asks; render it next to the view. */
  dialog: ReactNode;
} {
  const session = useConnectionSession();
  const [asking, setAsking] = useState<{
    item: ProfileItem;
    done: Done;
    cancelled?: () => void;
  } | null>(null);

  const run = async (item: ProfileItem, done: Done) => {
    const name = visible(item.name);
    // What the header showed when the user asked (the connection to go back to if this one fails).
    const previous = session.active;
    session.setActive({
      name,
      environment: item.environment,
      status: 'connecting',
      profileId: item.id,
    });
    const result = await openConnection(fetcher, item.id);
    if (result.ok) {
      session.setActive({
        name,
        environment: result.value.environment ?? item.environment,
        status: 'connected',
        connectionId: result.value.connectionId,
        profileId: item.id,
      });
    } else {
      // Another connection may have become active meanwhile; only take back our own "connecting".
      session.setActive((current) =>
        current?.profileId === item.id && current.status === 'connecting' ? previous : current,
      );
    }
    done(result, item);
  };

  const connect = (item: ProfileItem, done: Done, cancelled?: () => void) => {
    if (item.environment === 'prod' && !session.prodConfirmed(item.id)) {
      setAsking({ item, done, cancelled });
    } else void run(item, done);
  };

  const dialog =
    asking === null ? null : (
      <ProdConnectDialog
        open
        name={visible(asking.item.name)}
        timeoutSeconds={prodTimeoutSeconds(asking.item)}
        onCancel={() => {
          setAsking(null);
          asking.cancelled?.();
        }}
        onConnect={() => {
          session.confirmProd(asking.item.id);
          setAsking(null);
          void run(asking.item, asking.done);
        }}
      />
    );
  return { connect, dialog };
}
