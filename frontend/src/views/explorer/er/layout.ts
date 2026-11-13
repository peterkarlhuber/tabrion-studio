/**
 * The ER diagram's scene (STU-023, 03 section 2 E, 08 section 4.7): the boxes and lines of the tables chosen from one
 * schema, laid out by dagre (MIT; elkjs is not used, E-S4). The interactive view (`ErDiagram.tsx`) and both exports
 * (`exportImage.ts`) draw this one scene, so what is saved is what is shown.
 *
 * - **Boxes:** a table's name and its first `MAX_BOX_COLUMNS` columns (primary and foreign key columns marked), the
 *   rest counted in a last line; every name escaped and capped (`lib/visible.ts`, U-1). Sizes come from the text
 *   lengths in a monospace font, so the layout needs no measuring in the page (and runs the same in a test).
 * - **Lines:** one per foreign key between two shown tables, from the referencing table to the referenced one, with
 *   dagre's route. Keys to tables that are not shown are listed in the table alternative only (`outside`).
 * - **Layout:** tables with a line among them go through dagre (left to right, referenced tables first); tables
 *   without one are packed in rows below, so that a schema of unrelated tables is not one endless column.
 *
 * At most `MAX_DIAGRAM_TABLES` (150) tables, the bound of 03 section 2 E; the acceptance of 09 (STU-023) is a layout
 * of 150 tables in under 2 s (`layout.test.ts`, `e2e/er-diagram.spec.ts` measure it).
 */
import { Graph, layout } from '@dagrejs/dagre';

import type { Diagram, DiagramTable, ForeignKey, ObjectKind } from '../../../api/explorer';
import { capped } from '../../../lib/visible';

/** The most tables one diagram shows (03 section 2 E: "max. 150 Tabellen"). */
export const MAX_DIAGRAM_TABLES = 150;
/** The most columns a box lists; the others are counted ("+ 12 more columns"). */
export const MAX_BOX_COLUMNS = 20;
/** The longest table or column name a box shows, and the longest type (code points, after escaping). */
export const NAME_CAP = 40;
export const TYPE_CAP = 24;

/** Box geometry in CSS pixels; `ErDiagram.module.css` uses the same numbers. */
export const GEOMETRY = {
  /** Advance of one character of the box font (12 px monospace; 0.6 em, rounded up). */
  char: 7.3,
  header: 32,
  row: 20,
  padX: 10,
  padY: 6,
  /** The key marker column ("PK", "FK"). */
  badge: 24,
  /** Between a column's name and its type. */
  gap: 16,
  minWidth: 140,
  maxWidth: 560,
} as const;

const RANK_SEP = 90;
const NODE_SEP = 36;
/** The space around the dagre part and between packed tables. */
const MARGIN = 24;
const PACK_GAP = 36;

export type KeyMark = 'pk' | 'fk' | 'pkfk' | null;

export interface ErColumn {
  name: string;
  type: string;
  key: KeyMark;
  nullable: boolean | null;
}

export interface ErBox {
  /** The table's name as the database spells it (also its id in the scene: one schema). */
  id: string;
  /** The name as shown: escaped and capped. */
  label: string;
  kind: ObjectKind;
  x: number;
  y: number;
  width: number;
  height: number;
  columns: ErColumn[];
  /** Columns not listed in the box. */
  hidden: number;
}

export interface Point {
  x: number;
  y: number;
}

export interface ErLine {
  id: string;
  /** The referencing table (the key's table). */
  from: string;
  /** The referenced table. */
  to: string;
  key: ForeignKey;
  /** From the referencing box to the referenced one. */
  points: Point[];
}

/** A key to a table that is not in the diagram (another schema, or not chosen). */
export interface OutsideKey {
  from: string;
  key: ForeignKey;
}

export interface ErScene {
  schema: string;
  boxes: ErBox[];
  lines: ErLine[];
  outside: OutsideKey[];
  width: number;
  height: number;
}

/** A name for a box: escaped and capped, with a visible "…" when cut. */
export function shown(value: string, cap: number): string {
  const [text, cut] = capped(value, cap);
  return cut ? `${text}…` : text;
}

function keyMark(table: DiagramTable, column: string): KeyMark {
  const pk = table.primaryKey.includes(column);
  const fk = table.foreignKeys.some((key) => key.columns.includes(column));
  if (pk && fk) return 'pkfk';
  if (pk) return 'pk';
  return fk ? 'fk' : null;
}

function box(table: DiagramTable): ErBox {
  const columns = table.columns.slice(0, MAX_BOX_COLUMNS).map((column) => ({
    name: shown(column.name, NAME_CAP),
    type: shown(column.type ?? '', TYPE_CAP),
    key: keyMark(table, column.name),
    nullable: column.nullable,
  }));
  const hidden = Math.max(0, table.columns.length - columns.length);
  const label = shown(table.name, NAME_CAP);
  const g = GEOMETRY;
  const nameChars = Math.max(0, ...columns.map((column) => column.name.length));
  const typeChars = Math.max(0, ...columns.map((column) => column.type.length));
  const rowWidth = g.padX * 2 + g.badge + (nameChars + typeChars) * g.char + g.gap;
  // The header: the name and room for the kind marker of a foreign table.
  const headWidth = g.padX * 2 + (label.length + 3) * g.char;
  // The "+ N more columns" line is short text; 22 characters cover it in both languages.
  const moreWidth = hidden > 0 ? g.padX * 2 + 22 * g.char : 0;
  const width = Math.ceil(
    Math.min(g.maxWidth, Math.max(g.minWidth, rowWidth, headWidth, moreWidth)),
  );
  const rows = columns.length + (hidden > 0 ? 1 : 0);
  const height = g.header + g.padY * 2 + Math.max(1, rows) * g.row;
  return { id: table.name, label, kind: table.kind, x: 0, y: 0, width, height, columns, hidden };
}

