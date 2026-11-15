/**
 * The Data Editor's column types and value checks on the page (STU-XDE-1; the server's twin is
 * `src/tabrion_studio/write/values.py`, and both run the vectors of `cellValues.vectors.json`).
 *
 * The table route sends each column's type: its kind, NULL, whether an insert may leave it out, whether it takes no
 * value at all, the length, precision and scale, an enum's labels and whether it is an array. The page picks a cell
 * editor from it and checks every value before the preview, so the user sees the problem at the cell. The checks
 * here are help only: the server checks every value again before it makes the plan, and the database is the last
 * instance (CHECK, UNIQUE, foreign keys, triggers).
 */

export const KINDS = [
  'boolean',
  'int2',
  'int4',
  'int8',
  'float4',
  'float8',
  'numeric',
  'text',
  'date',
  'time',
  'timetz',
  'timestamp',
  'timestamptz',
  'uuid',
  'json',
  'enum',
  'bytes',
  'other',
] as const;
export type Kind = (typeof KINDS)[number];

export interface ColumnType {
  name: string;
  kind: Kind;
  nullable: boolean;
  /** An insert may leave the column out (a default, an identity, SQLite's rowid alias). */
  optional: boolean;
  /** The column takes no value (generated, identity ALWAYS). */
  readOnly: boolean;
  length: number | null;
  precision: number | null;
  scale: number | null;
  array: boolean;
  /** An enum's labels in their order. */
  values: string[];
}

/** The type of a column the server did not describe: any value, the database checks it. */
export function untyped(name: string): ColumnType {
  return {
    name,
    kind: 'other',
    nullable: true,
    optional: true,
    readOnly: false,
    length: null,
    precision: null,
    scale: null,
    array: false,
    values: [],
  };
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) ? value : null;
}

/** A column type as the server sent it, or null when it is not one. */
export function parseColumnType(body: unknown): ColumnType | null {
  if (typeof body !== 'object' || body === null || Array.isArray(body)) return null;
  const doc = body as Record<string, unknown>;
  if (typeof doc.name !== 'string' || !(KINDS as readonly unknown[]).includes(doc.kind))
    return null;
  const values = Array.isArray(doc.values)
    ? doc.values.filter((item): item is string => typeof item === 'string')
    : [];
  return {
    name: doc.name,
    kind: doc.kind as Kind,
    nullable: doc.nullable !== false,
    optional: doc.optional !== false,
    readOnly: doc.read_only === true,
    length: count(doc.length),
    precision: count(doc.precision),
    scale: count(doc.scale),
    array: doc.array === true,
    values,
  };
}

/** A short label of the type for the column header (`numeric(10,2)`, `text(20)`, `int4[]`). */
export function typeLabel(column: ColumnType): string {
  if (column.kind === 'other') return '';
  let label: string = column.kind;
  if (column.length !== null) label += `(${column.length})`;
  if (column.precision !== null) label += `(${column.precision},${column.scale ?? 0})`;
  return column.array ? `${label}[]` : label;
}

/** The checks, as `value.<check>` codes (the server's issue codes); `value.json_null` is the page's own. */
export type IssueCode =
  | 'value.read_only'
  | 'value.not_null'
  | 'value.required'
  | 'value.type'
  | 'value.format'
  | 'value.too_long'
  | 'value.out_of_range'
  | 'value.not_allowed'
  | 'value.json_null';

const INT_LIMITS: Partial<Record<Kind, bigint>> = {
  int2: 1n << 15n,
  int4: 1n << 31n,
  int8: 1n << 63n,
};
const FLOAT4_MAX = 3.4028234663852886e38;
const INTEGER = /^[+-]?\d+$/;
const DECIMAL = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;
const SPECIAL_FLOAT = /^(?:[+-]?(?:inf|infinity)|nan)$/i;
const DATE = String.raw`(\d{4})-(\d{2})-(\d{2})`;
const TIME = String.raw`(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?`;
const OFFSET = String.raw`(?:Z|[+-](\d{2}):(\d{2})(?::(\d{2}))?)`;
const FORMATS: Partial<Record<Kind, RegExp>> = {
  date: new RegExp(`^${DATE}$`),
  time: new RegExp(`^${TIME}$`),
  timetz: new RegExp(`^${TIME}${OFFSET}$`),
  timestamp: new RegExp(`^${DATE}[T ]${TIME}$`),
  timestamptz: new RegExp(`^${DATE}[T ]${TIME}${OFFSET}$`),
  uuid: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
};
const BOOLEAN_TEXT = ['true', 't', '1', 'false', 'f', '0'];
/** The kinds an array may have elements of (as `_ARRAY_KINDS` on the server). */
const ARRAY_KINDS: readonly Kind[] = [
  'boolean',
  'int2',
  'int4',
  'int8',
  'float4',
  'float8',
  'text',
];

