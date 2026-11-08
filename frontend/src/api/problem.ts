/**
 * Problem JSON from the Studio API (STU-004, docs/architecture/errors-and-logging.md). The page shows its own de/en
 * text for `code`; it never shows `detail` from the server as HTML.
 */
export interface Problem {
  code: string;
  kind: string;
  status: number;
  logId: string | null;
}

/** The problem in `response`, or a generic one when the body is not a problem document. */
export async function readProblem(response: Response): Promise<Problem> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const doc = (typeof body === 'object' && body !== null ? body : {}) as Record<string, unknown>;
  return {
    code: typeof doc.code === 'string' ? doc.code : 'studio.unknown',
    kind: typeof doc.kind === 'string' ? doc.kind : 'internal',
    status: response.status,
    logId: typeof doc.log_id === 'string' && /^[0-9a-f]{12}$/.test(doc.log_id) ? doc.log_id : null,
  };
}
