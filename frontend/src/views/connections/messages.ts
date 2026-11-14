/** De/en texts for problems and form issues (STU-008): the page shows its own text for a code, never the server's. */
import type { TFunction } from 'i18next';

import type { Problem, ProblemIssue } from '../../api/problem';

const PROBLEMS = new Set([
  'config_stale',
  'config_invalid',
  'config_hand_written',
  'config_dir_unsafe',
  'config_unwritable',
  'config_too_large',
  'config_not_storable',
  'profile_invalid',
  'settings_invalid',
  'profile_exists',
  'profile_read_only',
  'profile_not_found',
  'keyring_unavailable',
  'keyring_failed',
  'secret_invalid',
  'secret_not_storable',
  // Opening a connection and the migration status (STU-009, STU-015).
  'project_untrusted',
  'trust_stale',
  'trust_unavailable',
  'no_migrations',
  'migrations_invalid',
  'migrations_changed',
  'migrations_revisions',
  'migrations_database',
  'migrations_timeout',
  'migrations_busy',
  'migrations_failed',
  'connection_failed',
  'connection_config',
  'connection_tls',
  'connection_limit',
  'connection_busy',
  'connection_stale',
  'prod_confirmation_required',
  'connection_not_found',
  'database_missing',
  'database_unsafe',
  'database_outside',
  'secret_missing',
  'secret_env_missing',
  'secret_file_outside',
  'secret_file_unsafe',
  'secret_file_not_private',
  'secret_file_unreadable',
  // The explorer (STU-010), and an answer the page cannot read (STU-014 diagnostics, STU-010).
  'object_not_found',
  'explorer_engine',
  // The ER diagram (STU-023).
  'diagram_too_large',
  'answer_invalid',
  // The diagnosis report (STU-025, TD-036).
  'report_invalid',
  'report_unavailable',
  // The state database, the history, favorites, completion and scripts (STU-020).
  'state_unavailable',
  'favorites_full',
  'catalog_not_loaded',
  'script_invalid',
  'script_not_found',
  'script_unreadable',
  'script_too_large',
  'script_exists',
  'script_stale',
  'script_folder_missing',
  'script_link',
  'script_permission_denied',
  'script_locked',
  'script_disk_full',
  'script_write_failed',
  'offline',
]);

const ISSUES = new Set([
  'required',
  'type',
  'range',
  'length',
  'choice',
  'control_character',
  'network_path',
  'url_character',
  'not_for_engine',
  'plain_password',
  'rename_unsupported',
  'unknown_key',
  'too_many',
  'encoding',
]);

/**
 * The `db.<kind>` problems of opening or testing a connection with a text that says what to check (08 section 4.2:
 * "the error shows the remedy"); other kinds get the general database text with their kind.
 */
const DB_KINDS = new Set([
  'connection',
  'timeout',
  'canceled',
  'locked',
  'permission',
  'read_only',
  'corrupt',
  'disk_full',
  'resource',
]);

/**
 * The access protection's refusals (`access.*`, `security/access.py`, `security/body.py`) by the text they get
 * (QA-S1 S1-32). A session that ended under an open tab (Studio started again, which signs the new tab in and ends the
 * old tab's session, or restarted) is `access.csrf_invalid`: the text says so and how to go on, like the page's
 * "Session ended" notice. `access.session_required` is also a tab that was never signed in, and its text says both.
 * The checks of Host, Origin and fetch metadata refuse a request that did not come from Studio's own page; JSON only,
 * the control channel and the CLI-only routes refuse requests that page never sends. Both texts name the code.
 */
const ACCESS: Record<string, string> = {
  'access.session_required': 'session_missing',
  'access.csrf_invalid': 'session_ended',
  'access.launch_used': 'sign_in_used',
  'access.launch_expired': 'sign_in_expired',
  'access.launch_invalid': 'sign_in_invalid',
  'access.body_too_large': 'request_too_large',
  'access.bad_request': 'request_invalid',
  'access.json_only': 'request_refused',
  'access.control_denied': 'request_refused',
  'access.browser_not_allowed': 'request_refused',
};

/**
 * SQLSTATE class 28 (invalid authorization): the server refused the sign-in. The text never says whether the user
 * exists (08 section 4.2).
 */
const AUTH_STATES = /^28/;

export function problemText(t: TFunction, problem: Problem): string {
  if (problem.code.startsWith('db.')) {
    const kind = problem.code.slice(3);
    if (problem.sqlstate !== undefined && AUTH_STATES.test(problem.sqlstate)) {
      return t('problems.db.auth');
    }
    if (DB_KINDS.has(kind)) return t(`problems.db.${kind}`);
    return t('problems.db.other', {
      kind: /^[a-z_]{1,32}$/.test(kind) ? kind : 'unknown',
      interpolation: { escapeValue: false },
    });
  }
  if (problem.code.startsWith('access.')) {
    const access = ACCESS[problem.code];
    return access !== undefined
      ? t(`problems.${access}`, { code: problem.code, interpolation: { escapeValue: false } })
      : t('problems.access_refused', { code: problem.code, interpolation: { escapeValue: false } });
  }
  const name = problem.code.replace(/^studio\./, '');
  return PROBLEMS.has(name)
    ? t(`problems.${name}`)
    : t('problems.other', { code: problem.code, interpolation: { escapeValue: false } });
}

export function issueText(t: TFunction, issue: ProblemIssue): string {
  const name = issue.code.replace(/^config\./, '');
  return ISSUES.has(name) ? t(`issues.${name}`) : t('issues.other', { code: issue.code });
}

/** The issues of a refused form by field (`profile.host` -> `host`); the rest under `''`. */
export function issuesByField(problem: Problem | null): Record<string, ProblemIssue[]> {
  const result: Record<string, ProblemIssue[]> = {};
  for (const issue of problem?.issues ?? []) {
    const field = issue.pointer.split('.').slice(1).join('.');
    (result[field] ??= []).push(issue);
  }
  return result;
}
