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
  'profile_invalid',
  'settings_invalid',
  'profile_exists',
  'profile_read_only',
  'profile_not_found',
  'keyring_unavailable',
  'keyring_failed',
  'secret_invalid',
  'secret_not_storable',
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
]);

export function problemText(t: TFunction, problem: Problem): string {
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
