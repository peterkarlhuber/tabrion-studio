/**
 * The graphical mode of the Schema Designer (STU-XSD-1, TX-31, GS3-23), its pure part: the draft as the tables of the
 * explorer's ER scene (`../explorer/er/layout.ts`, laid out by dagre) and a line drawn on the canvas as a foreign key
 * the page proposes. Nothing here sends anything: the canvas hands a drawn key to the table's form, which sends the
 * same `add_constraint` edit as the table view to the same route (`POST /api/write/schema-designer/draft/edits`).
 */
import type { DiagramTable } from '../../api/explorer';
import type { Draft } from '../../api/schemaDesigner';
import { MAX_BOX_COLUMNS, MAX_DIAGRAM_TABLES } from '../explorer/er/layout';

/** A foreign key drawn on the canvas: from a column of the referencing table to a column of the referenced one. */
export interface DrawnKey {
  table: string;
  column: string;
  referredTable: string;
  referredColumn: string;
}

/**
 * The most foreign keys the canvas draws as lines (review I-2 of #148): like the explorer's bound on tables, a bound
 * on what dagre lays out in the page. The keys after it are not drawn; the canvas says so, and the table view lists
 * every key.
 */
export const MAX_DIAGRAM_LINES = 300;

/** The id of the canvas node of the shown table at `index` (review I-1 of #148): never the table's name, which xyflow
 * puts unescaped into a CSS selector while a line is drawn. */
export function nodeId(index: number): string {
  return `t${index}`;
}

/** The foreign keys among the tables the canvas shows (`diagramTables`): the lines it would draw without the bound. */
export function lineCount(tables: DiagramTable[]): number {
  const shown = new Set(tables.map((table) => table.name));
  return tables.reduce(
    (sum, table) => sum + table.foreignKeys.filter((key) => shown.has(key.referredTable)).length,
    0,
  );
}

/** The tables the canvas shows: the draft's first `MAX_DIAGRAM_TABLES`, in the draft's order (the table view: all). */
export function diagramTables(draft: Draft): DiagramTable[] {
  return draft.tables.slice(0, MAX_DIAGRAM_TABLES).map((table) => ({
    name: table.name,
    kind: 'table',
    columns: table.columns.map((column) => ({
      name: column.name,
      type: column.type,
      nullable: column.nullable,
    })),
    primaryKey: table.primaryKey,
    foreignKeys: table.foreignKeys.map((key) => ({
      name: key.name,
      columns: key.columns,
      referredSchema: draft.schema,
      referredTable: key.referredTable,
      referredColumns: key.referredColumns,
    })),
  }));
}

/** The handle of a box's column: `from` on the right (a key starts there), `to` on the left (a key points there). */
export function handleId(side: 'from' | 'to', index: number): string {
  return `${side}:${index}`;
}

const HANDLE = /^(from|to):(\d{1,3})$/;

function columnAt(draft: Draft, table: string, handle: string | null | undefined, side: string) {
  const match = HANDLE.exec(handle ?? '');
  if (match === null || match[1] !== side) return null;
  const index = Number(match[2]);
  if (index >= MAX_BOX_COLUMNS) return null;
  return draft.tables.find((item) => item.name === table)?.columns[index]?.name ?? null;
}

/**
 * The key a line drawn on the canvas proposes (xyflow's `Connection`), or null when it does not join a column's
 * `from` handle to a column's `to` handle of the draft. The server checks the key when the form sends it, like every
 * other edit (TX-31); this only reads the names back from the shown draft.
 */
export function drawnKey(
  draft: Draft,
  connection: {
    source: string;
    target: string;
    sourceHandle?: string | null;
    targetHandle?: string | null;
  },
): DrawnKey | null {
  const column = columnAt(draft, connection.source, connection.sourceHandle, 'from');
  const referredColumn = columnAt(draft, connection.target, connection.targetHandle, 'to');
  if (column === null || referredColumn === null) return null;
  return { table: connection.source, column, referredTable: connection.target, referredColumn };
}
