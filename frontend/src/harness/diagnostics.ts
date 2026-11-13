/**
 * The diagnosis the e2e harness answers in its `diagnostics` scenario (STU-014): one finding of each severity, from
 * tabrion and from Studio, with a schema name that carries a bidi override (escaped on the page, U-1), and two backup
 * hints of STU-025 (TD-036). Studio's own entries carry `params` like the server's (TD-032), so the page words them
 * in en/de; tabrion's text stays English. `report` is the Markdown report the scenario answers for a download.
 */
export const HOSTILE_SCHEMA = `audit${String.fromCodePoint(0x202e)}gol`;

interface HarnessFinding {
  code: string;
  severity: string;
  title: string;
  detail: string;
  remedy: string;
  source: string;
  params?: Record<string, unknown>;
}

export function diagnosis(canceled = false) {
  const findings: HarnessFinding[] = [
    {
      code: 'STU-DX-006',
      severity: 'warning',
      title: 'Role owns tables',
      detail:
        'The role owns, or can become the owner of, 3 tables or views in the database; an owner can change ' +
        'them and is not subject to row-level security unless FORCE ROW LEVEL SECURITY is set.',
      remedy: 'Use an owner role for migrations and a separate runtime role for the application.',
      source: 'studio',
      params: { owned: 3, prod: false },
    },
    {
      code: 'TAB-PG-002',
      severity: 'warning',
      title: 'Schema is writable by PUBLIC',
      detail: `Schema ${HOSTILE_SCHEMA} grants CREATE to PUBLIC.`,
      remedy: `REVOKE CREATE ON SCHEMA ${HOSTILE_SCHEMA} FROM PUBLIC.`,
      source: 'tabrion',
    },
    {
      code: 'STU-DX-010',
      severity: 'info',
      title: 'Session settings missing for the role or database',
      detail:
        'Neither the role nor the database sets lock_timeout, application_name; an application that sets none ' +
        'runs without them (unless postgresql.conf sets them, which this role cannot read).',
      remedy:
        "ALTER ROLE ... SET (or ALTER DATABASE), or set them in the application (tabrion's SessionSettings does).",
      source: 'studio',
      params: { missing: ['lock_timeout', 'application_name'] },
    },
  ];
  if (canceled) {
    findings.push({
      code: 'STU-DX-008',
      severity: 'info',
      title: 'Not every check ran',
      detail: 'The diagnosis stopped because you canceled it. Not run: STU-DX-004.',
      remedy:
        'Run the diagnosis again; for a large SQLite file, give the profile a longer statement timeout.',
      source: 'studio',
      params: { reason: 'canceled', time_limit_s: 30, skipped: ['STU-DX-004'] },
    });
  }
  return {
    connection_id: 'c1',
    engine: 'postgresql',
    environment: 'dev',
    ran_at: 1_794_500_000,
    findings: [
      {
        code: 'STU-DX-003',
        severity: 'error',
        title: 'The read-only role can write',
        detail:
          'The profile says read_only_role, but the role, or a role it can SET ROLE to, may write in 2 tables, ' +
          "views or sequences (INSERT, UPDATE, DELETE, TRUNCATE, a column grant or ownership). Studio's " +
          'session is read-only; the role is not.',
        remedy:
          'Connect with a role that has only SELECT (and USAGE on the schemas), no CREATE on any schema, no TEMP ' +
          'on the database, and no membership in a role that can write.',
        source: 'studio',
        params: { writable: 2, creatable: 0, temporary: false },
      },
      ...findings,
    ],
    counts: { error: 1, warning: 2, info: canceled ? 2 : 1 },
    backup: {
      hints: [
        {
          code: 'STU-BK-010',
          severity: 'warning',
          title: 'WAL archiving is off',
          detail:
            'archive_mode is off: the server keeps no WAL archive, so the database cannot be restored to a point ' +
            'in time. Studio cannot see backups made with pg_dump or with a tool that does not archive WAL.',
          remedy:
            'Make sure the database is backed up (pg_dump, pg_basebackup or a backup tool); for point-in-time ' +
            'recovery set archive_mode and an archive_command or archive_library.',
          source: 'studio',
          params: {},
        },
        {
          code: 'STU-BK-012',
          severity: 'info',
          title: 'Nothing archived yet',
          detail:
            'archive_mode is on, but the archiver has not archived a WAL file since its statistics were reset.',
          remedy:
            'Check that archive_command or archive_library is set; pg_switch_wal() forces a first file.',
          source: 'studio',
          params: {},
        },
      ],
      facts: { archive_mode: 'off' },
    },
  };
}

/** The report file name of the scenario (the server's form, so the page keeps it). */
export const REPORT_FILENAME = 'tabrion-studio-diagnosis-postgresql-dev-20261112T101500Z.md';

export const REPORT_MARKDOWN =
  '# tabrion Studio diagnosis report\n\n- Findings: 1 error, 2 warnings, 1 note\n';
