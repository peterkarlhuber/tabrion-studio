/**
 * "Connect" asked on another page (STU-020: a favorite or a recently opened profile on the start page). The start page
 * sets it and navigates to the connection manager, which takes it once, when it has read the profiles, selects the
 * profile and runs its own "Connect" (D-02 for PROD, the password dialog where one is missing). It lives in this
 * page's memory only, so a reload never connects by itself.
 */
let pending: string | null = null;

export function requestConnect(profileId: string): void {
  pending = profileId;
}

export function takeConnectRequest(): string | null {
  const id = pending;
  pending = null;
  return id;
}
