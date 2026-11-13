/** The code trust and migration status the e2e harness answers in its `migrations` scenario (STU-015). */
export const CODE_DIGEST = 'a'.repeat(64);

export function codeTrust(trusted: boolean) {
  return {
    state: trusted ? 'trusted' : 'untrusted',
    trusted,
    format: 'alembic',
    config_file: 'alembic.ini',
    digest: CODE_DIGEST,
    script_location: 'migrations',
    revision_files: ['0001_parts.py', '0002_measurements.py', '0003_indexes.py'],
    import_roots: ['.'],
    module_files: ['shop_models.py'],
    issue: null,
    notice: '',
  };
}

/** `knownOnly`: no revision the project does not know, so the page offers the upgrade command (TD-046 B-3). */
export function migrationStatus(trusted: boolean, knownOnly = false) {
  const alembic = trusted
    ? {
        current: ['0002'],
        heads: ['0003'],
        pending: ['0003'],
        is_current: false,
        unknown: knownOnly ? [] : ['ghost-1'],
        revisions: 'read',
      }
    : {
        current: ['0002'],
        heads: null,
        pending: null,
        is_current: null,
        unknown: null,
        revisions: 'unknown (project not trusted)',
      };
  return {
    connection_id: 'c1',
    engine: 'sqlite',
    formats: [
      { format: 'sqlite_user_version', current: 3 },
      {
        format: 'alembic',
        code_trust: trusted ? 'trusted' : 'untrusted',
        code_issue: null,
        script_location: 'migrations',
        problem: null,
        ...alembic,
      },
      // A Levuvo version table (STU-024) and a format the page does not show yet (review C-1 of #32).
      {
        format: 'nodestro',
        schema: 'public',
        table: 'nodestro_schema_migrations',
        current: 1,
        state: 'pending',
        steps: [
          { version: 1, name: '0001_init.sql', status: 'applied' },
          { version: 2, name: '0002_parts.sql', status: 'pending' },
        ],
        problem: null,
      },
      { format: 'later' },
    ],
  };
}
