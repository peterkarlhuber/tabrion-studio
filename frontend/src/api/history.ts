/**
 * The query history and the favorite and recently opened profiles (STU-020; backend:
 * docs/architecture/history-and-scripts.md).
 *
 * - `GET /api/history?profile_id=&q=&limit=&before=`: the newest entries first. An entry holds the statement as it
 *   was written, with its `:name` placeholders and their names, never a parameter value (08 section 5).
 * - `DELETE /api/history` with `{}`, `{profile_id}` or `{id}`.
 * - `GET /api/favorites` and `PUT /api/favorites` with `{profile_id, favorite}`.
 *
 * The parsers fail closed: an answer without the lists the page needs is `studio.answer_invalid`; an entry with an
 * unexpected shape is dropped. Every text is shown escaped and capped by the views (`lib/visible.ts`).
 */
import { ANSWER_INVALID, read } from './explorer';
import { send, type Result } from './profiles';

export type HistoryState = 'done' | 'failed' | 'canceled';
const STATES: HistoryState[] = ['done', 'failed', 'canceled'];
const ENVIRONMENTS = ['dev', 'test', 'prod'] as const;
export type HistoryEnvironment = (typeof ENVIRONMENTS)[number];

export interface HistoryEntry {
  id: number;
  profileId: string;
  environment: HistoryEnvironment;
  engine: string;
  sql: string;
  sqlTruncated: boolean;
  paramNames: string[];
  state: HistoryState;
  rowCount: number | null;
  elapsedMs: number | null;
  code: string | null;
  /** ms since the epoch. */
  finishedAt: number;
}

export interface HistoryPage {
  enabled: boolean;
  retentionDays: number;
  entries: HistoryEntry[];
  nextBefore: number | null;
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function entry(value: unknown): HistoryEntry[] {
  if (typeof value !== 'object' || value === null) return [];
  const doc = value as Record<string, unknown>;
  const state = STATES.find((item) => item === doc.state);
  const environment = ENVIRONMENTS.find((item) => item === doc.environment);
  const finishedAt = finite(doc.finished_at);
  if (
    typeof doc.id !== 'number' ||
    !Number.isSafeInteger(doc.id) ||
    typeof doc.profile_id !== 'string' ||
    typeof doc.sql !== 'string' ||
    state === undefined ||
    environment === undefined ||
    finishedAt === null
  ) {
    return [];
  }
  return [
    {
      id: doc.id,
      profileId: doc.profile_id,
      environment,
      engine: typeof doc.engine === 'string' ? doc.engine : '',
      sql: doc.sql,
      sqlTruncated: doc.sql_truncated === true,
      paramNames: Array.isArray(doc.param_names)
        ? doc.param_names.filter((name): name is string => typeof name === 'string')
        : [],
      state,
      rowCount: finite(doc.row_count),
      elapsedMs: finite(doc.elapsed_ms),
      code: typeof doc.code === 'string' ? doc.code : null,
      finishedAt: finishedAt * 1000,
    },
  ];
}

export function parseHistory(body: unknown): HistoryPage | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  if (!Array.isArray(doc.entries) || typeof doc.enabled !== 'boolean') return null;
  return {
    enabled: doc.enabled,
    retentionDays: finite(doc.retention_days) ?? 30,
    entries: doc.entries.flatMap(entry),
    nextBefore: typeof doc.next_before === 'number' ? doc.next_before : null,
  };
}

export interface HistoryQuery {
  profileId?: string;
  q?: string;
  limit?: number;
  before?: number;
}

export function fetchHistory(
  fetcher: typeof fetch,
  query: HistoryQuery = {},
): Promise<Result<HistoryPage>> {
  const params = new URLSearchParams();
  if (query.profileId) params.set('profile_id', query.profileId);
  if (query.q) params.set('q', query.q);
  if (query.limit !== undefined) params.set('limit', String(query.limit));
  if (query.before !== undefined) params.set('before', String(query.before));
  const search = params.toString();
  return read(fetcher, `/api/history${search ? `?${search}` : ''}`, parseHistory);
}

/** Delete one entry (`id`), one profile's entries (`profileId`) or, with neither, every entry shown. */
export async function deleteHistory(
  fetcher: typeof fetch,
  which: { id?: number; profileId?: string } = {},
): Promise<Result<number>> {
  const body: Record<string, unknown> = {};
  if (which.id !== undefined) body.id = which.id;
  if (which.profileId !== undefined) body.profile_id = which.profileId;
  const result = await send<{ deleted?: unknown }>(fetcher, 'DELETE', '/api/history', body);
  if (!result.ok) return result;
  return { ok: true, value: typeof result.value?.deleted === 'number' ? result.value.deleted : 0 };
}

// --- favorites ----------------------------------------------------------------------------------------------------

export interface FavoritesState {
  /** Favorite profile ids, in the order they were added. */
  favorites: string[];
  /** Recently opened profile ids, the newest first, with when (ms since the epoch). */
  recent: { profileId: string; openedAt: number }[];
}

export function parseFavorites(body: unknown): FavoritesState | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  if (!Array.isArray(doc.favorites) || !Array.isArray(doc.recent)) return null;
  const favorites = doc.favorites.flatMap((item: unknown) => {
    const id = (item as { profile_id?: unknown } | null)?.profile_id;
    return typeof id === 'string' ? [id] : [];
  });
  const recent = doc.recent.flatMap((item: unknown) => {
    const value = (item ?? {}) as { profile_id?: unknown; opened_at?: unknown };
    const openedAt = finite(value.opened_at);
    return typeof value.profile_id === 'string' && openedAt !== null
      ? [{ profileId: value.profile_id, openedAt: openedAt * 1000 }]
      : [];
  });
  return { favorites, recent };
}

export function fetchFavorites(fetcher: typeof fetch): Promise<Result<FavoritesState>> {
  return read(fetcher, '/api/favorites', parseFavorites);
}

export async function setFavorite(
  fetcher: typeof fetch,
  profileId: string,
  favorite: boolean,
): Promise<Result<FavoritesState>> {
  const result = await send<unknown>(fetcher, 'PUT', '/api/favorites', {
    profile_id: profileId,
    favorite,
  });
  if (!result.ok) return result;
  const parsed = parseFavorites(result.value);
  return parsed === null ? { ok: false, problem: ANSWER_INVALID } : { ok: true, value: parsed };
}
