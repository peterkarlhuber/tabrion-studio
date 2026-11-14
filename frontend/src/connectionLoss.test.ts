/** TD-034: the page's record of lost connections, the replaced ones and the D-02 rule of "Reconnect". */
import { ConnectionLoss, profileName } from './connectionLoss';
import type { LostConnection } from './workspace';

function setup(
  header: { connectionId?: string; name: string; environment: 'dev' | 'prod' } | null = null,
) {
  const loss = new ConnectionLoss();
  const effects = {
    changed: vi.fn<(lost: LostConnection[]) => void>(),
    forgetProd: vi.fn<(profileId: string) => void>(),
    disconnected: vi.fn<(connectionId: string) => void>(),
    header: () => header,
  };
  loss.attach(effects);
  return { loss, effects };
}

const PROD = { connectionId: 'c1', profileId: 'user:live', environment: 'prod' as const };

describe('ConnectionLoss', () => {
  it('records a lost connection once, forgets its D-02 confirmation and marks the header', () => {
    const { loss, effects } = setup({
      connectionId: 'c1',
      name: 'live (shown)',
      environment: 'prod',
    });
    loss.seen(PROD, true);
    loss.markLost('c1', 'db.connection');
    loss.markLost('c1', 'db.connection');
    expect(loss.lost).toEqual([
      {
        connectionId: 'c1',
        profileId: 'user:live',
        name: 'live (shown)',
        environment: 'prod',
        cause: 'lost',
        code: 'db.connection',
      },
    ]);
    expect(effects.changed).toHaveBeenCalledTimes(1);
    expect(effects.forgetProd).toHaveBeenCalledWith('user:live');
    expect(effects.disconnected).toHaveBeenCalledWith('c1');
  });

  it('ignores a connection it never saw, and one without a profile', () => {
    const { loss, effects } = setup();
    loss.seen({ connectionId: 'c2', profileId: null, environment: null }, true);
    loss.markLost('c2', 'db.connection');
    loss.markLost('c3', 'studio.connection_stale');
    expect(loss.lost).toEqual([]);
    expect(effects.forgetProd).not.toHaveBeenCalled();
  });

  it('names a listed connection by its profile when the header shows another one', () => {
    const { loss } = setup({ connectionId: 'other', name: 'x', environment: 'dev' });
    loss.seen({ connectionId: 'c4', profileId: 'project:parts‮dev', environment: 'dev' }, false);
    loss.markLost('c4', 'studio.connection_not_found');
    expect(loss.lost[0]?.name).toBe(profileName('project:parts‮dev'));
    expect(loss.lost[0]?.name).not.toContain('‮'); // escaped like every name on the page
  });

  it('ends the loss when the profile opens again, tells the views, and ignores the replaced id since', () => {
    const { loss, effects } = setup();
    const heard = vi.fn();
    const stop = loss.onReconnected(heard);
    loss.seen(PROD, true);
    loss.markLost('c1', 'db.connection');
    // A list that still names the lost connection does not end the loss.
    loss.seen(PROD, false);
    expect(loss.lost).toHaveLength(1);
    loss.seen({ ...PROD, connectionId: 'c9' }, true);
    expect(loss.lost).toEqual([]);
    expect(effects.changed).toHaveBeenLastCalledWith([]);
    expect(heard).toHaveBeenCalledWith({ profileId: 'user:live', from: 'c1', to: 'c9' });
    loss.markLost('c1', 'studio.connection_not_found');
    expect(loss.lost).toEqual([]);
    stop();
    loss.markLost('c9', 'db.connection');
    loss.seen({ ...PROD, connectionId: 'c9' }, true); // the database came back: the same id
    expect(heard).toHaveBeenCalledTimes(1);
  });

  describe("the user's own Disconnect (QA-S1 S1-32)", () => {
    const DEV = {
      connectionId: 'd1',
      profileId: 'user:shop',
      name: 'Shop (DEV)',
      environment: 'dev' as const,
    };

    it('keeps the profile disconnected, shows nothing until a run asks, and keeps D-02', () => {
      const { loss, effects } = setup();
      expect(loss.isDisconnected('user:shop')).toBe(false);
      loss.disconnect(DEV);
      expect(loss.isDisconnected('user:shop')).toBe(true);
      expect(loss.isDisconnected('user:other')).toBe(false);
      expect(loss.lost).toEqual([]);
      expect(effects.changed).not.toHaveBeenCalled();
      // "Connect" in the connection manager asks D-02 once per session; "Reconnect" asks again by itself.
      expect(effects.forgetProd).not.toHaveBeenCalled();
    });

    it('a run that asks shows the banner once, as a disconnect, and waits for "Reconnect"', () => {
      const { loss, effects } = setup();
      loss.disconnect(DEV);
      const first = vi.fn();
      const second = vi.fn();
      loss.askReconnect('user:shop', first);
      loss.askReconnect('user:shop', second);
      expect(loss.lost).toEqual([{ ...DEV, cause: 'disconnected', code: null }]);
      expect(effects.changed).toHaveBeenCalledTimes(1);
      // The connection opens again: the banner and the disconnect end, but nothing runs before "Reconnect" says so.
      loss.seen({ connectionId: 'd2', profileId: 'user:shop', environment: 'dev' }, true);
      expect(loss.lost).toEqual([]);
      expect(loss.isDisconnected('user:shop')).toBe(false);
      expect(first).not.toHaveBeenCalled();
      expect(second).not.toHaveBeenCalled();
      loss.reconnected('user:shop', 'd2');
      // Only the newest run goes on, once.
      expect(first).not.toHaveBeenCalled();
      expect(second).toHaveBeenCalledExactlyOnceWith('d2');
      loss.reconnected('user:shop', 'd2');
      expect(second).toHaveBeenCalledTimes(1);
    });

    it('a run that stopped waiting does not go on; a profile that is not disconnected keeps nothing', () => {
      const { loss, effects } = setup();
      const resume = vi.fn();
      loss.askReconnect('user:shop', resume)();
      expect(loss.lost).toEqual([]);
      expect(effects.changed).not.toHaveBeenCalled();
      loss.disconnect(DEV);
      loss.askReconnect('user:shop', resume)();
      loss.reconnected('user:shop', 'd2');
      expect(resume).not.toHaveBeenCalled();
      // A new disconnect drops a run that waited for an earlier "Reconnect".
      loss.askReconnect('user:shop', resume);
      loss.disconnect(DEV);
      loss.reconnected('user:shop', 'd2');
      expect(resume).not.toHaveBeenCalled();
    });

    it('a request that fails on the disconnected connection names the disconnect, not a loss', () => {
      const { loss, effects } = setup();
      loss.seen(DEV, true);
      loss.disconnect(DEV);
      loss.markLost('d1', 'studio.connection_not_found');
      expect(loss.lost).toEqual([
        {
          ...DEV,
          name: profileName('user:shop'),
          cause: 'disconnected',
          code: 'studio.connection_not_found',
        },
      ]);
      expect(effects.disconnected).toHaveBeenCalledWith('d1');
    });

    it('a loss shown before the disconnect becomes the disconnect; a loss of another profile stays a loss', () => {
      const { loss } = setup();
      loss.seen(DEV, true);
      loss.seen(PROD, true);
      loss.markLost('d1', 'db.connection');
      loss.markLost('c1', 'db.connection');
      loss.disconnect(DEV);
      expect(loss.lost.map((item) => [item.connectionId, item.cause])).toEqual([
        ['d1', 'disconnected'],
        ['c1', 'lost'],
      ]);
    });
  });

  it('does nothing without the page attached', () => {
    const loss = new ConnectionLoss();
    loss.seen(PROD, true);
    loss.markLost('c1', 'db.connection');
    expect(loss.lost).toEqual([]);
  });
});
