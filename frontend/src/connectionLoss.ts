/**
 * The page's record of lost connections (TD-034): which connections it saw opened or listed, which are lost now, and
 * which were replaced by "Reconnect". `App` keeps one and puts its state in the connection session; the banner
 * (`shell/ConnectionLost.tsx`) shows `lost`, and the views follow `onReconnected`.
 */
import type { OpenedConnection } from './api/connections';
import { visible } from './lib/visible';
import type { Environment, LostConnection, Reconnected } from './workspace';

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
    this.lost = [
      ...this.lost,
      {
        connectionId,
        profileId: entry.profileId,
        name: shown?.name ?? profileName(entry.profileId),
        environment: entry.environment ?? shown?.environment ?? null,
        code,
      },
    ];
    effects.changed(this.lost);
  }

  onReconnected(listener: (event: Reconnected) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
}
