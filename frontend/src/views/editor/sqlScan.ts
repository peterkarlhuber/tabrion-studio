/**
 * Statements and `:name` placeholders of the editor's text (03 section 2 C, 08 section 4.4).
 *
 * The editor needs both before it sends anything: "Run" takes the statement under the cursor, "Run script" every
 * statement in turn, and the parameter panel lists the placeholders. The scan follows the backend's rules
 * (`services/sqltext.py`, docs/architecture/queries.md): literals, quoted identifiers, dollar quotes (PostgreSQL) and
 * comments are skipped, `::` is a cast, and a `;` outside of them ends a statement.
 *
 * This is a convenience, not a security boundary: the backend checks every statement again (one statement, an
 * allowed first word, `tabrion.sql.split_statements`) and refuses what it does not accept. A text this scan reads
 * differently from the server is refused there, never run as something else.
 */

export type Dialect = 'postgresql' | 'sqlite';

export interface Statement {
  /** Offsets into the text: `text.slice(from, to)` is the statement without its `;`. */
  from: number;
  to: number;
  sql: string;
  /** The placeholders in the order they first appear, each once. */
  params: string[];
}

const NAME_START = /[A-Za-z_]/;
const NAME_PART = /[A-Za-z0-9_]/;
const SPACE = /[ \t\n\r\f\v]/;

interface Token {
  kind: 'code' | 'skip' | 'semicolon' | 'param';
  from: number;
  to: number;
  name?: string;
}

/** The end of a quoted run that starts at `start` with `quote`; a doubled quote stays inside. */
function quoted(text: string, start: number, quote: string, backslash = false): number {
  let i = start + 1;
  while (i < text.length) {
    const char = text[i];
    if (backslash && char === '\\') {
      i += 2;
      continue;
    }
    if (char === quote) {
      if (text[i + 1] === quote) {
        i += 2;
        continue;
      }
      return i + 1;
    }
    i += 1;
  }
  return text.length;
}

function blockComment(text: string, start: number, nested: boolean): number {
  let depth = 1;
  let i = start + 2;
  while (i < text.length) {
    if (nested && text.startsWith('/*', i)) {
      depth += 1;
      i += 2;
    } else if (text.startsWith('*/', i)) {
      depth -= 1;
      i += 2;
      if (depth === 0) return i;
    } else {
      i += 1;
    }
  }
  return text.length;
}

/** `$tag$` at `start`, or null (a `$1` positional parameter is not a tag). */
function dollarTag(text: string, start: number): string | null {
  const match = /^\$([A-Za-z_\u0080-\uffff][A-Za-z0-9_\u0080-\uffff]*)?\$/.exec(text.slice(start));
  return match ? match[0] : null;
}

