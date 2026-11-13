/**
 * STU-023: the ER diagram's scene. The acceptance of 09 ("150 Tabellen < 2 s Layout") is timed in
 * `layout.perf.test.ts`, alone in one worker (`npm test`, project "perf"); the browser measures it again in
 * `e2e/er-diagram.spec.ts`.
 */
import type { Diagram, DiagramTable } from '../../../api/explorer';
import { HOSTILE_TABLE, largeDiagram } from '../../../harness/explorer';
import { parseDiagram } from '../../../api/explorer';
import {
  buildScene,
  chosenTables,
  defaultChoice,
  GEOMETRY,
  MAX_BOX_COLUMNS,
  MAX_DIAGRAM_TABLES,
  sameLayout,
  timedScene,
  type ErBox,
} from './layout';
import { denseSchema } from './denseSchema';

function overlaps(a: ErBox, b: ErBox): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

describe('the layout of 150 tables', () => {
  it('places every box apart, inside the scene, and routes every line', () => {
    const { scene } = timedScene('public', denseSchema(150));
    for (const [index, box] of scene.boxes.entries()) {
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.y).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(scene.width);
      expect(box.y + box.height).toBeLessThanOrEqual(scene.height);
      for (const other of scene.boxes.slice(index + 1)) expect(overlaps(box, other)).toBe(false);
    }
    for (const line of scene.lines) expect(line.points.length).toBeGreaterThanOrEqual(2);
  });

  it('uses the time the clock gives', () => {
    const times = [10, 25.5];
    expect(timedScene('audit', [], () => times.shift() ?? 0).ms).toBe(15.5);
  });

  it('is the same every time, so "Lay out again" keeps the shown boxes', () => {
    const tables = denseSchema(40);
    const first = buildScene('public', tables);
    expect(sameLayout(first, buildScene('public', tables))).toBe(true);
    const moved = buildScene('public', tables);
    (moved.boxes[3] as ErBox).x += 1;
    expect(sameLayout(first, moved)).toBe(false);
    const rerouted = buildScene('public', tables);
    const point = rerouted.lines[0]?.points[0];
    if (point !== undefined) point.y += 1;
    expect(sameLayout(first, rerouted)).toBe(false);
    expect(sameLayout(first, buildScene('public', tables.slice(1)))).toBe(false);
  });
});

describe('boxes', () => {
  const diagram = parseDiagram(largeDiagram(10)) as Diagram;

  it('mark keys, list at most 20 columns and count the rest', () => {
    const scene = buildScene('audit', diagram.tables);
    const wide = scene.boxes.find((box) => box.id === 'audit_003');
    expect(wide?.columns).toHaveLength(MAX_BOX_COLUMNS);
    expect(wide?.hidden).toBe(30 - MAX_BOX_COLUMNS);
    expect(wide?.height).toBe(
      GEOMETRY.header + GEOMETRY.padY * 2 + (MAX_BOX_COLUMNS + 1) * GEOMETRY.row,
    );
    expect(wide?.columns.slice(0, 3).map((column) => column.key)).toEqual(['pk', 'fk', null]);
    const both = buildScene('s', [
      {
        name: 't',
        kind: 'table',
        columns: [{ name: 'a', type: null, nullable: false }],
        primaryKey: ['a'],
        foreignKeys: [
          {
            name: null,
            columns: ['a'],
            referredSchema: 's',
            referredTable: 't',
            referredColumns: ['a'],
          },
        ],
      },
    ]);
    expect(both.boxes[0]?.columns[0]).toEqual({
      name: 'a',
      type: '',
      key: 'pkfk',
      nullable: false,
    });
    // A key of a table to itself is a line too.
    expect(both.lines).toHaveLength(1);
    expect(both.lines[0]?.points.length).toBeGreaterThanOrEqual(2);
  });

  it('show names escaped and capped, and size the box by its text', () => {
    const long = 'n'.repeat(80);
    const scene = buildScene('s', [
      {
        name: HOSTILE_TABLE,
        kind: 'foreign_table',
        columns: [{ name: long, type: 'CHARACTER VARYING(255) ARRAY[]', nullable: true }],
        primaryKey: [],
        foreignKeys: [],
      },
      { name: 'small', kind: 'table', columns: [], primaryKey: [], foreignKeys: [] },
    ]);
    const [hostile, small] = scene.boxes;
    expect(hostile?.label).toBe('notes\\u202Etxt.exe\\u000Ax');
    expect(hostile?.columns[0]?.name).toBe(`${'n'.repeat(40)}…`);
    expect(hostile?.columns[0]?.type).toBe(`${'CHARACTER VARYING(255) A'}…`);
    expect(hostile?.width).toBeLessThanOrEqual(GEOMETRY.maxWidth);
    expect(hostile?.width).toBeGreaterThan(41 * GEOMETRY.char + 25 * GEOMETRY.char);
    expect(small?.width).toBe(GEOMETRY.minWidth);
    // An empty table still has one row of height.
    expect(small?.height).toBe(GEOMETRY.header + GEOMETRY.padY * 2 + GEOMETRY.row);
  });
});

