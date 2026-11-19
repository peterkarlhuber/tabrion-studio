/**
 * The Database Manager's profile export and import (STU-XDM-5; STU-ADR-0008 section 8; TX-45, GS4-21). The routes
 * answer only while the owner turned the `database_manager` flag on; the page never sets it.
 *
 * - Export: `POST /api/write/database-manager/profiles/export/preview` with the read profiles' ids and the new file's
 *   path (writes nothing), then `.../profiles/export` with the typed file name and the preview's `state`. The server
 *   writes each profile from an allowlist, read only, with an `env:` reference at most; the write and admin
 *   profiles coupled to them are named in the preview and never written.
 * - Import: `POST .../profiles/import/preview` with a file's path (changes nothing), then `.../profiles/import` with
 *   the chosen profiles (each under its own or a new name), the typed file name, the preview's `state` and the user
 *   file's digest. Imported profiles are read profiles only, never over an existing profile, and carry no secret and
 *   no reference of the file (every password is asked for when used).
 *
 * The page sends ids, paths, names and what the user typed; never a profile's fields, a secret, a mode or a coupling.
 */
import type { EnvironmentTag } from './profiles';
import { send, type Result } from './profiles';

/** The ending of a profile file's name (the server's rule). */
export const PROFILE_FILE_SUFFIX = '.tabrion-profile.toml';

const UNREADABLE: Result<never> = {
  ok: false,
  problem: { code: 'studio.answer_invalid', kind: 'internal', status: 0, logId: null },
};

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function environment(value: unknown): EnvironmentTag | null {
  return value === 'dev' || value === 'test' || value === 'prod' ? value : null;
}

// --- export ----------------------------------------------------------------------------------------------------------

export interface ExportPreviewProfile {
  id: string;
  name: string;
  source: string;
  engine: string;
  environment: EnvironmentTag | null;
  /** What the file says about the password: `env` (the variable's name), `prompt`, or null (none). */
  password: 'env' | 'prompt' | null;
}

export interface CoupledProfile {
  name: string;
  kind: 'write' | 'admin';
  readProfile: string;
}

export interface ExportPreview {
  path: string;
  fileName: string;
  profiles: ExportPreviewProfile[];
  /** The write and admin profiles coupled to the chosen read profiles: never written. */
  leftOut: CoupledProfile[];
  state: string;
}

export function parseExportPreview(body: unknown): ExportPreview | null {
  const doc = record(body);
  if (doc === null || !Array.isArray(doc.profiles) || !Array.isArray(doc.left_out)) return null;
  const path = text(doc.path);
  const fileName = text(doc.file_name);
  const state = text(doc.state);
  if (path === null || fileName === null || state === null) return null;
  const profiles: ExportPreviewProfile[] = [];
  for (const raw of doc.profiles) {
    const item = record(raw);
    const id = text(item?.id);
    const name = text(item?.name);
    if (item === null || id === null || name === null) return null;
    profiles.push({
      id,
      name,
      source: text(item.source) ?? 'user',
      engine: text(item.engine) ?? '',
      environment: environment(item.environment),
      password: item.password === 'env' || item.password === 'prompt' ? item.password : null,
    });
  }
  const leftOut: CoupledProfile[] = [];
  for (const raw of doc.left_out) {
    const item = record(raw);
    const name = text(item?.name);
    const readProfile = text(item?.read_profile);
    if (item === null || name === null || readProfile === null) return null;
    leftOut.push({ name, kind: item.kind === 'admin' ? 'admin' : 'write', readProfile });
  }
  return { path, fileName, profiles, leftOut, state };
}

export interface ExportResult {
  fileName: string;
  profiles: number;
  auditPending: boolean;
}

export async function previewProfileExport(
  fetcher: typeof fetch,
  profiles: string[],
  path: string,
): Promise<Result<ExportPreview>> {
  const result = await send<unknown>(
    fetcher,
    'POST',
    '/api/write/database-manager/profiles/export/preview',
    { profiles, path },
  );
  if (!result.ok) return result;
  const preview = parseExportPreview(result.value);
  return preview === null ? UNREADABLE : { ok: true, value: preview };
}

export async function runProfileExport(
  fetcher: typeof fetch,
  profiles: string[],
  path: string,
  typed: string,
  state: string,
): Promise<Result<ExportResult>> {
  const result = await send<unknown>(
    fetcher,
    'POST',
    '/api/write/database-manager/profiles/export',
    {
      profiles,
      path,
      typed,
      state,
    },
  );
  if (!result.ok) return result;
  const doc = record(result.value);
  const fileName = text(doc?.file_name);
  if (doc === null || fileName === null || typeof doc.profiles !== 'number') return UNREADABLE;
  return {
    ok: true,
    value: { fileName, profiles: doc.profiles, auditPending: doc.audit_pending === true },
  };
}

