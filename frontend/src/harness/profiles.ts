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
