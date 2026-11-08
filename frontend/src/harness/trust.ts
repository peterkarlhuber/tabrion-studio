/**
 * Reviews for D-01 used by the unit tests, the axe tests and the e2e harness (STU-006, review U-1).
 * The hostile one carries what the binding requirement U-1 names: commas, a newline, a tab, a bidi override
 * (U+202E), an overlong value and more entries than the dialog shows.
 */
import type { TrustDiff, TrustReview } from '../api/project';

export const REVIEW: TrustReview = {
  connections: [
    { name: 'parts-dev', engine: 'sqlite', environment: 'dev', location: 'data/parts.db' },
    { name: 'parts-pg', engine: 'postgresql', environment: 'test', location: 'db.internal:5432' },
    {
      name: 'measure-central',
      engine: 'postgresql',
      environment: 'prod',
      location: 'db.internal:5432',
    },
  ],
  hosts: ['db.internal:5432'],
  files: [
    { path: 'data/parts.db', kind: 'database', outside: false },
    { path: './secrets/db', kind: 'secret', outside: false },
  ],
  env: ['PGPASSWORD'],
  unknown_keys: [],
};

const RLO = String.fromCodePoint(0x202e);
export const OVERLONG = `measure-${'x'.repeat(300)}`;
export const HOSTILE_NAMES = [
  'parts-dev, safe (checked)',
  `evil${RLO}txt.exe`,
  'line\nbreak',
  OVERLONG,
  ...Array.from({ length: 16 }, (_, i) => `filler-${i + 1}`),
];

export const HOSTILE_REVIEW: TrustReview = {
  connections: HOSTILE_NAMES.map((name) => ({
    name,
    engine: 'postgresql',
    environment: 'dev',
    location: 'h:5432',
  })),
  hosts: ['db.internal:5432, localhost:5432', `attacker${RLO}lacol:5432`],
  files: [
    { path: 'data/parts.db\tsafe', kind: 'database', outside: false },
    { path: '../../.ssh/id_rsa', kind: 'secret', outside: true },
  ],
  env: ['AWS_SECRET_ACCESS_KEY'],
  unknown_keys: ['profile[0].extra'],
};

export const DIFF: TrustDiff = {
  connections: { added: ['parts-dev2'], removed: ['parts-dev'], changed: ['measure-central'] },
  hosts: { added: ['attacker.example:5432'], removed: ['db.internal:5432'] },
  files: { added: [], removed: [] },
  env: { added: ['AWS_SECRET_ACCESS_KEY'], removed: [] },
  unknown_keys: { added: [], removed: [] },
};

/** An invalid project file as GET /api/project reports it: key paths and codes only (review I-2). */
export const INVALID_PROJECT = {
  path: '/work/parts',
  profiles_file: '.tabrion/studio.toml',
  state: 'invalid',
  trusted: false,
  digest: null,
  review: null,
  diff: null,
  issues: [
    { pointer: 'profile[0].path', code: 'config.network_path', severity: 'error' },
    { pointer: 'profile[1].password', code: 'config.plain_password', severity: 'error' },
  ],
};
