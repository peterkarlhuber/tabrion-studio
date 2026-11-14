import { removeServiceWorkers } from './serviceWorkers';

function registration(result: boolean | Error) {
  return {
    unregister: vi.fn(() =>
      result instanceof Error ? Promise.reject(result) : Promise.resolve(result),
    ),
  } as unknown as ServiceWorkerRegistration;
}

describe('removeServiceWorkers (A-01)', () => {
  it('unregisters every registration of the origin', async () => {
    const found = [registration(true), registration(true)];
    const removed = await removeServiceWorkers({ getRegistrations: () => Promise.resolve(found) });
    expect(removed).toBe(2);
    for (const entry of found) expect(entry.unregister).toHaveBeenCalledTimes(1);
  });

  it('does nothing without service worker support', async () => {
    expect(await removeServiceWorkers(undefined)).toBe(0);
  });

  it('never throws', async () => {
    const failing = { getRegistrations: () => Promise.reject(new Error('SecurityError')) };
    expect(await removeServiceWorkers(failing)).toBe(0);
    const partly = [registration(new Error('busy')), registration(true)];
    expect(await removeServiceWorkers({ getRegistrations: () => Promise.resolve(partly) })).toBe(1);
  });
});