// --- import ----------------------------------------------------------------------------------------------------------

export interface ImportRefusal {
  field: string;
  code: string;
}

export interface ImportPreviewProfile {
  name: string;
  engine: string;
  environment: EnvironmentTag | null;
  path: string | null;
  host: string | null;
  port: number | null;
  database: string | null;
  user: string | null;
  mode: string;
  /**
   * Where the password will come from: `keyring`, Studio's own entry, empty until entered; null: none. No reference
   * of the file is ever kept, `env:` included (review M-1 of #155).
   */
  password: { source: 'keyring' } | null;
  importable: boolean;
  refusals: ImportRefusal[];
  /** Keys of the file that Studio's profile does not have (never imported). */
  notImported: string[];
  nameTaken: boolean;
}

export interface ImportPreview {
  path: string;
  fileName: string;
  state: string;
  profiles: ImportPreviewProfile[];
  userFile: { digest: string | null; handWritten: boolean };
}

function importProfile(raw: unknown): ImportPreviewProfile | null {
  const item = record(raw);
  const name = text(item?.name);
  if (item === null || name === null) return null;
  const password = record(item.password);
  return {
    name,
    engine: text(item.engine) ?? '',
    environment: environment(item.environment),
    path: text(item.path),
    host: text(item.host),
    port: typeof item.port === 'number' ? item.port : null,
    database: text(item.database),
    user: text(item.user),
    mode: text(item.mode) ?? '',
    // Anything but a keyring entry is not what the server sends: shown as asked for, never as a reference.
    password: password === null ? null : { source: 'keyring' },
    importable: item.importable === true,
    refusals: (Array.isArray(item.refusals) ? item.refusals : [])
      .map(record)
      .filter((entry): entry is Record<string, unknown> => entry !== null)
      .map((entry) => ({ field: text(entry.field) ?? '', code: text(entry.code) ?? '' })),
    notImported: (Array.isArray(item.not_imported) ? item.not_imported : []).filter(
      (key): key is string => typeof key === 'string',
    ),
    nameTaken: item.name_taken === true,
  };
}

export function parseImportPreview(body: unknown): ImportPreview | null {
  const doc = record(body);
  if (doc === null || !Array.isArray(doc.profiles)) return null;
  const path = text(doc.path);
  const fileName = text(doc.file_name);
  const state = text(doc.state);
  const userFile = record(doc.user_file);
  if (path === null || fileName === null || state === null || userFile === null) return null;
  const profiles = doc.profiles.map(importProfile);
  if (profiles.some((item) => item === null)) return null;
  return {
    path,
    fileName,
    state,
    profiles: profiles as ImportPreviewProfile[],
    userFile: { digest: text(userFile.digest), handWritten: userFile.hand_written === true },
  };
}

export interface ImportChoice {
  name: string;
  as: string;
}

export interface ImportResult {
  imported: string[];
  fileName: string;
  auditPending: boolean;
}

export async function previewProfileImport(
  fetcher: typeof fetch,
  path: string,
): Promise<Result<ImportPreview>> {
  const result = await send<unknown>(
    fetcher,
    'POST',
    '/api/write/database-manager/profiles/import/preview',
    { path },
  );
  if (!result.ok) return result;
  const preview = parseImportPreview(result.value);
  return preview === null ? UNREADABLE : { ok: true, value: preview };
}

export async function runProfileImport(
  fetcher: typeof fetch,
  preview: ImportPreview,
  profiles: ImportChoice[],
  typed: string,
  overwrite: boolean,
): Promise<Result<ImportResult>> {
  const result = await send<unknown>(
    fetcher,
    'POST',
    '/api/write/database-manager/profiles/import',
    {
      path: preview.path,
      typed,
      state: preview.state,
      profiles,
      digest: preview.userFile.digest,
      overwrite,
    },
  );
  if (!result.ok) return result;
  const doc = record(result.value);
  const fileName = text(doc?.file_name);
  if (doc === null || fileName === null || !Array.isArray(doc.imported)) return UNREADABLE;
  return {
    ok: true,
    value: {
      imported: doc.imported.filter((name): name is string => typeof name === 'string'),
      fileName,
      auditPending: doc.audit_pending === true,
    },
  };
}