/** The tables shown: `chosen` (names) in the diagram's order, at most `MAX_DIAGRAM_TABLES`. */
export function chosenTables(diagram: Diagram, chosen: ReadonlySet<string>): DiagramTable[] {
  return diagram.tables.filter((table) => chosen.has(table.name)).slice(0, MAX_DIAGRAM_TABLES);
}

/** The default choice: every table, or the first `MAX_DIAGRAM_TABLES` by name when there are more. */
export function defaultChoice(diagram: Diagram): Set<string> {
  return new Set(diagram.tables.slice(0, MAX_DIAGRAM_TABLES).map((table) => table.name));
}

/** Lay out `tables` of `diagram`'s schema. Pure and synchronous: the caller measures it. */
export function buildScene(schema: string, tables: DiagramTable[]): ErScene {
  const boxes = tables.slice(0, MAX_DIAGRAM_TABLES).map(box);
  const byId = new Map(boxes.map((item) => [item.id, item]));
  const lines: ErLine[] = [];
  const outside: OutsideKey[] = [];
  for (const table of tables.slice(0, MAX_DIAGRAM_TABLES)) {
    table.foreignKeys.forEach((key, index) => {
      if (key.referredSchema === schema && byId.has(key.referredTable)) {
        lines.push({
          id: `${lines.length}:${index}`,
          from: table.name,
          to: key.referredTable,
          key,
          points: [],
        });
      } else outside.push({ from: table.name, key });
    });
  }

  // The connected part through dagre: referenced tables (the edge's tail) left, referencing ones right.
  const linked = new Set(lines.flatMap((line) => [line.from, line.to]));
  const graph = new Graph({ multigraph: true });
  graph.setGraph({ rankdir: 'LR', ranksep: RANK_SEP, nodesep: NODE_SEP, marginx: 0, marginy: 0 });
  graph.setDefaultEdgeLabel(() => ({}));
  for (const item of boxes) {
    if (linked.has(item.id)) graph.setNode(item.id, { width: item.width, height: item.height });
  }
  for (const line of lines) graph.setEdge(line.to, line.from, { minlen: 1 }, line.id);
  let width = 0;
  let height = 0;
  if (linked.size > 0) {
    layout(graph);
    for (const item of boxes) {
      if (!linked.has(item.id)) continue;
      const placed = graph.node(item.id) as { x: number; y: number };
      item.x = MARGIN + placed.x - item.width / 2;
      item.y = MARGIN + placed.y - item.height / 2;
      width = Math.max(width, item.x + item.width);
      height = Math.max(height, item.y + item.height);
    }
    for (const line of lines) {
      const route = (
        graph.edge({ v: line.to, w: line.from, name: line.id }) as {
          points?: Point[];
        }
      ).points;
      // dagre routes tail to head (referenced to referencing); a line is drawn from the key to its target.
      line.points = (route ?? [])
        .map((point) => ({ x: point.x + MARGIN, y: point.y + MARGIN }))
        .reverse();
    }
  }

  // The tables without a line: in rows below, as wide as the dagre part (or a square-ish block without one).
  const loose = boxes.filter((item) => !linked.has(item.id));
  if (loose.length > 0) {
    const area = loose.reduce(
      (sum, item) => sum + (item.width + PACK_GAP) * (item.height + PACK_GAP),
      0,
    );
    const rowLimit = Math.max(width - MARGIN, Math.sqrt(area) * 1.6, GEOMETRY.maxWidth);
    let x = MARGIN;
    let y = height > 0 ? height + PACK_GAP * 2 : MARGIN;
    let rowHeight = 0;
    for (const item of loose) {
      if (x > MARGIN && x + item.width > MARGIN + rowLimit) {
        x = MARGIN;
        y += rowHeight + PACK_GAP;
        rowHeight = 0;
      }
      item.x = x;
      item.y = y;
      x += item.width + PACK_GAP;
      rowHeight = Math.max(rowHeight, item.height);
      width = Math.max(width, item.x + item.width);
      height = Math.max(height, item.y + item.height);
    }
  }
  return {
    schema,
    boxes,
    lines,
    outside,
    width: Math.ceil(width + MARGIN),
    height: Math.ceil(height + MARGIN),
  };
}

/** `buildScene` with the time it took (ms), as the page reports it ("laid out in 120 ms"). */
export function timedScene(
  schema: string,
  tables: DiagramTable[],
  now: () => number = () => performance.now(),
): { scene: ErScene; ms: number } {
  const started = now();
  const scene = buildScene(schema, tables);
  return { scene, ms: now() - started };
}
