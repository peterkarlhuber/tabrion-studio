/**
 * Problem JSON from the Studio API (STU-004, docs/architecture/errors-and-logging.md). The page shows its own de/en
 * text for `code`; it never shows `detail` from the server as HTML.
 */
export interface Problem {
  code: string;
  kind: string;
  status: number;
  logId: string | null;
  /** The fields of a refused form (STU-008): fixed key names (`profile.host`) and `config.*` codes. */
  issues?: ProblemIssue[];
}

export interface ProblemIssue {
  pointer: string;
  code: string;
}

const ISSUE_POINTER = /^(?:profile|settings)(?:\.[a-z_]{1,32}){0,2}$/;
const ISSUE_CODE = /^config\.[a-z_]{1,48}$/;

function issues(value: unknown): ProblemIssue[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.flatMap((item: unknown) => {
    const issue = item as Partial<ProblemIssue> | null;
    return issue !== null &&
      typeof issue === 'object' &&
      typeof issue.pointer === 'string' &&
      typeof issue.code === 'string' &&
      ISSUE_POINTER.test(issue.pointer) &&
      ISSUE_CODE.test(issue.code)
      ? [{ pointer: issue.pointer, code: issue.code }]
      : [];
  });
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
    issues: issues(doc.issues),
  };
}

/** The problem for a request that did not reach the server. */
export const OFFLINE: Problem = {
  code: 'studio.offline',
  kind: 'internal',
  status: 0,
  logId: null,
};
