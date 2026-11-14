/**
 * Studio never registers a service worker (finding A-01). A registration on its origin can only come from another
 * program that served a page on the same address and port earlier, and it would answer later page loads of that
 * origin. Since every start serves a fresh loopback address `127.a.b.c`, such a registration is not expected; this
 * removes any that is found, so it cannot outlive this page load. It is cleanup only: it does not make a page load that
 * such a worker already answered trustworthy, which is why the fresh address per start is the actual protection.
 */
export async function removeServiceWorkers(
  container: Pick<ServiceWorkerContainer, 'getRegistrations'> | undefined = navigator.serviceWorker,
): Promise<number> {
  if (container === undefined) return 0;
  try {
    const registrations = await container.getRegistrations();
    const results = await Promise.all(
      registrations.map((registration) => registration.unregister().catch(() => false)),
    );
    return results.filter(Boolean).length;
  } catch {
    return 0;
  }
}
