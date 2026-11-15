/**
 * The shared write foundation (STU-XW-1, STU-ADR-0006). Every write area is off unless the owner of the Studio
 * process turned its flag on in `write-areas.toml`; the page only reads that state (`GET /api/write/areas`) to show
 * or hide controls, and never sets it. The server refuses every write route of an area that is off (405).
 */
import { getJson, send, type Result, type Saved } from './profiles';

export type WriteArea = 'data_editor' | 'schema_designer' | 'database_manager';
export type WriteKind = 'write' | 'admin';
export type WriteAreas = Record<WriteArea, boolean>;

export const ALL_OFF: WriteAreas = {
  data_editor: false,
  schema_designer: false,
  database_manager: false,
};

/** The kinds of profile each area opens: write profiles for the Data Editor and the Schema Designer. */
export function openKinds(areas: WriteAreas): WriteKind[] {
  const kinds: WriteKind[] = [];
  if (areas.data_editor || areas.schema_designer) kinds.push('write');
  if (areas.database_manager) kinds.push('admin');
  return kinds;
}

/** Parse defensively: anything but a literal `true` is off. */
export function parseWriteAreas(body: unknown): WriteAreas {
  const doc = (typeof body === 'object' && body !== null ? body : {}) as Record<string, unknown>;
  const areas = (typeof doc.areas === 'object' && doc.areas !== null ? doc.areas : {}) as Record<
    string,
    unknown
  >;
  return {
    data_editor: areas.data_editor === true,
    schema_designer: areas.schema_designer === true,
    database_manager: areas.database_manager === true,
  };
}

export async function fetchWriteAreas(fetcher: typeof fetch): Promise<WriteAreas> {
  return parseWriteAreas(await getJson(fetcher, '/api/write/areas'));
}

export interface WriteProfileFields {
  name: string;
  kind: WriteKind;
  read_profile: string;
  user?: string;
  /** The reference as the file writes it (`keyring:`, `env:NAME`, `file:PATH`). */
  password?: string | null;
}

/**
 * Create a write or admin profile. `confirmed` says the page showed the note on production credentials at this
 * moment and the user confirmed it (review N-6); the server creates none without it.
 */
export function createWriteProfile(
  fetcher: typeof fetch,
  profile: WriteProfileFields,
  digest: string | null,
  confirmed: boolean,
): Promise<Result<Saved>> {
  return send(fetcher, 'POST', '/api/write/profiles', {
    profile,
    digest,
    confirm_no_prod_credentials: confirmed,
  });
}

/** One record of the audit log (STU-XW-2): names, counts, codes and hashes only, never a value. */
export interface AuditRecord {
  seq: number;
  time: string;
  event: string;
  fields: Record<string, unknown>;
}

export interface AuditVerification {
  ok: boolean;
  records: number;
  problem: string | null;
  at: number | null;
}

/** `GET /api/write/audit`: whether records can be written, pending records (review N-4), verification, records. */
export interface AuditState {
  available: boolean;
  problem: string | null;
  pending: number;
  verification: AuditVerification | null;
  records: AuditRecord[];
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : 0;
}

/** Parse defensively: anything not understood counts as not available and not verified. */
export function parseAudit(body: unknown): AuditState | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  const raw = (
    typeof doc.verification === 'object' && doc.verification !== null ? doc.verification : null
  ) as Record<string, unknown> | null;
  const records = Array.isArray(doc.records) ? doc.records : [];
  return {
    available: doc.available === true,
    problem: text(doc.problem),
    pending: count(doc.pending),
    verification:
      raw === null
        ? null
        : {
            ok: raw.ok === true,
            records: count(raw.records),
            problem: text(raw.problem),
            at: typeof raw.at === 'number' ? raw.at : null,
          },
    records: records
      .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
      .map((item) => ({
        seq: count(item.seq),
        time: text(item.time) ?? '',
        event: text(item.event) ?? '',
        fields: (typeof item.fields === 'object' && item.fields !== null
          ? item.fields
          : {}) as Record<string, unknown>,
      })),
  };
}

export async function fetchAudit(fetcher: typeof fetch, limit = 20): Promise<AuditState | null> {
  return parseAudit(await getJson(fetcher, `/api/write/audit?limit=${limit}`));
}

/** A write or admin profile as `GET /api/write/profiles` lists it: never a secret, only where it comes from. */
export interface WriteProfileItem {
  id: string;
  name: string;
  kind: WriteKind;
  engine: string;
  /** The read profile it is coupled to (`user:<name>`): the Data Editor offers a connection's own write profiles. */
  readProfileId: string;
  /** STU-XW-4 (I-109 j): null for SQLite (no secret); false until the pair of server and reference is confirmed. */
  confirmed: boolean | null;
  foreign: boolean | null;
}

function flag(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

export function parseWriteProfiles(body: unknown): WriteProfileItem[] {
  if (typeof body !== 'object' || body === null) return [];
  const list = (body as Record<string, unknown>).write_profiles;
  if (!Array.isArray(list)) return [];
  return list
    .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
    .filter((item) => typeof item.id === 'string' && typeof item.name === 'string')
    .map((item) => {
      const secret = (
        typeof item.secret === 'object' && item.secret !== null ? item.secret : {}
      ) as Record<string, unknown>;
      return {
        id: item.id as string,
        name: item.name as string,
        kind: item.kind === 'admin' ? 'admin' : 'write',
        engine: text(item.engine) ?? '',
        readProfileId: text(item.read_profile_id) ?? '',
        confirmed: flag(secret.confirmed),
        foreign: flag(secret.foreign),
      };
    });
}

export async function fetchWriteProfiles(fetcher: typeof fetch): Promise<WriteProfileItem[]> {
  return parseWriteProfiles(await getJson(fetcher, '/api/write/profiles'));
}

/** The pair a write or admin secret belongs to (D-15): the server it goes to and the reference it comes from. */
export interface SecretPairPreview {
  confirmation_hash: string;
  confirm_text: string;
  confirmed: boolean;
  pair: {
    host: string;
    port: number;
    database: string | null;
    sslmode: string | null;
    role: string | null;
    reference: { source: string; target: string };
    foreign: boolean;
  };
}

/** `POST /api/write/secret-confirmations`: the server reads the pair from the user's file, never from the page. */
export function previewSecretPair(
  fetcher: typeof fetch,
  writeProfileId: string,
): Promise<Result<SecretPairPreview>> {
  return send(fetcher, 'POST', '/api/write/secret-confirmations', {
    write_profile_id: writeProfileId,
  });
}

/** Confirm the previewed pair with the host, typed by the user. */
export function confirmSecretPair(
  fetcher: typeof fetch,
  confirmationHash: string,
  typed: string,
): Promise<Result<{ confirmed: boolean }>> {
  return send(fetcher, 'POST', '/api/write/secret-confirmations/confirm', {
    confirmation_hash: confirmationHash,
    typed,
  });
}