describe('lines and the choice', () => {
  const diagram = parseDiagram(largeDiagram(150)) as Diagram;

  it('draw keys between shown tables, from the key to its target, and list the others', () => {
    const scene = buildScene('audit', diagram.tables);
    expect(scene.lines).toHaveLength(170);
    const line = scene.lines.find((item) => item.key.name === 'audit_002_parent_fkey');
    expect(line).toMatchObject({ from: 'audit_002', to: 'audit_000' });
    const from = scene.boxes.find((box) => box.id === 'audit_002') as ErBox;
    const to = scene.boxes.find((box) => box.id === 'audit_000') as ErBox;
    // Left to right: the referenced table first; the line starts at the key's box and ends at the target's.
    expect(to.x + to.width).toBeLessThan(from.x);
    const points = line?.points ?? [];
    expect(points[0]?.x).toBeCloseTo(from.x, 0);
    expect(points[points.length - 1]?.x).toBeCloseTo(to.x + to.width, 0);
    expect(scene.outside).toEqual([
      { from: 'audit_001', key: expect.objectContaining({ referredTable: 'device' }) as unknown },
    ]);
  });

  it('keeps a key to a table that is not chosen out of the drawing', () => {
    const chosen = new Set(['audit_000', 'audit_002', 'audit_009']);
    const scene = buildScene('audit', chosenTables(diagram, chosen));
    expect(scene.boxes.map((box) => box.id)).toEqual(['audit_000', 'audit_002', 'audit_009']);
    expect(scene.lines.map((line) => [line.from, line.to])).toEqual([['audit_002', 'audit_000']]);
    // audit_009 points at audit_004, which is not shown; it is packed below the connected part.
    expect(scene.outside.map((item) => item.key.referredTable)).toEqual(['audit_004']);
    const loose = scene.boxes[2] as ErBox;
    expect(loose.y).toBeGreaterThan(Math.max(scene.boxes[0]?.y ?? 0, scene.boxes[1]?.y ?? 0));
  });

  it('packs tables without keys in rows instead of one column', () => {
    const tables: DiagramTable[] = Array.from({ length: 100 }, (_, index) => ({
      name: `t${index}`,
      kind: 'table',
      columns: [{ name: 'id', type: 'INTEGER', nullable: false }],
      primaryKey: ['id'],
      foreignKeys: [],
    }));
    const scene = buildScene('main', tables);
    expect(scene.lines).toEqual([]);
    expect(scene.width / scene.height).toBeGreaterThan(0.5);
    expect(scene.width / scene.height).toBeLessThan(4);
  });

  it('chooses at most 150 tables, the first by name, and never draws more', () => {
    const many = { ...diagram, tables: [...diagram.tables, ...denseSchema(20)] };
    const choice = defaultChoice(many);
    expect(choice.size).toBe(MAX_DIAGRAM_TABLES);
    expect(choice.has('audit_149')).toBe(true);
    expect(buildScene('audit', many.tables).boxes).toHaveLength(MAX_DIAGRAM_TABLES);
    const all = new Set(many.tables.map((table) => table.name));
    expect(chosenTables(many, all)).toHaveLength(MAX_DIAGRAM_TABLES);
  });
});
