/**
 * A minimal client-side router for the few views of the shell (08 section 2). The backend serves index.html for
 * exactly these paths (CLIENT_ROUTES in src/tabrion_studio/app.py; tests/unit/test_app.py keeps the two lists in
 * step). A routing library arrives when the workspace routes (/c/:conn/...) do.
 */
import { useSyncExternalStore } from 'react';

export const ROUTES = [
  '/',
  '/connections',
  '/explorer',
  '/sql',
  '/migrations',
  '/diagnostics',
  '/vectors',
  '/history',
  '/settings',
] as const;
export type Route = (typeof ROUTES)[number];

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener('popstate', listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener('popstate', listener);
  };
}

export function navigate(path: Route): void {
  if (window.location.pathname !== path) {
    window.history.pushState(null, '', path);
    for (const listener of listeners) listener();
  }
}

/** The current path; anything that is not a known route renders the not-found view. */
export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname);
}
