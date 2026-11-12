/**
 * The diagnosis the e2e harness answers in its `diagnostics` scenario (STU-014): one finding of each severity, from
 * tabrion and from Studio, with a schema name that carries a bidi override (escaped on the page, U-1).
 */
export const HOSTILE_SCHEMA = `audit${String.fromCodePoint(0x202e)}gol`;

export function diagnosis(canceled = false) {
  const findings = [
    {
      code: 'STU-DX-006',
      severity: 'warning',
      title: 'Role owns tables',
      detail:
        'The role owns, or can become the owner of, 3 table(s) or view(s) in the database; an owner can change ' +
        'them and is not subject to row-level security unless FORCE ROW LEVEL SECURITY is set.',
      remedy: 'Use an owner role for migrations and a separate runtime role for the application.',
      source: 'studio',
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
          'The profile says read_only_role, but the role, or a role it can SET ROLE to, may write in 2 table(s), ' +
          "view(s) or sequence(s) (INSERT, UPDATE, DELETE, TRUNCATE, a column grant or ownership). Studio's " +
          'session is read-only; the role is not.',
        remedy:
          'Connect with a role that has only SELECT (and USAGE on the schemas), and no membership in a role that ' +
          'can write.',
        source: 'studio',
      },
      ...findings,
    ],
    counts: { error: 1, warning: 2, info: canceled ? 2 : 1 },
  };
}
