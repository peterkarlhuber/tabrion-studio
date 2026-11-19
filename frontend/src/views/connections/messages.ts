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
  'connection_environment',
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
  // The write foundation (STU-XW-1) and its audit log (STU-XW-2).
  'write_profile_invalid',
  'write_profile_unknown',
  'write_profile_exists',
  'write_warning_unconfirmed',
  'write_profiles_ignored',
  'profile_coupled',
  'write_prod_locked',
  'write_role_over_privileged',
  'write_coupling_invalid',
  'write_target_unknown',
  'unlock_name_mismatch',
  'write_locked',
  'write_target_changed',
  'write_unsupported',
  'audit_unavailable',
  'audit_record_invalid',
  // The plan framework (STU-XW-3).
  'plan_executor_unknown',
  'plan_invalid',
  'plan_target_invalid',
  'plan_target_partitioned',
  'plan_unknown',
  'plan_binding',
  'plan_expired',
  'plan_not_confirmed',
  'plan_confirmation_mismatch',
  'plan_changed',
  'plan_drift',
  // The Data Editor (STU-026).
  'plan_target_no_primary_key',
  'data_editor_conflict',
  'data_editor_rowcount',
  'data_editor_value_invalid',
  'data_editor_constraint',
  'data_editor_column_incomparable',
  // The I-109 j confirmation and require_auth (STU-XW-4).
  'secret_unconfirmed',
  'secret_confirmation_unknown',
  'secret_confirmation_stale',
  'secret_confirmation_mismatch',
  'secret_confirmation_not_needed',
  'secret_confirmation_unavailable',
  'write_tls_required',
  'require_auth_unavailable',
  // The Schema Designer's database creation (STU-CR-2, STU-CR-3).
  'schema_create_invalid',
  'schema_create_unconfirmed',
  'schema_create_engine',
  'schema_file_exists',
  'schema_folder_unsafe',
  'schema_folder_reserved',
  'schema_create_platform',
  'schema_database_exists',
  'schema_createdb_required',
  'schema_owner_missing',
  'schema_owner_not_allowed',
  // The Schema Designer's draft and plan preview (STU-CR-4).
  'schema_draft_missing',
  'schema_draft_stale',
  'schema_draft_schema',
  'schema_draft_limit',
  'schema_edit_invalid',
  'schema_plan_refused',
  'schema_model_invalid',
  // The Schema Designer's confirmed run (STU-CR-5).
  'schema_run_not_allowed',
  'schema_backup_required',
  'schema_backup_failed',
  'schema_run_check_failed',
  'schema_run_outcome_unknown',
  'schema_run_failed',
  // The Schema Designer's comparison (STU-XSD-2).
  'schema_compare_invalid',
  'schema_compare_schema',
  'schema_snapshot_invalid',
  'schema_snapshot_too_large',
  'schema_compare_mismatch',
  'schema_compare_plan_not_allowed',
  // The Database Manager's backup and restore (STU-XDM-2).
  'backup_tool_unavailable',
  'backup_tool_failed',
  'dbm_backup_failed',
  'dbm_backup_unknown',
  'dbm_backup_invalid',
  'dbm_restore_not_allowed',
  'dbm_restore_name_invalid',
  'dbm_restore_name_mismatch',
  'dbm_restore_role_unsafe',
  'dbm_restore_target_exists',
  'dbm_restore_target_changed',
  'dbm_restore_failed',
  // Deleting a database (STU-XDM-3).
  'dbm_delete_not_allowed',
  'dbm_delete_name_invalid',
  'dbm_delete_name_mismatch',
  'dbm_delete_target_missing',
  'dbm_delete_target_changed',
  'dbm_delete_in_use',
  'dbm_delete_backup_unverified',
  'dbm_delete_failed',
  // PostgreSQL role management (STU-XDM-4).
  'dbm_role_not_allowed',
  'dbm_role_requires_16',
  'dbm_role_invalid',
  'dbm_role_attribute_banned',
  'dbm_role_name_invalid',
  'dbm_role_name_mismatch',
  'dbm_role_changed',
  'dbm_role_unsafe',
  'dbm_role_missing',
  'dbm_role_exists',
  'dbm_role_object_missing',
  'dbm_role_denied',
  'dbm_role_failed',
  'admin_over_privileged',
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
  'host_list',
  'not_for_engine',
  'plain_password',
  'rename_unsupported',
  'unknown_key',
  'too_many',
  'encoding',
  'requirepeer_socket_only',
  'os_user_name',
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

/** The `create.*` codes of a refused database creation (STU-CR-2, STU-CR-3), each with its own text. */
const CREATE_ISSUES = new Set([
  'required',
  'type',
  'too_long',
  'control_character',
  'whitespace',
  'network_path',
  'not_absolute',
  'not_normalized',
  'no_file_name',
  'suffix',
  'choice',
  'format',
]);

/** The `edit.*` codes of a refused Schema Designer edit (STU-CR-4), each with its own text. */
const EDIT_ISSUES = new Set([
  'required',
  'type',
  'too_long',
  'control_character',
  'whitespace',
  'dot',
  'unknown_key',
  'choice',
  'table_missing',
  'table_exists',
  'column_missing',
  'column_exists',
  'column_in_use',
  'column_named_in_text',
  'table_referenced',
  'last_column',
  'no_change',
  'name_required',
  'name_not_supported',
  'name_exists',
  'primary_key_exists',
  'count_mismatch',
  'constraint_missing',
  'index_missing',
  'duplicate',
  'too_many',
]);

/** The `compare.*` codes of a refused schema comparison or plan preview (STU-XSD-2), each with its own text. */
const COMPARE_ISSUES = new Set([
  'required',
  'type',
  'choice',
  'unknown_key',
  'two_files',
  'format',
  'level',
  'too_large',
  'dialect',
  'plan_file',
  'plan_source',
  'plan_prod',
  'plan_environment',
]);

export function issueText(t: TFunction, issue: ProblemIssue): string {
  if (issue.code.startsWith('compare.')) {
    const code = issue.code.slice('compare.'.length);
    return COMPARE_ISSUES.has(code)
      ? t(`schemaCompare.issues.${code}`)
      : t('issues.other', { code: issue.code });
  }
  if (issue.code.startsWith('edit.')) {
    const code = issue.code.slice('edit.'.length);
    return EDIT_ISSUES.has(code)
      ? t(`schemaDesigner.issues.${code}`)
      : t('issues.other', { code: issue.code });
  }
  if (issue.code.startsWith('create.')) {
    const code = issue.code.slice('create.'.length);
    return CREATE_ISSUES.has(code)
      ? t(`createDatabase.issues.${code}`)
      : t('issues.other', { code: issue.code });
  }
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
