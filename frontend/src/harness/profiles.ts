/** Profiles for the connection manager in the e2e harness and the jsdom accessibility test (STU-008). */
export const PROFILES = {
  profiles: [
    {
      id: 'project:parts-dev',
      source: 'project',
      name: 'parts-dev',
      engine: 'sqlite',
      environment: 'dev',
      path: 'data/parts.db',
      password: null,
      secret: { source: null, has_secret: null },
    },
    {
      id: 'project:parts-pg',
      source: 'project',
      name: 'parts-pg',
      engine: 'postgresql',
      environment: 'test',
      host: 'db.internal',
      port: 5432,
      database: 'parts',
      user: 'studio_ro',
      sslmode: 'verify-full',
      password: { source: 'env', target: 'PGPASSWORD' },
      secret: { source: 'env', has_secret: false },
    },
    {
      id: 'user:measure-central',
      source: 'user',
      name: 'measure-central',
      engine: 'postgresql',
      environment: 'prod',
      host: 'measure.example.internal',
      database: 'measurements',
      user: 'studio_ro',
      sslmode: 'verify-full',
      read_only_role: true,
      password: { source: 'keyring', target: '' },
      secret: { source: 'keyring', has_secret: null },
    },
  ],
  project_state: 'trusted',
  issues: { user: [] },
  user_file: { digest: 'b'.repeat(64), hand_written: false },
};

/** Answers of `POST /api/connection-tests` by engine (the facts of 03 section 2 A). */
export const TEST_RESULTS: Record<string, unknown> = {
  sqlite: {
    ok: true,
    engine: 'sqlite',
    environment: 'dev',
    server_version: '3.45.1',
    latency_ms: 0.8,
    read_only: true,
    facts: { foreign_keys: true, journal_mode: 'wal', encoding: 'UTF-8' },
  },
  postgresql: {
    ok: true,
    engine: 'postgresql',
    environment: 'prod',
    server_version: '17.6',
    latency_ms: 12.4,
    read_only: true,
    facts: {
      role: 'studio_ro',
      superuser: false,
      bypass_rls: false,
      encoding: 'UTF8',
      timezone: 'Europe/Vienna',
      tls: true,
    },
  },
};

export const SETTINGS = {
  settings: {
    language: 'system',
    theme: 'system',
    log_sql: false,
    telemetry: false,
    limits: { max_rows: 10000, statement_timeout_ms: 30000 },
  },
  issues: [],
  file: { digest: null, hand_written: false },
};
