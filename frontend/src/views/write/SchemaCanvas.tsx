/**
 * The Schema Designer's graphical mode (STU-XSD-1; STU-ADR-0007, gate G-S3, not granted; TX-31, GS3-23): the draft
 * the server holds, drawn as the explorer's ER diagram (`../explorer/er/layout.ts`, dagre, @xyflow/react 12) with
 * each table's status. The canvas has no route and no form of its own and never calls the server:
 *
 * - **Choose a table:** its name (a button, reached with Tab in the draft's order; the view follows the focus) or a
 *   click on its box or on one of its lines. The page then shows that table's card from the table view below the
 *   canvas, with every edit it has (`SchemaDesigner.tsx`).
 * - **Draw a foreign key:** a line from a column's dot on the right of a box to a column's dot on the left of another
 *   box (or the same one) opens the table's "New constraint" form with the key filled in. Nothing is sent until the
 *   user applies the form; the form sends the same `add_constraint` edit as the table view to the same route, and the
 *   server checks it like every other edit (TX-31).
 * - **Keyboard and screen readers:** drawing needs a pointer; the table view is the accessible alternative with the
 *   same edits (08 section 4.7), and the chosen table's card offers every edit with the keyboard.
 * - **Size:** the first 150 tables of the draft (the diagram's bound) and the first 300 foreign keys among them as
 *   lines (review I-2 of #148), each with a note when it applies; the table view shows all (at most 500 tables).
 * - **Node ids:** a box's index (`t<i>`), never the table's name (review I-1 of #148): xyflow puts the id unescaped
 *   into a CSS selector while a line is drawn, so a name with `"` or `\\` would break drawing.
 * - **CSP:** as in the explorer's diagram (`ErDiagram.tsx`): styles go through the CSSOM, the style sheet is bundled.
 *   The page loads this module with a state update after the import, not `React.lazy` (TD-052); it holds no `<select>`.
 */
import {
  BaseEdge,
  Handle,
  MarkerType,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
  useStore,
  type Connection,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Maximize, ZoomIn, ZoomOut } from 'lucide-react';
import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  type FocusEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import { useTranslation } from 'react-i18next';

import type { Draft, DraftTable } from '../../api/schemaDesigner';
import { Button } from '../../components/ui/Button';
import erStyles from '../explorer/er/ErDiagram.module.css';
import {
  buildScene,
  GEOMETRY,
  MAX_DIAGRAM_TABLES,
  type ErBox,
  type ErLine,
  type ErScene,
} from '../explorer/er/layout';
import styles from './SchemaCanvas.module.css';
import designer from './SchemaDesigner.module.css';
import {
  diagramTables,
  drawnKey,
  handleId,
  lineCount,
  MAX_DIAGRAM_LINES,
  nodeId,
  type DrawnKey,
} from './schemaCanvasModel';

interface BoxTexts {
  keys: Record<'pk' | 'fk' | 'pkfk', string>;
  /** "Edit table" before the name (read by screen readers only). */
  edit: string;
}

interface BoxData extends Record<string, unknown> {
  box: ErBox;
  status: DraftTable['status'];
  statusText: string;
  chosen: boolean;
  more: string;
  texts: BoxTexts;
  onChoose: (name: string, keyboard?: boolean) => void;
  onFocusBox: (box: ErBox) => void;
}
interface LineData extends Record<string, unknown> {
  line: ErLine;
}
type BoxNode = Node<BoxData, 'draftBox'>;
type LineEdge = Edge<LineData, 'draftLine'>;

/** The vertical middle of column row `index` in a box (`GEOMETRY`, as `ErDiagram.module.css` draws the rows). */
function rowMiddle(index: number): number {
  return GEOMETRY.header + GEOMETRY.padY + index * GEOMETRY.row + GEOMETRY.row / 2;
}