/** The checks of `check_value` on the server: null when `value` fits `column`, else the issue. */
export function checkValue(column: ColumnType, value: unknown): IssueCode | null {
  if (column.readOnly) return 'value.read_only';
  if (value === null) return column.nullable ? null : 'value.not_null';
  if (column.array && ARRAY_KINDS.includes(column.kind)) return checkArray(column, value, 0);
  return checkScalar(column, value);
}

function checkArray(column: ColumnType, value: unknown, depth: number): IssueCode | null {
  if (!Array.isArray(value) || depth > 5) return 'value.type'; // at most 6 dimensions
  const items = value as unknown[];
  if (items.some(Array.isArray)) {
    const first = items[0] as unknown[];
    if (!items.every((item) => Array.isArray(item) && item.length === first.length)) {
      return 'value.format';
    }
    for (const item of items) {
      const issue = checkArray(column, item, depth + 1);
      if (issue !== null) return issue;
    }
    return null;
  }
  for (const item of items) {
    const issue = item === null ? null : checkScalar(column, item);
    if (issue !== null) return issue;
  }
  return null;
}

function checkScalar(column: ColumnType, value: unknown): IssueCode | null {
  const kind = column.kind;
  if (kind === 'other' || kind === 'json') return null;
  if (kind === 'bytes') return isBytes(value) ? null : 'value.type';
  if (kind === 'boolean') {
    if (typeof value === 'boolean') return null;
    return typeof value === 'string' && BOOLEAN_TEXT.includes(value.trim().toLowerCase())
      ? null
      : 'value.type';
  }
  if (typeof value === 'boolean') return 'value.type';
  const limit = INT_LIMITS[kind];
  if (limit !== undefined) return checkInteger(limit, value);
  if (kind === 'float4' || kind === 'float8') return checkFloat(kind, value);
  if (kind === 'numeric') return checkNumeric(column, value);
  if (typeof value !== 'string') return 'value.type';
  if (value.includes('\u0000')) return 'value.format';
  if (kind === 'text') {
    // Like PostgreSQL: a longer value is refused unless the excess is spaces, which are cut. Counted in characters.
    return column.length !== null && [...value.replace(/ +$/, '')].length > column.length
      ? 'value.too_long'
      : null;
  }
  if (kind === 'enum') return column.values.includes(value) ? null : 'value.not_allowed';
  return checkFormat(kind, value);
}

function checkInteger(limit: bigint, value: unknown): IssueCode | null {
  let number: bigint;
  if (typeof value === 'number') {
    if (!Number.isInteger(value)) return 'value.type';
    number = BigInt(value);
  } else if (typeof value === 'string') {
    if (!INTEGER.test(value)) return 'value.format';
    number = BigInt(value);
  } else {
    return 'value.type';
  }
  return -limit <= number && number < limit ? null : 'value.out_of_range';
}

function checkFloat(kind: Kind, value: unknown): IssueCode | null {
  let number: number;
  if (typeof value === 'string') {
    if (SPECIAL_FLOAT.test(value)) return null;
    if (decimalParts(value) === null) return 'value.format';
    number = Number(value);
  } else if (typeof value === 'number') {
    number = value;
  } else {
    return 'value.type';
  }
  if (!Number.isFinite(number) || (kind === 'float4' && Math.abs(number) > FLOAT4_MAX)) {
    return 'value.out_of_range';
  }
  return null;
}

/** `[digits, exponent]` of a decimal text (`value = digits * 10 ** exponent`, no leading zeros), or null. */
function decimalParts(text: string): [string, number] | null {
  const found = DECIMAL.exec(text);
  if (found === null || !(found[2] || found[3])) return null;
  const whole = found[2] ?? '';
  const fraction = found[3] ?? '';
  const exponent = found[4] ?? '0';
  const digits = (whole + fraction).replace(/^0+/, '');
  if (exponent.replace(/^[+-]/, '').length > 9) {
    return [digits, exponent.startsWith('-') ? -1e9 : 1e9];
  }
  return [digits, Number(exponent) - fraction.length];
}

function checkNumeric(column: ColumnType, value: unknown): IssueCode | null {
  let text: string;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return 'value.out_of_range';
    text = String(value);
  } else if (typeof value === 'string') {
    text = value;
  } else {
    return 'value.type';
  }
  if (text.toLowerCase() === 'nan') return null;
  if (SPECIAL_FLOAT.test(text)) return column.precision === null ? null : 'value.out_of_range';
  const parts = decimalParts(text);
  if (parts === null) return 'value.format';
  if (column.precision === null) return null;
  return fits(parts[0], parts[1], column.precision, column.scale ?? 0)
    ? null
    : 'value.out_of_range';
}

/**
 * Whether `digits * 10 ** exponent`, rounded half away from zero to `scale` places as PostgreSQL does, has at most
 * `precision` digits (as `_fits` on the server).
 */
function fits(digits: string, exponent: number, precision: number, scale: number): boolean {
  if (digits === '') return true;
  const shift = exponent + scale;
  if (shift >= 0) return digits.length + shift <= precision;
  if (-shift > digits.length) return true;
  const divisor = 10n ** BigInt(-shift);
  const whole = BigInt(digits);
  let quotient = whole / divisor;
  if (2n * (whole % divisor) >= divisor) quotient += 1n;
  return quotient === 0n || quotient.toString().length <= precision;
}

