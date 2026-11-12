/**
 * The values of `:name` placeholders (03 section 2 C: typed text, number, boolean, null, JSON). They live only in the
 * page: never in the drafts, the log or a history (08 section 5).
 *
 * The server takes text, a number, `true`, `false` or `null` (docs/architecture/queries.md). JSON goes as text; the
 * statement casts it (`:doc::jsonb`), so the panel checks that it parses and sends it unchanged.
 */
import type { ParamValue } from '../../api/queries';

export type ParamType = 'text' | 'number' | 'boolean' | 'null' | 'json';

export const PARAM_TYPES: readonly ParamType[] = ['text', 'number', 'boolean', 'null', 'json'];

export interface ParamInput {
  type: ParamType;
  /** The text the user typed; for `boolean` "true" or "false". */
  value: string;
}

export type ParamProblem = 'missing' | 'number' | 'range' | 'json';

/** The value to send, or why there is none. A placeholder the user has not filled in has no entry: `missing`. */
export function paramValue(
  input: ParamInput | undefined,
): { ok: true; value: ParamValue } | { ok: false; problem: ParamProblem } {
  if (input === undefined) return { ok: false, problem: 'missing' };
  switch (input.type) {
    case 'null':
      return { ok: true, value: null };
    case 'boolean':
      return { ok: true, value: input.value === 'true' };
    case 'number': {
      const text = input.value.trim();
      // JSON's number syntax: what the server accepts as a number, nothing else (no hex, no "Infinity").
      if (!/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(text)) {
        return { ok: false, problem: text === '' ? 'missing' : 'number' };
      }
      const value = Number(text);
      if (!Number.isFinite(value)) return { ok: false, problem: 'number' };
      // Integers beyond 2^53 lose digits as a JavaScript number: the panel asks for text with a cast instead.
      if (Number.isInteger(value) && !Number.isSafeInteger(value))
        return { ok: false, problem: 'range' };
      return { ok: true, value };
    }
    case 'json':
      try {
        JSON.parse(input.value);
      } catch {
        return { ok: false, problem: input.value.trim() === '' ? 'missing' : 'json' };
      }
      return { ok: true, value: input.value };
    default:
      return { ok: true, value: input.value };
  }
}