const DraftBox = memo(function DraftBox({ data }: NodeProps<BoxNode>) {
  const { box, status, statusText, chosen, more, texts, onChoose, onFocusBox } = data;
  const follow = (event: FocusEvent<HTMLButtonElement>) => {
    if (event.currentTarget.matches(':focus-visible')) onFocusBox(box);
  };
  const statusClass =
    status === 'new' ? designer.new : status === 'changed' ? designer.changed : '';
  return (
    <>
      <div
        className={`${erStyles.box} ${chosen ? styles.chosen : ''}`}
        data-testid="schema-canvas-box"
        data-table={box.id}
      >
        <button
          type="button"
          className={`${erStyles.boxName} nodrag nopan`}
          aria-pressed={chosen}
          onClick={(event) => {
            // The canvas's click on the box (`onNodeClick`) would choose the table a second time.
            event.stopPropagation();
            // A click from the keyboard (Enter or Space) has no pointer detail: the chosen card takes the focus.
            onChoose(box.id, event.detail === 0);
          }}
          onFocus={follow}
        >
          {/* The name read out: "Edit table bins new" (the button is a flex box: its parts are read apart). */}
          <span className="tb-sr-only">{texts.edit} </span>
          <span className={erStyles.boxLabel} title={box.label}>
            {box.label}
          </span>
          {status !== 'unchanged' ? (
            <span className={`${designer.badge} ${statusClass} ${styles.status}`}>
              {statusText}
            </span>
          ) : null}
        </button>
        <ul className={erStyles.columns}>
          {box.columns.map((column, index) => (
            <li
              key={index}
              className={erStyles.column}
              title={column.type === '' ? column.name : `${column.name} ${column.type}`}
            >
              <span className={erStyles.mark}>
                {column.key === null ? null : (
                  <>
                    <span aria-hidden="true">
                      {column.key === 'pkfk' ? 'PF' : column.key.toUpperCase()}
                    </span>
                    <span className="tb-sr-only">{texts.keys[column.key]}</span>
                  </>
                )}
              </span>
              <span className={erStyles.columnName}>{column.name}</span>
              <span className={erStyles.columnType}>{column.type}</span>
            </li>
          ))}
          {more !== '' ? <li className={erStyles.more}>{more}</li> : null}
        </ul>
      </div>
      {/* One dot per listed column on each side: a key starts on the right and ends on the left. Pointer only; the
          same key is made with the keyboard in the "New constraint" form. */}
      {box.columns.map((_, index) => (
        <Handle
          key={`to${index}`}
          id={handleId('to', index)}
          type="target"
          position={Position.Left}
          className={styles.handle}
          style={{ top: rowMiddle(index) }}
          aria-hidden="true"
          data-testid="schema-canvas-to"
        />
      ))}
      {box.columns.map((_, index) => (
        <Handle
          key={`from${index}`}
          id={handleId('from', index)}
          type="source"
          position={Position.Right}
          className={styles.handle}
          style={{ top: rowMiddle(index) }}
          aria-hidden="true"
          data-testid="schema-canvas-from"
        />
      ))}
    </>
  );
});

