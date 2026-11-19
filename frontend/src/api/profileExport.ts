/**
 * The profile export (STU-CR-6 part 1; TX-30, GS3-22, W-10; STU-ADR-0007 section 12):
 * `POST /api/write/schema-designer/profile-export`. The route answers only while the `schema_designer` flag is on.
 *
 * The page sends the ids of read profiles (`user:<name>`, `project:<name>`), the new file's absolute path and its
 * typed name, never a profile, a secret or a mode: the server builds each exported profile from an allowlist of the
 * read profile's fields, read only, with an `env:` reference at most, and creates the file (`0600`) without ever
 * overwriting one.
 */
import type { Result } from './profiles';
import { send } from './profiles';

/** The ending of an export's file name (the server's rule). */
export const EXPORT_SUFFIX = '.tabrion-profile.toml';
/** The most profiles one export writes (the server's limit). */
export const MAX_EXPORT_PROFILES = 64;

export interface ExportedFile {
  path: string;
  fileName: string;
  profiles: number;
  auditPending: boolean;
}

const UNREADABLE: Result<never> = {
  ok: false,
  problem: { code: 'studio.answer_invalid', kind: 'internal', status: 0, logId: null },
};

function parseExported(value: unknown): ExportedFile | null {
  if (typeof value !== 'object' || value === null) return null;
  const doc = value as Record<string, unknown>;
  if (
    typeof doc.path !== 'string' ||
    typeof doc.file_name !== 'string' ||
    typeof doc.profiles !== 'number' ||
    typeof doc.audit_pending !== 'boolean'
  ) {
    return null;
  }
  return {
    path: doc.path,
    fileName: doc.file_name,
    profiles: doc.profiles,
    auditPending: doc.audit_pending,
  };
}

/** `POST /api/write/schema-designer/profile-export`: write the named read profiles to a new file. */
export async function exportProfiles(
  fetcher: typeof fetch,
  profiles: string[],
  path: string,
  typed: string,
): Promise<Result<ExportedFile>> {
  const result = await send<unknown>(fetcher, 'POST', '/api/write/schema-designer/profile-export', {
    profiles,
    path,
    typed,
  });
  if (!result.ok) return result;
  const exported = parseExported(result.value);
  return exported === null ? UNREADABLE : { ok: true, value: exported };
}