function* tokens(text: string, dialect: Dialect): Generator<Token> {
  let i = 0;
  while (i < text.length) {
    const char = text[i] as string;
    const next = text[i + 1];
    const start = i;
    if (char === '-' && next === '-') {
      const end = text.indexOf('\n', i);
      i = end === -1 ? text.length : end + 1;
      yield { kind: 'skip', from: start, to: i };
    } else if (char === '/' && next === '*') {
      i = blockComment(text, i, dialect === 'postgresql');
      yield { kind: 'skip', from: start, to: i };
    } else if (char === "'") {
      // PostgreSQL's E'...' takes backslash escapes; the E was the previous code character.
      const escaped =
        dialect === 'postgresql' &&
        i > 0 &&
        /[eE]/.test(text[i - 1] as string) &&
        (i < 2 || !NAME_PART.test(text[i - 2] as string));
      i = quoted(text, i, "'", escaped);
      yield { kind: 'skip', from: start, to: i };
    } else if (char === '"') {
      i = quoted(text, i, '"');
      yield { kind: 'skip', from: start, to: i };
    } else if (dialect === 'sqlite' && char === '`') {
      i = quoted(text, i, '`');
      yield { kind: 'skip', from: start, to: i };
    } else if (dialect === 'sqlite' && char === '[') {
      const end = text.indexOf(']', i);
      i = end === -1 ? text.length : end + 1;
      yield { kind: 'skip', from: start, to: i };
    } else if (
      dialect === 'postgresql' &&
      char === '$' &&
      (i === 0 || !NAME_PART.test(text[i - 1] as string))
    ) {
      const tag = dollarTag(text, i);
      if (tag === null) {
        i += 1;
        yield { kind: 'code', from: start, to: i };
      } else {
        const end = text.indexOf(tag, i + tag.length);
        i = end === -1 ? text.length : end + tag.length;
        yield { kind: 'skip', from: start, to: i };
      }
    } else if (char === ';') {
      i += 1;
      yield { kind: 'semicolon', from: start, to: i };
    } else if (char === ':' && next === ':') {
      i += 2;
      yield { kind: 'code', from: start, to: i };
    } else if (char === ':' && next !== undefined && NAME_START.test(next)) {
      i += 2;
      while (i < text.length && NAME_PART.test(text[i] as string)) i += 1;
      yield { kind: 'param', from: start, to: i, name: text.slice(start + 1, i) };
    } else if (NAME_PART.test(char)) {
      // A whole word at once, so a `:` inside it (none in SQL) or an E before a quote is judged by its neighbour.
      while (i < text.length && NAME_PART.test(text[i] as string)) i += 1;
      yield { kind: 'code', from: start, to: i };
    } else {
      i += 1;
      yield { kind: SPACE.test(char) ? 'skip' : 'code', from: start, to: i };
    }
  }
}

/**
 * The statements of `text`: split at every `;` outside literals and comments, trimmed, and without the ones that
 * hold only white space and comments.
 */
export function splitStatements(text: string, dialect: Dialect): Statement[] {
  const result: Statement[] = [];
  let from = -1;
  let to = -1;
  let params: string[] = [];
  const close = () => {
    if (from !== -1) result.push({ from, to, sql: text.slice(from, to), params });
    from = -1;
    to = -1;
    params = [];
  };
  for (const token of tokens(text, dialect)) {
    if (token.kind === 'semicolon') {
      close();
      continue;
    }
    const blank = token.kind === 'skip' && SPACE.test(text[token.from] as string);
    if (blank) continue;
    if (from === -1) {
      // A statement that holds only comments is dropped below; it starts at its first non-blank token.
      from = token.from;
    }
    to = token.to;
    if (token.kind === 'param' && token.name !== undefined && !params.includes(token.name)) {
      params.push(token.name);
    }
  }
  close();
  return result.filter((statement) => hasCode(statement.sql, dialect));
}

function hasCode(sql: string, dialect: Dialect): boolean {
  for (const token of tokens(sql, dialect)) {
    if (token.kind === 'code' || token.kind === 'param') return true;
    if (token.kind === 'skip' && !SPACE.test(sql[token.from] as string)) {
      const first = sql[token.from];
      // A literal or a quoted identifier is code; only comments are not.
      if (first !== '-' && first !== '/') return true;
    }
  }
  return false;
}

/**
 * The statement the cursor is in. Between two statements (after a `;`, in white space or a comment) it is the one
 * before the cursor, else the first one after it; null for a text without statements.
 */
export function statementAt(statements: Statement[], position: number): Statement | null {
  let before: Statement | null = null;
  for (const statement of statements) {
    if (position >= statement.from && position <= statement.to) return statement;
    if (statement.to < position) before = statement;
    else return before ?? statement;
  }
  return before;
}

/** Every placeholder of the text, in order, each once. */
export function placeholders(text: string, dialect: Dialect): string[] {
  const names: string[] = [];
  for (const token of tokens(text, dialect)) {
    if (token.kind === 'param' && token.name !== undefined && !names.includes(token.name)) {
      names.push(token.name);
    }
  }
  return names;
}