function pathOf(line: ErLine): string {
  return line.points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x} ${point.y}`)
    .join(' ');
}

function DraftLine({ data, markerEnd }: EdgeProps<LineEdge>) {
  if (data === undefined || data.line.points.length < 2) return null;
  return <BaseEdge path={pathOf(data.line)} markerEnd={markerEnd} interactionWidth={12} />;
}

const NODE_TYPES = { draftBox: DraftBox };
const EDGE_TYPES = { draftLine: DraftLine };

function ZoomBar() {
  const { t } = useTranslation();
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const zoom = useStore((state) => state.transform[2]);
  return (
    <Panel position="top-left" className={erStyles.zoomBar}>
      <Button
        size="sm"
        icon={<ZoomOut />}
        aria-label={t('explorer.er.zoomOut')}
        title={t('explorer.er.zoomOut')}
        onClick={() => void zoomOut({ duration: 0 })}
      />
      <span className={erStyles.zoom} aria-live="off">
        {t('explorer.er.zoom', { percent: Math.round(zoom * 100) })}
      </span>
      <Button
        size="sm"
        icon={<ZoomIn />}
        aria-label={t('explorer.er.zoomIn')}
        title={t('explorer.er.zoomIn')}
        onClick={() => void zoomIn({ duration: 0 })}
      />
      <Button
        size="sm"
        icon={<Maximize />}
        aria-label={t('explorer.er.fit')}
        title={t('explorer.er.fit')}
        onClick={() => void fitView({ padding: 0.08, maxZoom: 1, duration: 0 })}
      />
    </Panel>
  );
}

/** The view is fitted once xyflow has the boxes, and again when a table comes or goes (not after every edit). */
function FitOnChange({ tables }: { tables: string }) {
  const { fitView } = useReactFlow();
  const ready = useNodesInitialized();
  useEffect(() => {
    if (ready) void fitView({ padding: 0.08, maxZoom: 1, duration: 0 });
  }, [ready, tables, fitView]);
  return null;
}

function Flow({
  draft,
  scene,
  chosen,
  label,
  onChoose,
  onDraw,
}: {
  draft: Draft;
  scene: ErScene;
  chosen: string | null;
  label: string;
  onChoose: (name: string, keyboard?: boolean) => void;
  onDraw: (key: DrawnKey) => void;
}) {
  const { t } = useTranslation();
  const { setCenter, getZoom } = useReactFlow();
  const statusOf = useMemo(
    () => new Map(draft.tables.map((table) => [table.name, table.status])),
    [draft],
  );
  // Review I-1 of #148: a node's id is its index (`t<i>`), never the table's name: xyflow puts the id unescaped into
  // a CSS selector while a line is drawn. The names come back through this map.
  const names = useMemo(
    () => new Map(scene.boxes.map((box, index) => [nodeId(index), box.id])),
    [scene],
  );
  const ids = useMemo(
    () => new Map(scene.boxes.map((box, index) => [box.id, nodeId(index)])),
    [scene],
  );
  const nodes = useMemo<BoxNode[]>(() => {
    const texts: BoxTexts = {
      keys: { pk: t('explorer.er.pk'), fk: t('explorer.er.fk'), pkfk: t('explorer.er.pkfk') },
      edit: t('schemaDesigner.canvas.edit'),
    };
    const onFocusBox = (box: ErBox) =>
      void setCenter(box.x + box.width / 2, box.y + box.height / 2, {
        zoom: Math.max(getZoom(), 0.8),
        duration: 0,
      });
    return scene.boxes.map((box, index) => {
      const status = statusOf.get(box.id) ?? 'unchanged';
      return {
        id: nodeId(index),
        type: 'draftBox',
        position: { x: box.x, y: box.y },
        width: box.width,
        height: box.height,
        data: {
          box,
          status,
          statusText: t(`schemaDesigner.status.${status}`),
          chosen: box.id === chosen,
          more: box.hidden > 0 ? t('explorer.er.more', { count: box.hidden }) : '',
          texts,
          onChoose,
          onFocusBox,
        },
        draggable: false,
        selectable: false,
        connectable: true,
        focusable: false,
      };
    });
  }, [scene, statusOf, chosen, onChoose, t, setCenter, getZoom]);
  const edges = useMemo<LineEdge[]>(
    () =>
      scene.lines.map((line) => ({
        id: line.id,
        type: 'draftLine',
        source: ids.get(line.from) ?? '',
        target: ids.get(line.to) ?? '',
        data: { line },
        selectable: false,
        focusable: false,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
      })),
    [scene, ids],
  );
  const aria = useMemo(
    () => ({
      'node.a11yDescription.default': t('schemaDesigner.canvas.aria'),
      'node.a11yDescription.keyboardDisabled': t('schemaDesigner.canvas.aria'),
      'node.a11yDescription.ariaLiveMessage': () => '',
      'edge.a11yDescription.default': t('explorer.er.aria.edge'),
      'controls.ariaLabel': t('explorer.er.aria.controls'),
      'controls.zoomIn.ariaLabel': t('explorer.er.zoomIn'),
      'controls.zoomOut.ariaLabel': t('explorer.er.zoomOut'),
      'controls.fitView.ariaLabel': t('explorer.er.fit'),
      'controls.interactive.ariaLabel': t('explorer.er.aria.controls'),
      'minimap.ariaLabel': t('explorer.er.minimap'),
      'handle.ariaLabel': t('explorer.er.aria.edge'),
    }),
    [t],
  );
  const chooseNode = useCallback(
    (_event: ReactMouseEvent, node: BoxNode) => onChoose(node.data.box.id),
    [onChoose],
  );
  const chooseLine = useCallback(
    (_event: ReactMouseEvent, edge: LineEdge) => {
      const name = names.get(edge.source);
      if (name !== undefined) onChoose(name);
    },
    [onChoose, names],
  );
  /** The key a drawn line proposes, with the nodes' ids read back as the tables' names. */
  const keyOf = useCallback(
    (connection: Connection | Edge) => {
      const source = names.get(connection.source);
      const target = names.get(connection.target);
      if (source === undefined || target === undefined) return null;
      return drawnKey(draft, {
        source,
        target,
        sourceHandle: connection.sourceHandle,
        targetHandle: connection.targetHandle,
      });
    },
    [draft, names],
  );
  const valid = useCallback((connection: Connection | Edge) => keyOf(connection) !== null, [keyOf]);
  const connect = useCallback(
    (connection: Connection) => {
      const key = keyOf(connection);
      if (key !== null) onDraw(key);
    },
    [keyOf, onDraw],
  );
  const tables = scene.boxes.map((box) => box.id).join('\n');
  return (
    <div className={erStyles.canvas} role="group" aria-label={label} data-testid="schema-canvas">
      <ReactFlow<BoxNode, LineEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        nodesDraggable={false}
        nodesConnectable
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        isValidConnection={valid}
        onConnect={connect}
        onNodeClick={chooseNode}
        onEdgeClick={chooseLine}
        disableKeyboardA11y
        fitView
        fitViewOptions={{ padding: 0.08, maxZoom: 1 }}
        minZoom={0.05}
        maxZoom={2}
        ariaLabelConfig={aria}
        className={`${erStyles.flow} ${styles.flow}`}
      >
        <FitOnChange tables={tables} />
        <ZoomBar />
      </ReactFlow>
    </div>
  );
}

export interface SchemaCanvasProps {
  draft: Draft;
  /** The table whose card the page shows below the canvas. */
  chosen: string | null;
  onChoose: (name: string, keyboard?: boolean) => void;
  /** A line drawn from a column to a column: the page opens the table's "New constraint" form with it. */
  onDraw: (key: DrawnKey) => void;
}

export function SchemaCanvas({ draft, chosen, onChoose, onDraw }: SchemaCanvasProps) {
  const { t } = useTranslation();
  const { scene, lines } = useMemo(() => {
    const tables = diagramTables(draft);
    return {
      scene: buildScene(draft.schema, tables, MAX_DIAGRAM_LINES),
      lines: lineCount(tables),
    };
  }, [draft]);
  return (
    <div className={styles.wrap} data-testid="schema-canvas-panel">
      <p className={designer.meta}>{t('schemaDesigner.canvas.hint')}</p>
      {draft.tables.length > MAX_DIAGRAM_TABLES ? (
        <p className={designer.meta} data-testid="schema-canvas-capped">
          {t('schemaDesigner.canvas.capped', {
            max: MAX_DIAGRAM_TABLES,
            total: draft.tables.length,
          })}
        </p>
      ) : null}
      {lines > MAX_DIAGRAM_LINES ? (
        <p className={designer.meta} data-testid="schema-canvas-lines-capped">
          {t('schemaDesigner.canvas.linesCapped', { max: MAX_DIAGRAM_LINES, total: lines })}
        </p>
      ) : null}
      {scene.boxes.length === 0 ? (
        <p className={designer.meta} data-testid="schema-canvas-empty">
          {t('schemaDesigner.canvas.empty')}
        </p>
      ) : (
        <ReactFlowProvider>
          <Flow
            draft={draft}
            scene={scene}
            chosen={chosen}
            label={t('schemaDesigner.canvas.label', { count: scene.boxes.length })}
            onChoose={onChoose}
            onDraw={onDraw}
          />
        </ReactFlowProvider>
      )}
    </div>
  );
}

export default SchemaCanvas;
