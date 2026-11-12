/**
 * "Connect" (STU-008, 08 section 5 "Connect") and the D-02 gate in front of every new PROD connection.
 *
 * - `guard` asks D-02 before anything that opens a connection to a PROD profile the user has not confirmed in this page
 *   session (`ConnectionSession.prodConfirmed`, keyed by the profile and its target): "Connect", and also the
 *   connection test, which connects too (C-2 of `stu-008-connect-review.md`, the rule of `stu-015-ui-review.md` L-1:
 *   never a new PROD connection without D-02).
 * - `connect` then sends `POST /api/connections`, which checks the connection before it answers. While it opens, the
 *   header shows the profile as "connecting". Only the newest connect request may set the header (L-4); a failed one
 *   gives back the connection the header showed before, and the caller reads the open list again, which drops it if
 *   it was closed meanwhile. An older request whose "connecting" was given back that way still decides the header
 *   when it answers (L-10 of the delta review), so the header never stays "connecting".
 */
import { useState, type ReactNode } from 'react';

import { openConnection, type OpenedConnection } from '../../api/connections';
import type { ProfileItem, Result } from '../../api/profiles';
import { ProdConnectDialog } from '../../components/dialogs/StudioDialogs';
import { visible } from '../../lib/visible';
import { useConnectionSession, type ActiveConnection } from '../../workspace';

/** 03 section 5.3: a PROD profile's statement timeout is 15 s unless the profile sets its own (D-02 names it). */
export const PROD_TIMEOUT_MS = 15_000;

export function prodTimeoutSeconds(item: ProfileItem): number {
  return Math.max(1, Math.round((item.statement_timeout_ms ?? PROD_TIMEOUT_MS) / 1000));
}

type Done = (result: Result<OpenedConnection>, item: ProfileItem) => void;

/** The header shows `item` as "connecting" (an entry without a connection). */
function ownConnecting(current: ActiveConnection | null, item: ProfileItem): boolean {
  return current?.profileId === item.id && current.status === 'connecting';
}

export function useConnect(fetcher: typeof fetch): {
  /** Run `proceed` for `item`, after D-02 where needed; `cancelled` runs when D-02 is cancelled. */
  guard: (item: ProfileItem, proceed: () => void, cancelled?: () => void) => void;
  /** Connect to `item` (D-02 first where needed); `done` gets the answer, `cancelled` runs when D-02 is cancelled. */
  connect: (item: ProfileItem, done: Done, cancelled?: () => void) => void;
  /** D-02 while it asks; render it next to the view. */
  dialog: ReactNode;
} {
  const session = useConnectionSession();
  const [asking, setAsking] = useState<{
    item: ProfileItem;
    proceed: () => void;
    cancelled?: () => void;
  } | null>(null);

  const run = async (item: ProfileItem, done: Done) => {
    const name = visible(item.name);
    const request = session.beginConnect();
    // What the header showed when the user asked (given back if this one fails).
    const previous = session.active;
    session.setActive({
      name,
      environment: item.environment,
      status: 'connecting',
      profileId: item.id,
    });
    // The environment this page confirmed for (D-02 on the server, TD-038).
    const result = await openConnection(fetcher, item.id, item.environment);
    const opened: ActiveConnection | null = result.ok
      ? {
          name,
          environment: result.value.environment ?? item.environment,
          status: 'connected',
          connectionId: result.value.connectionId,
          profileId: item.id,
        }
      : null;
    // What the header shows instead of a failed request's "connecting": the connection it showed before, unless that
    // was itself a request still "connecting" (which answers later, or never sets the header if it is stale).
    const before = previous?.status === 'connecting' ? null : previous;
    if (session.isLatestConnect(request)) {
      session.setActive((current) =>
        opened !== null ? opened : ownConnecting(current, item) ? previous : current,
      );
    } else {
      // L-10 of the connect review: a newer request may have given the header back to this one's "connecting" (it
      // failed, or was dropped). Then this answer still decides; otherwise the newer request owns the header.
      session.setActive((current) => (ownConnecting(current, item) ? (opened ?? before) : current));
    }
    done(result, item);
  };

  const guard = (item: ProfileItem, proceed: () => void, cancelled?: () => void) => {
    if (item.environment === 'prod' && !session.prodConfirmed(item)) {
      setAsking({ item, proceed, cancelled });
    } else proceed();
  };

  const connect = (item: ProfileItem, done: Done, cancelled?: () => void) =>
    guard(item, () => void run(item, done), cancelled);

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
          session.confirmProd(asking.item);
          setAsking(null);
          asking.proceed();
        }}
      />
    );
  return { guard, connect, dialog };
}
