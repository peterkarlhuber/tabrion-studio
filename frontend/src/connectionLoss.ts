/**
 * The page's record of gone connections (TD-034, QA-S1 S1-32): which connections it saw opened or listed, which are
 * lost now, which the user disconnected, and which were replaced by "Reconnect". `App` keeps one and puts its state in
 * the connection session, the one source every view reads; the banner (`shell/ConnectionLost.tsx`) shows `lost`, and
 * the views follow `onReconnected`.
 *
 * A loss (the database stopped answering, the server closed the connection) and the user's own "Disconnect" are told
 * apart (`GoneCause`, owner decision of 2026-11-14 14:13Z): after a disconnect the profile stays disconnected until a
 * connection of it is opened again, and a run that needs it asks first (`askReconnect`): the banner with "Reconnect"
 * shows, nothing is opened or run, and only "Reconnect" on the banner (`reconnected`) lets the run go on.
 */
import type { OpenedConnection } from './api/connections';
import { visible } from './lib/visible';
import type {
  DisconnectedConnection,
  Environment,
  GoneCause,
  LostConnection,
  Reconnected,
} from './workspace';

export interface LossEffects {
  /** The list of lost connections changed. */
  changed: (lost: LostConnection[]) => void;
  /** Forget the D-02 confirmation of `profileId` ("Reconnect" asks D-02 again for PROD). */
  forgetProd: (profileId: string) => void;
  /** The header's connection is `connectionId`: show it as disconnected. */
  disconnected: (connectionId: string) => void;
  /** The header's connection now, for the name the page showed. */
  header: () => { connectionId?: string; name: string; environment: Environment } | null;
}

/** The profile's name in its id (`user:<name>`, `project:<name>`), escaped for the page. */
export function profileName(profileId: string): string {
  const colon = profileId.indexOf(':');
  return visible(colon === -1 ? profileId : profileId.slice(colon + 1));
}

export class ConnectionLoss {
  private known = new Map<string, { profileId: string; environment: Environment | null }>();
  private replaced = new Set<string>();
  private listeners = new Set<(event: Reconnected) => void>();
  private effects: LossEffects | null = null;
  /** The profiles the user disconnected (S1-32), by profile id, until one of their connections is opened again. */
  private disconnectedProfiles = new Map<string, DisconnectedConnection>();
  /** What waits for "Reconnect" on the banner, by profile id (`askReconnect`). */
  private waiting = new Map<string, (connectionId: string) => void>();
  lost: LostConnection[] = [];

  /** Give the page's effects (again, when they change); returns the function that takes them back. */
  attach(effects: LossEffects): () => void {
    this.effects = effects;
    return () => {
      if (this.effects === effects) this.effects = null;
    };
  }

  /** A connection the page opened (`opened`) or saw in the list; an opened one ends the loss of its profile. */
  seen(connection: OpenedConnection, opened: boolean): void {
    if (connection.profileId === null) return;
    this.known.set(connection.connectionId, {
      profileId: connection.profileId,
      environment: connection.environment,
    });
    if (!opened) return;
    // A connection of the profile is open again (by "Reconnect", "Connect" or "Open"): the user's disconnect ends.
    this.disconnectedProfiles.delete(connection.profileId);
    const ended = this.lost.filter((item) => item.profileId === connection.profileId);
    if (ended.length === 0) return;
    this.lost = this.lost.filter((item) => item.profileId !== connection.profileId);
    this.effects?.changed(this.lost);
    for (const item of ended) {
      if (item.connectionId !== connection.connectionId) this.replaced.add(item.connectionId);
      const event = {
        profileId: item.profileId,
        from: item.connectionId,
        to: connection.connectionId,
      };
      for (const listener of [...this.listeners]) listener(event);
    }
  }

  /** A request on `connectionId` answered `code`. Unknown and replaced connections are ignored. */
  markLost(connectionId: string, code: string): void {
    const entry = this.known.get(connectionId);
    const effects = this.effects;
    if (entry === undefined || this.replaced.has(connectionId) || effects === null) return;
    effects.forgetProd(entry.profileId);
    effects.disconnected(connectionId);
    if (this.lost.some((item) => item.connectionId === connectionId)) return;
    const header = effects.header();
    const shown = header?.connectionId === connectionId ? header : null;
    const cause: GoneCause = this.disconnectedProfiles.has(entry.profileId)
      ? 'disconnected'
      : 'lost';
    this.lost = [
      ...this.lost,
      {
        connectionId,
        profileId: entry.profileId,
        name: shown?.name ?? profileName(entry.profileId),
        environment: entry.environment ?? shown?.environment ?? null,
        cause,
        code,
      },
    ];
    effects.changed(this.lost);
  }

  /**
   * The user chose "Disconnect" for `connection` (S1-32): its profile stays disconnected until a connection of it is
   * opened again. A banner already shown for it now names the disconnect. The D-02 confirmation is kept ("Connect" in
   * the connection manager asks once per session, 08 section 6); "Reconnect" on the banner asks D-02 again itself.
   */
  disconnect(connection: DisconnectedConnection): void {
    this.known.set(connection.connectionId, {
      profileId: connection.profileId,
      environment: connection.environment,
    });
    this.disconnectedProfiles.set(connection.profileId, connection);
    // A run that waited for an earlier "Reconnect" of the profile does not wait for this one.
    this.waiting.delete(connection.profileId);
    if (this.lost.some((item) => item.profileId === connection.profileId)) {
      this.lost = this.lost.map((item) =>
        item.profileId === connection.profileId ? { ...item, cause: 'disconnected' } : item,
      );
      this.effects?.changed(this.lost);
    }
  }

  isDisconnected(profileId: string): boolean {
    return this.disconnectedProfiles.has(profileId);
  }

  /**
   * A run needs `profileId`, which the user disconnected: show the banner for it (once) and keep `resume` for
   * "Reconnect" on the banner; a later call for the profile replaces it. Returns the function that drops `resume`.
   * A profile that is not disconnected (any more) shows nothing and keeps nothing.
   */
  askReconnect(profileId: string, resume: (connectionId: string) => void): () => void {
    const entry = this.disconnectedProfiles.get(profileId);
    const effects = this.effects;
    if (entry === undefined || effects === null) return () => undefined;
    this.waiting.set(profileId, resume);
    if (!this.lost.some((item) => item.profileId === profileId)) {
      this.lost = [...this.lost, { ...entry, cause: 'disconnected', code: null }];
      effects.changed(this.lost);
    }
    return () => {
      if (this.waiting.get(profileId) === resume) this.waiting.delete(profileId);
    };
  }

  /** "Reconnect" on the banner opened `connectionId` for `profileId`: what waited for it runs, once. */
  reconnected(profileId: string, connectionId: string): void {
    const resume = this.waiting.get(profileId);
    this.waiting.delete(profileId);
    resume?.(connectionId);
  }

  onReconnected(listener: (event: Reconnected) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
}
