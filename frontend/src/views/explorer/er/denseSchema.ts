/** STU-023: a dense schema for the layout tests (`layout.test.ts`, `layout.perf.test.ts`), the same in every run. */
import type { DiagramTable } from '../../../api/explorer';

/** A fixed pseudo-random sequence (the same schema in every run). */
function random(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state * 1_103_515_245 + 12_345) % 2_147_483_648;
    return state / 2_147_483_648;
  };
}

/** `count` tables; each references up to three earlier ones (some twice, one itself), 300 keys in all for 150. */
export function denseSchema(count: number): DiagramTable[] {
  const next = random(23);
  const name = (index: number) =>
    `table_${String(index).padStart(3, '0')}_${'x'.repeat(index % 13)}`;
  return Array.from({ length: count }, (_, index) => {
    const keys = index === 0 ? 0 : Math.min(index, index % 5 === 0 ? 4 : 2);
    const foreignKeys = Array.from({ length: keys }, (_, key) => ({
      name: `fk_${index}_${key}`,
      columns: [`ref_${key}`],
      referredSchema: 'public',
      referredTable: name(index === 7 && key === 0 ? 7 : Math.floor(next() * index)),
      referredColumns: ['id'],
    }));
    const columns = 4 + Math.floor(next() * 27);
    return {
      name: name(index),
      kind: 'table',
      columns: Array.from({ length: columns }, (_, column) => ({
        name: column === 0 ? 'id' : column <= keys ? `ref_${column - 1}` : `column_${column}`,
        type: column % 3 === 0 ? 'TIMESTAMP WITH TIME ZONE' : 'BIGINT',
        nullable: column > 0,
      })),
      primaryKey: ['id'],
      foreignKeys,
    };
  });
}
