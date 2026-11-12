/**
 * Export (STU-021, 03 sections 2 D and 6 `POST /exports`; the page part is TD-043). `POST /api/exports` with
 * `{connection_id, format: "csv" | "json", sql + params | table: {schema, name}, max_rows, escape_formulas}` answers the
 * file as an attachment, streamed (`docs/architecture/exports.md`). The export only reads: the server checks the
 * statement like a query and runs it read-only, at most `max_rows` rows (1 to 1 000 000) and 10 minutes.
 *
 * The page reads the stream chunk by chunk (it counts the bytes for the progress), so an `AbortSignal` stops it at
 * once, and the server closes the export when the request goes away. It keeps the file only when the media type is the
 * one asked for, and saves it under the server's `Content-Disposition` name only when that name has the server's form
 * (`exportFilename`); otherwise under a fixed one. A JSON file that does not end with the server's closing
 * `"truncated"` member is an export that ended early, and is not saved.
 *
 * The source is a statement of the SQL editor today; the data viewer (STU-013) passes a table (`ExportSource`).
 */
import { apiHeaders } from '../session';
import { OFFLINE, readProblem, type Problem } from './problem';
import type { Result } from './profiles';
import type { ParamValue } from './queries';

export type ExportFormat = 'csv' | 'json';

/** What to export: a reading statement with its parameters, or a whole table or view of the connection's catalog. */
export type ExportSource =
  | { kind: 'sql'; sql: string; params: Record<string, ParamValue> }
  | { kind: 'table'; schema: string; name: string };

export interface ExportRequest {
  connectionId: string;
  format: ExportFormat;
  source: ExportSource;
  /** 1 to `MAX_EXPORT_ROWS`. */
  maxRows: number;
  /** CSV only: a text a spreadsheet would read as a formula gets a leading `'` (the server's default). */
  escapeFormulas: boolean;
}

export interface ExportFile {
  filename: string;
  blob: Blob;
  bytes: number;
  /** JSON only: the server cut the export at `max_rows` (`"truncated": "rows"`); null when the format cannot tell. */
  truncated: boolean | null;
}

/** The server's row limit of an export (`MAX_EXPORT_ROWS`). */
export const MAX_EXPORT_ROWS = 1_000_000;

/** The answer the page cannot use (another media type, a JSON file without its end). */
export const EXPORT_INCOMPLETE: Problem = {
  code: 'studio.export_incomplete',
  kind: 'internal',
  status: 0,
  logId: null,
};

/** The user stopped the export (an abort, not a server answer). */
export const EXPORT_CANCELED: Problem = {
  code: 'studio.export_canceled',
  kind: 'internal',
  status: 0,
  logId: null,
};

const TYPES: Record<ExportFormat, string> = { csv: 'text/csv', json: 'application/json' };
/** The server's names: `export.<format>` for a statement, the table's name in `[A-Za-z0-9_.-]` (up to 100). */
const NAME = /^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}$/;
/** The last bytes of a complete JSON export (`services/exports.py`, `tail`). */
const JSON_END = /\],"truncated":(null|"rows")\}\n?$/;

/** The name to save an export under: the server's when it has the expected form, else `export.<format>`. */
export function exportFilename(disposition: string | null, format: ExportFormat): string {
  const suffix = `.${format}`;
  const name =
    /^attachment;\s*filename="([^"]{1,200})"$/.exec(disposition?.trim() ?? '')?.[1] ?? '';
  const stem = name.endsWith(suffix) ? name.slice(0, -suffix.length) : '';
  return NAME.test(stem) && !stem.includes('..') ? name : `export${suffix}`;
}

function body(request: ExportRequest): Record<string, unknown> {
  const doc: Record<string, unknown> = {
    connection_id: request.connectionId,
    format: request.format,
    max_rows: request.maxRows,
  };
  if (request.format === 'csv') doc.escape_formulas = request.escapeFormulas;
  if (request.source.kind === 'sql') {
    doc.sql = request.source.sql;
    doc.params = request.source.params;
  } else {
    doc.table = { schema: request.source.schema, name: request.source.name };
  }
  return doc;
}

/**
 * Run the export and read the whole file. `onProgress` gets the bytes received so far. An abort through `signal`
 * answers `EXPORT_CANCELED`; nothing of a stopped, refused or broken export is returned.
 */
export async function downloadExport(
  fetcher: typeof fetch,
  request: ExportRequest,
  options: { signal?: AbortSignal; onProgress?: (bytes: number) => void } = {},
): Promise<Result<ExportFile>> {
  const { signal, onProgress } = options;
  try {
    const response = await fetcher('/api/exports', {
      method: 'POST',
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { ...apiHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body(request)),
      signal,
    });
    if (!response.ok) return { ok: false, problem: await readProblem(response) };
    const type = (response.headers.get('Content-Type') ?? '').split(';')[0]?.trim().toLowerCase();
    if (type !== TYPES[request.format]) {
      await response.body?.cancel().catch(() => undefined);
      return { ok: false, problem: EXPORT_INCOMPLETE };
    }
    const parts: Uint8Array[] = [];
    let bytes = 0;
    if (response.body) {
      const reader = response.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (signal?.aborted) {
          await reader.cancel().catch(() => undefined);
          return { ok: false, problem: EXPORT_CANCELED };
        }
        parts.push(value);
        bytes += value.byteLength;
        onProgress?.(bytes);
      }
    } else {
      const whole = new Uint8Array(await response.arrayBuffer());
      parts.push(whole);
      bytes = whole.byteLength;
      onProgress?.(bytes);
    }
    let truncated: boolean | null = null;
    if (request.format === 'json') {
      const tail = new TextDecoder().decode(lastBytes(parts, 64));
      const end = JSON_END.exec(tail);
      if (end === null) return { ok: false, problem: EXPORT_INCOMPLETE };
      truncated = end[1] === '"rows"';
    }
    return {
      ok: true,
      value: {
        filename: exportFilename(response.headers.get('Content-Disposition'), request.format),
        blob: new Blob(parts as BlobPart[], { type: TYPES[request.format] }),
        bytes,
        truncated,
      },
    };
  } catch {
    return { ok: false, problem: signal?.aborted ? EXPORT_CANCELED : OFFLINE };
  }
}

/** The last `count` bytes of `parts`. */
function lastBytes(parts: Uint8Array[], count: number): Uint8Array {
  const out: number[] = [];
  for (let index = parts.length - 1; index >= 0 && out.length < count; index -= 1) {
    const part = parts[index] as Uint8Array;
    for (let at = part.length - 1; at >= 0 && out.length < count; at -= 1) {
      out.push(part[at] as number);
    }
  }
  return Uint8Array.from(out.reverse());
}
