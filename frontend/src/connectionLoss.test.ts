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

  it('does nothing without the page attached', () => {
    const loss = new ConnectionLoss();
    loss.seen(PROD, true);
    loss.markLost('c1', 'db.connection');
    expect(loss.lost).toEqual([]);
  });
});
