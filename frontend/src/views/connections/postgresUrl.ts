/**
 * "Or paste a URL" of the assistant (08 section 4.2, 03 section 2 A): a `postgresql://` URL fills the fields of step 1,
 * and its password moves to the password field of step 2, which stores it in the keyring (or for this session),
 * never in the file. Parsed in the page; nothing is sent until the profile is saved.
 *
 * Only what the profile can hold is taken: host, port, database, user and `sslmode`. A URL with anything else (several
 * hosts, a socket folder, other parameters) is refused as a whole rather than taken in part, so the profile never
 * connects differently from the URL the user pasted.
 */
export interface PostgresUrl {
  host: string;
  port: string;
  database: string;
  user: string;
  password: string;
  sslmode: string;
}

export type UrlRefusal = 'scheme' | 'invalid' | 'host' | 'parameters';

const SSL_MODES = new Set(['disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full']);

function decode(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

export function parsePostgresUrl(text: string): PostgresUrl | UrlRefusal {
  const trimmed = text.trim();
  const scheme = /^(postgresql|postgres):\/\//i.exec(trimmed);
  if (scheme === null) return 'scheme';
  let url: URL;
  try {
    // "postgresql" is not a special scheme for the URL parser: the host is taken as written (an IPv6 host in
    // brackets), and no port is dropped as a default.
    url = new URL(`postgresql://${trimmed.slice(scheme[0].length)}`);
  } catch {
    return 'invalid';
  }
  if (url.hash !== '') return 'invalid';
  // Several hosts (libpq's "h1,h2") or an encoded host cannot go into a profile; no host means a socket folder.
  if (url.hostname === '' || /[,%]/.test(url.hostname)) return 'host';
  const parameters = [...url.searchParams.keys()];
  if (
    parameters.some((key) => key !== 'sslmode') ||
    url.searchParams.getAll('sslmode').length > 1
  ) {
    return 'parameters';
  }
  const sslmode = url.searchParams.get('sslmode') ?? '';
  if (sslmode !== '' && !SSL_MODES.has(sslmode)) return 'parameters';
  const path = url.pathname.replace(/^\//, '');
  const database = decode(path);
  const user = decode(url.username);
  const password = decode(url.password);
  if (database === null || user === null || password === null || database.includes('/')) {
    return 'invalid';
  }
  const host = url.hostname.startsWith('[') ? url.hostname.slice(1, -1) : url.hostname;
  return { host, port: url.port, database, user, password, sslmode };
}

/**
 * `text` without its passwords, so a refused URL does not keep showing one (L-2 of `stu-008-connect-review.md`):
 *
 * - the password of the user part (`postgresql://user:secret@host` -> `postgresql://user@host`). The user part ends at
 *   the last `@` before the path, as the URL parser reads it. When the user part cannot be found that way (a `/` in
 *   the password) but the text still looks like it holds `user:password@`, the whole text goes;
 * - every `password` parameter of the query (`?password=secret`), its name read as the URL parser decodes it.
 */
export function withoutPassword(text: string): string {
  return withoutPasswordParameter(withoutUserPassword(text));
}

function withoutUserPassword(text: string): string {
  const match = /^(\s*[a-z]+:\/\/)([^/?#]*)@/i.exec(text);
  if (match === null) return /^\s*[a-z]+:\/\/[^@]*:[^@]*@/i.test(text) ? '' : text;
  const userinfo = match[2] ?? '';
  const colon = userinfo.indexOf(':');
  if (colon < 0) return text;
  return `${match[1]}${userinfo.slice(0, colon)}@${text.slice(match[0].length)}`;
}

function parameterName(part: string): string {
  const name = part.split('=', 1)[0] ?? '';
  return (decode(name.replace(/\+/g, ' ')) ?? name).trim().toLowerCase();
}

function withoutPasswordParameter(text: string): string {
  const start = text.indexOf('?');
  if (start < 0) return text;
  const hash = text.indexOf('#', start);
  const end = hash < 0 ? text.length : hash;
  const parts = text.slice(start + 1, end).split('&');
  const kept = parts.filter((part) => parameterName(part) !== 'password');
  if (kept.length === parts.length) return text;
  const query = kept.filter((part) => part !== '').join('&');
  return `${text.slice(0, start)}${query === '' ? '' : `?${query}`}${text.slice(end)}`;
}
