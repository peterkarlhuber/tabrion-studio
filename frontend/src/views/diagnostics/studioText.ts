/**
 * The en/de details and remedies of Studio's own findings and backup hints (`STU-DX-*`, `STU-BK-*`; TD-032).
 *
 * The server sends each of them with `params`, its facts (counts, setting names, the `sslmode`, the checks that did
 * not run), and the page words them from `diagnostics.texts` of its i18n files. The English templates there are the
 * server's (`services/diagnosis_texts.py`; `tests/unit/test_diagnostics_page.py` compares them), and the cases of
 * `studioTexts.cases.json` are worded alike on both sides (`studioText.test.ts`).
 *
 * Fail closed: `params` that are not what this code expects (a missing count, an unknown word, a string where a
 * number belongs) give `null`, and the page then shows the server's English text, marked `lang="en"` like tabrion's.
 * Every value that reaches a text is a count, a boolean or one of a fixed list of words, so nothing the server sends
 * is shown as it came.
 */
import type { TFunction } from 'i18next';

import type { Finding } from '../../api/diagnostics';

export interface StudioText {
  detail: string;
  remedy: string;
}

/** Studio counts at most this many objects or rows; a count that reaches it is shown as "1000+". */
export const CAPPED_COUNT = 1000;
const SSL_MODES = ['disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'];
const STEPS = ['tabrion', 'tabrion_integrity', 'backup'];
const STEP_CODE = /^STU-DX-\d{3}$/;
const SETTINGS = ['lock_timeout', 'idle_in_transaction_session_timeout', 'application_name'];
const UNITS = ['minutes', 'hours', 'days'];
/** More skipped checks or missing settings than any server sends: such an answer is not worded. */
const MAX_LIST = 20;

type Params = Record<string, unknown>;

function isCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function countText(count: number): string {
  return count >= CAPPED_COUNT ? `${CAPPED_COUNT}+` : String(count);
}

function oneOf(value: unknown, words: readonly string[]): value is string {
  return typeof value === 'string' && words.includes(value);
}

function words(value: unknown, allowed: (word: string) => boolean): string[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_LIST) return null;
  return value.every((item) => typeof item === 'string' && allowed(item))
    ? (value as string[])
    : null;
}

/** The wording of `code` with `params`, or null when they do not fit. */
function word(t: TFunction, code: string, params: Params): StudioText | null {
  const key = (name: string) => `diagnostics.texts.${code}.${name}`;
  // `count` picks the plural form of the detail only; no remedy has one.
  const plain = (detail = 'detail', remedy = 'remedy', values: Record<string, unknown> = {}) => {
    const { count: _count, ...rest } = values;
    return { detail: t(key(detail), values), remedy: t(key(remedy), rest) };
  };
  switch (code) {
    case 'STU-DX-001': {
      const { sslmode } = params;
      if (sslmode === null || sslmode === undefined || sslmode === '') return plain('detail_unset');
      return oneOf(sslmode, SSL_MODES) ? plain('detail', 'remedy', { sslmode }) : null;
    }
    case 'STU-DX-002':
      return oneOf(params.sslmode, SSL_MODES)
        ? plain('detail', 'remedy', { sslmode: params.sslmode })
        : null;
    case 'STU-DX-003': {
      const { writable, creatable, temporary } = params;
      if (!isCount(writable) || !isCount(creatable) || typeof temporary !== 'boolean') return null;
      if (writable === 0 && creatable === 0) return plain('detail_temporary', 'remedy_temporary');
      const parts: string[] = [];
      if (writable > 0)
        parts.push(t(key('write'), { count: writable, amount: countText(writable) }));
      if (creatable > 0)
        parts.push(t(key('create'), { count: creatable, amount: countText(creatable) }));
      if (temporary) parts.push(t(key('temporary')));
      return plain('detail', 'remedy', { parts: parts.join(t(key('and'))) });
    }
    case 'STU-DX-005':
      return isCount(params.rows)
        ? plain('detail', 'remedy', { count: params.rows, amount: countText(params.rows) })
        : null;
    case 'STU-DX-006': {
      const { owned, prod } = params;
      if (!isCount(owned) || typeof prod !== 'boolean') return null;
      return {
        detail: t(key('detail'), { count: owned, amount: countText(owned) }),
        remedy: t(key(prod ? 'remedy_prod' : 'remedy')),
      };
    }
    case 'STU-DX-008': {
      const { reason, time_limit_s: seconds } = params;
      const skipped = words(params.skipped, (step) => STEPS.includes(step) || STEP_CODE.test(step));
      if (!oneOf(reason, ['canceled', 'time_limit']) || skipped === null) return null;
      if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return null;
      const steps = skipped.map((step) => (STEP_CODE.test(step) ? step : t(key(step)))).join(', ');
      return plain(`detail_${reason}`, 'remedy', { steps, seconds: String(seconds) });
    }
    case 'STU-DX-009':
      return oneOf(params.cause, ['definition', 'unreadable'])
        ? plain(`detail_${params.cause}`, `remedy_${params.cause}`)
        : null;
    case 'STU-DX-010': {
      const missing = words(params.missing, (name) => SETTINGS.includes(name));
      return missing === null ? null : plain('detail', 'remedy', { settings: missing.join(', ') });
    }
    case 'STU-BK-002': {
      const { amount, unit } = params;
      if (!isCount(amount) || !oneOf(unit, UNITS)) return null;
      return plain('detail', 'remedy', { span: t(key(unit), { count: amount }) });
    }
    case 'STU-BK-003':
      return isCount(params.files) ? plain('detail', 'remedy', { count: params.files }) : null;
    case 'STU-BK-005':
      return isCount(params.max_entries)
        ? plain('detail', 'remedy', { limit: params.max_entries })
        : null;
    case 'STU-BK-011':
      return isCount(params.failures)
        ? plain('detail', 'remedy', { count: params.failures })
        : null;
    case 'STU-DX-004':
    case 'STU-DX-007':
    case 'STU-BK-001':
    case 'STU-BK-004':
    case 'STU-BK-010':
    case 'STU-BK-012':
    case 'STU-BK-013':
    case 'STU-BK-014':
      return plain();
    default:
      return null;
  }
}

/**
 * The page's en/de detail and remedy of one of Studio's findings or hints, or null: a finding of tabrion, a code the
 * page does not know, or `params` that do not fit (then the page shows the server's English text).
 */
export function studioText(t: TFunction, finding: Finding): StudioText | null {
  if (finding.source !== 'studio' || finding.params === null) return null;
  return word(t, finding.code, finding.params);
}