function checkFormat(kind: Kind, value: string): IssueCode | null {
  const found = FORMATS[kind]?.exec(value);
  if (!found) return 'value.format';
  if (kind === 'uuid') return null;
  const numbers = found.slice(1).map((part) => (part === undefined ? null : Number(part)));
  let at = 0;
  if (kind === 'date' || kind === 'timestamp' || kind === 'timestamptz') {
    const [year, month, day] = numbers as [number, number, number];
    const date = new Date(Date.UTC(2000, month - 1, day));
    date.setUTCFullYear(year);
    if (
      year < 1 ||
      date.getUTCFullYear() !== year ||
      date.getUTCMonth() !== month - 1 ||
      date.getUTCDate() !== day
    ) {
      return 'value.format';
    }
    at = 3;
  }
  if (kind !== 'date') {
    const [hour, minute, second] = numbers.slice(at, at + 3);
    if ((hour ?? 0) > 23 || (minute ?? 0) > 59 || (second ?? 0) > 59) return 'value.format';
    const [offsetHour, offsetMinute, offsetSecond] = numbers.slice(at + 3, at + 6);
    if ((offsetHour ?? 0) > 23 || (offsetMinute ?? 0) > 59 || (offsetSecond ?? 0) > 59) {
      return 'value.format';
    }
  }
  return null;
}

export function isBytes(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const keys = Object.keys(value);
  const text = (value as { base64?: unknown }).base64;
  return (
    keys.length === 1 &&
    typeof text === 'string' &&
    /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(text)
  );
}

// --- what the user typed ------------------------------------------------------------------------------------------

/**
 * The text the user typed into a cell's text field, kept as typed (so "1." or a half-written JSON stays in the field)
 * and turned into a value for the column only when the changeset is built (`resolveText`).
 */
export class Typed {
  constructor(readonly text: string) {}
}

export type Resolved = { value: unknown } | { issue: IssueCode };

/**
 * The value of `text` typed into a cell of `column` whose loaded value was `original`, checked like the server
 * checks it. Whole numbers become JSON numbers where JavaScript holds them exactly (else text); a float a number;
 * a decimal, a date, a time and a UUID stay text (the server converts them for the column); JSON and arrays are
 * parsed. A column the server does not type (`other`) keeps the rule of STU-026: a number stays a number and a
 * boolean a boolean when the text reads as one, anything else is text. Text is never trimmed; NULL is never the
 * empty text.
 */
export function resolveText(column: ColumnType, original: unknown, text: string): Resolved {
  const trimmed = text.trim();
  let value: unknown = text;
  if (column.kind === 'other') {
    value = untypedValue(original, text);
  } else if (column.kind === 'json' || column.array) {
    try {
      value = JSON.parse(text) as unknown;
    } catch {
      return { issue: 'value.format' };
    }
    if (value === null) return { issue: 'value.json_null' };
  } else if (INT_LIMITS[column.kind] !== undefined) {
    value =
      INTEGER.test(trimmed) && Number.isSafeInteger(Number(trimmed)) ? Number(trimmed) : trimmed;
  } else if (column.kind === 'float4' || column.kind === 'float8') {
    value = decimalParts(trimmed) !== null ? Number(trimmed) : trimmed;
  } else if (column.kind !== 'text') {
    value = trimmed;
  }
  const issue = checkValue(column, value);
  return issue === null ? { value } : { issue };
}

/** STU-026's rule for a column without a type: a number stays a number, a boolean a boolean, else text. */
export function untypedValue(original: unknown, text: string): unknown {
  const trimmed = text.trim();
  if (typeof original === 'number' && trimmed !== '' && Number.isFinite(Number(trimmed))) {
    return Number(trimmed);
  }
  if (typeof original === 'boolean' && (trimmed === 'true' || trimmed === 'false')) {
    return trimmed === 'true';
  }
  return text;
}

/** The cell editor for a column: a choice (boolean, enum), a text field, or nothing to edit. */
export type EditorKind = 'boolean' | 'enum' | 'text' | 'readOnly';

export function editorKind(column: ColumnType, original: unknown): EditorKind {
  if (column.readOnly || column.kind === 'bytes') return 'readOnly';
  if (column.array || column.kind === 'json') return 'text';
  if (column.kind === 'boolean') return 'boolean';
  if (column.kind === 'enum') return 'enum';
  // Untyped: a value the text field can show and give back (not bytes, JSON or an array the viewer sent).
  return original === null || ['string', 'number', 'boolean'].includes(typeof original)
    ? 'text'
    : 'readOnly';
}

/** The text of a value in a text field (NULL is the empty field with the placeholder "NULL"). */
export function fieldText(value: unknown): string {
  if (value instanceof Typed) return value.text;
  if (value === null || value === undefined) return '';
  return typeof value === 'string' ? value : JSON.stringify(value);
}
