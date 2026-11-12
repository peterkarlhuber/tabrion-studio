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
  'answer_invalid',
  // The diagnosis report (STU-025, TD-036).
  'report_invalid',
  'report_unavailable',
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
