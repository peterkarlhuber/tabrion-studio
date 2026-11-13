/**
 * "Format" in the SQL editor (STU-020; 03 section 3.2 names sql-formatter, MIT). The formatter loads only when it is
 * first used, so the editor page stays small. `:name` placeholders stay as they are; strings and comments keep their
 * text. A text the formatter cannot parse is left unchanged (null).
 */
import type { Dialect } from './sqlScan';

export async function formatSql(text: string, dialect: Dialect): Promise<string | null> {
  const { format } = await import('sql-formatter');
  try {
    return format(text, {
      language: dialect === 'sqlite' ? 'sqlite' : 'postgresql',
      keywordCase: 'upper',
      tabWidth: 2,
      paramTypes: { named: [':'] },
    });
  } catch {
    return null;
  }
}
