/**
 * The ER diagram of the explorer (STU-023, 03 section 2 E, 08 section 4.7): the tables of one schema (or a choice of
 * at most 150) as boxes with their keys, laid out by dagre and shown with @xyflow/react 12; read-only.
 *
 * - **Data:** one `GET .../diagram?schema=` per schema (`api/explorer.ts`), on the explorer's open connection; nothing
 *   else is asked. A connection that is gone is handed to the explorer (`onGone`), like the data tab does.
 * - **Choice:** every table up to 150; a larger schema shows its first 150 by name and says so. "Tables" lists them
 *   all with a filter; the choice is applied with one button, so a click does not lay out 150 tables again.
 * - **Keyboard and screen readers:** each box's name is a button ("Show … in the explorer"), reached with Tab in name
 *   order; the view follows the focus. The canvas has its own zoom buttons. The same tables and keys are listed in a
 *   table below the diagram (08 section 4.7: "Tabellen-Alternative", required).
 * - **Exports:** SVG and PNG drawn in the page from the shown scene (`exportImage.ts`), no remote resource.
 * - **CSP:** React, xyflow and d3 set their inline styles through `element.style` (the CSSOM), which `style-src
 *   'self'` does not refuse; none of them adds a `<style>` element or a `style` attribute in the parts used (searched),
 *   and xyflow's style sheet is bundled. WebKit reports its own inline style of every `<select>` (TD-047), to the
 *   select only while it is in the document: the explorer therefore loads this view without `React.lazy`, so that the
 *   schema picker is created and attached in one task (`ExplorerView.tsx`, TD-052). `e2e/backend.spec.ts` runs this
 *   view under Studio's CSP in each browser and checks that every `<select>` is attached one task after it is made.
 */
import {
  BaseEdge,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
  useStore,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ChevronRight, Download, Maximize, RefreshCw, ZoomIn, ZoomOut } from 'lucide-react';
import { memo, useEffect, useId, useMemo, useRef, useState, type FocusEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';

import {
  fetchDiagram,
  type Catalog,
  type CatalogObject,
  type Diagram,
  type ForeignKey,
} from '../../../api/explorer';
import type { Problem } from '../../../api/problem';
import { Button } from '../../../components/ui/Button';
import { saveFile as browserSave, type SaveFile } from '../../../lib/download';
import connectionStyles from '../../connections/Connections.module.css';
import { problemText } from '../../connections/messages';
import {
  chosenTables,
  defaultChoice,
  MAX_DIAGRAM_TABLES,
  NAME_CAP,
  sameLayout,
  shown,
  timedScene,
  type ErBox,
  type ErLine,
  type ErScene,
} from './layout';
import { fileName, pagePalette, sceneSvg, scenePng, type ImageTexts } from './exportImage';
import styles from './ErDiagram.module.css';

/** A connection that is gone: the explorer drops it (as for the data tab). */
const GONE = new Set(['studio.connection_not_found', 'studio.connection_stale']);

/** What one diagram request (connection, schema, catalog) brought; another request's answer counts as loading. */
interface Answer {
  connectionId: string;
  schema: string;
  catalog: Catalog;
  result: { ok: true; diagram: Diagram } | { ok: false; problem: Problem };
}

type Load =
  | { status: 'loading' }
  | { status: 'problem'; problem: Problem }
  | { status: 'ready'; diagram: Diagram };

/** The texts of every box, translated once per diagram (not one translation hook in each of 150 boxes). */
interface BoxTexts {
  foreign: string;
  keys: Record<'pk' | 'fk' | 'pkfk', string>;
}

interface BoxData extends Record<string, unknown> {
  box: ErBox;
  /** "Show … in the explorer". */
  open: string;
  /** "+ N more columns", or '' when every column is listed. */
  more: string;
  texts: BoxTexts;
  onShow: (name: string) => void;
  /** Bring the box into view (it was reached with the keyboard). */
  onFocusBox: (box: ErBox) => void;
}
interface LineData extends Record<string, unknown> {
  line: ErLine;
}
type BoxNode = Node<BoxData, 'box'>;
type LineEdge = Edge<LineData, 'line'>;

/** Memoized, without hooks: a box renders again only when its own data changes. */
const TableBox = memo(function TableBox({ data }: NodeProps<BoxNode>) {
  const { box, open, more, texts, onShow, onFocusBox } = data;
  // Names and types are cut with "…" when the text needs more room than the layout gave it (larger text spacing,
  // 1.4.12): the whole text is in the title, as everywhere a cut is shown.
  // A box reached with the keyboard is brought into view (a pointer click is already where the user looks).
  const follow = (event: FocusEvent<HTMLButtonElement>) => {
    if (event.currentTarget.matches(':focus-visible')) onFocusBox(box);
  };
  return (
    <div className={styles.box} data-testid="er-box" data-table={box.id}>
      <Handle
        type="target"
        position={Position.Left}
        isConnectable={false}
        className={styles.handle}
      />
      <Handle
        type="source"
        position={Position.Right}
        isConnectable={false}
        className={styles.handle}
      />
      <button
        type="button"
        className={`${styles.boxName} nodrag nopan`}
        onClick={() => onShow(box.id)}
        onFocus={follow}
        aria-label={open}
      >
        <span className={styles.boxLabel} title={box.label}>
          {box.label}
        </span>
        {box.kind === 'foreign_table' ? (
          <span className={styles.boxKind}>{texts.foreign}</span>
        ) : null}
      </button>
      <ul className={styles.columns}>
        {box.columns.map((column, index) => (
          <li
            key={index}
            className={styles.column}
            title={column.type === '' ? column.name : `${column.name} ${column.type}`}
          >
            <span className={styles.mark}>
              {column.key === null ? null : (
                <>
                  <span aria-hidden="true">
                    {column.key === 'pkfk' ? 'PF' : column.key.toUpperCase()}
                  </span>
                  <span className="tb-sr-only">{texts.keys[column.key]}</span>
                </>
              )}
            </span>
            <span className={styles.columnName}>{column.name}</span>
            <span className={styles.columnType}>{column.type}</span>
          </li>
        ))}
        {more !== '' ? <li className={styles.more}>{more}</li> : null}
      </ul>
    </div>
  );
});

function pathOf(line: ErLine): string {
  return line.points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x} ${point.y}`)
    .join(' ');
}

function KeyLine({ data, markerEnd }: EdgeProps<LineEdge>) {
  if (data === undefined || data.line.points.length < 2) return null;
  return <BaseEdge path={pathOf(data.line)} markerEnd={markerEnd} interactionWidth={0} />;
}

const NODE_TYPES = { box: TableBox };
const EDGE_TYPES = { line: KeyLine };

function ZoomBar() {
  const { t } = useTranslation();
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const zoom = useStore((state) => state.transform[2]);
  return (
    <Panel position="top-left" className={styles.zoomBar}>
      <Button
        size="sm"
        icon={<ZoomOut />}
        aria-label={t('explorer.er.zoomOut')}
        title={t('explorer.er.zoomOut')}
        onClick={() => void zoomOut({ duration: 0 })}
        data-testid="er-zoom-out"
      />
      <span className={styles.zoom} aria-live="off" data-testid="er-zoom">
        {t('explorer.er.zoom', { percent: Math.round(zoom * 100) })}
      </span>
      <Button
        size="sm"
        icon={<ZoomIn />}
        aria-label={t('explorer.er.zoomIn')}
        title={t('explorer.er.zoomIn')}
        onClick={() => void zoomIn({ duration: 0 })}
        data-testid="er-zoom-in"
      />
      <Button
        size="sm"
        icon={<Maximize />}
        aria-label={t('explorer.er.fit')}
        title={t('explorer.er.fit')}
        onClick={() => void fitView({ padding: 0.08, maxZoom: 1, duration: 0 })}
        data-testid="er-fit"
      />
    </Panel>
  );
}

/** A new scene (another choice) or "Lay out again" (`runs`) is fitted into the view once xyflow has its boxes. */
function FitOnChange({ scene, runs }: { scene: ErScene; runs: number }) {
  const { fitView } = useReactFlow();
  const ready = useNodesInitialized();
  useEffect(() => {
    if (ready) void fitView({ padding: 0.08, maxZoom: 1, duration: 0 });
  }, [ready, scene, runs, fitView]);
  return null;
}

/** Memoized: editing the choice of tables re-renders the panel, not the 150 boxes. */
const Canvas = memo(function Canvas({
  scene,
  runs,
  label,
  onShow,
}: {
  scene: ErScene;
  /** How often "Lay out again" ran on this scene: the view is fitted again, the boxes stay. */
  runs: number;
  label: string;
  onShow: (name: string) => void;
}) {
  const { t } = useTranslation();
  const { setCenter, getZoom } = useReactFlow();
  const nodes = useMemo<BoxNode[]>(() => {
    const texts: BoxTexts = {
      foreign: t('explorer.er.foreign'),
      keys: { pk: t('explorer.er.pk'), fk: t('explorer.er.fk'), pkfk: t('explorer.er.pkfk') },
    };
    const onFocusBox = (box: ErBox) =>
      void setCenter(box.x + box.width / 2, box.y + box.height / 2, {
        zoom: Math.max(getZoom(), 0.8),
        duration: 0,
      });
    return scene.boxes.map((box) => ({
      id: box.id,
      type: 'box',
      position: { x: box.x, y: box.y },
      width: box.width,
      height: box.height,
      data: {
        box,
        open: t('explorer.er.open', { name: box.label }),
        more: box.hidden > 0 ? t('explorer.er.more', { count: box.hidden }) : '',
        texts,
        onShow,
        onFocusBox,
      },
      draggable: false,
      selectable: false,
      connectable: false,
      focusable: false,
    }));
  }, [scene, onShow, t, setCenter, getZoom]);
  const edges = useMemo<LineEdge[]>(
    () =>
      scene.lines.map((line) => ({
        id: line.id,
        type: 'line',
        source: line.from,
        target: line.to,
        data: { line },
        selectable: false,
        focusable: false,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
      })),
    [scene],
  );
  const aria = useMemo(
    () => ({
      'node.a11yDescription.default': t('explorer.er.aria.node'),
      'node.a11yDescription.keyboardDisabled': t('explorer.er.aria.node'),
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
  return (
    <div className={styles.canvas} role="group" aria-label={label} data-testid="er-canvas">
      <ReactFlow<BoxNode, LineEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        disableKeyboardA11y
        fitView
        fitViewOptions={{ padding: 0.08, maxZoom: 1 }}
        minZoom={0.05}
        maxZoom={2}
        ariaLabelConfig={aria}
        className={styles.flow}
      >
        <FitOnChange scene={scene} runs={runs} />
        <ZoomBar />
        <MiniMap
          pannable
          zoomable
          ariaLabel={t('explorer.er.minimap')}
          className={styles.minimap}
        />
      </ReactFlow>
    </div>
  );
});

function keyText(t: TFunction, key: ForeignKey): string {
  return key.name === null ? t('explorer.er.unnamed') : shown(key.name, NAME_CAP);
}

function columnsText(columns: string[]): string {
  return columns.map((column) => shown(column, NAME_CAP)).join(', ');
}

/** The text alternative (08 section 4.7, required): every shown table with its keys. */
const TableList = memo(function TableList({
  scene,
  diagram,
  onShow,
}: {
  scene: ErScene;
  diagram: Diagram;
  onShow: (name: string) => void;
}) {
  const { t } = useTranslation();
  const shownNames = new Set(scene.boxes.map((box) => box.id));
  const tables = diagram.tables.filter((table) => shownNames.has(table.name));
  const incoming = new Map<string, { from: string; key: ForeignKey }[]>();
  for (const table of tables) {
    for (const key of table.foreignKeys) {
      if (key.referredSchema !== diagram.schema || !shownNames.has(key.referredTable)) continue;
      const list = incoming.get(key.referredTable) ?? [];
      list.push({ from: table.name, key });
      incoming.set(key.referredTable, list);
    }
  }
  const target = (key: ForeignKey) =>
    key.referredSchema === diagram.schema
      ? shown(key.referredTable, NAME_CAP)
      : `${shown(key.referredSchema, NAME_CAP)}.${shown(key.referredTable, NAME_CAP)}`;
  return (
    <div className={styles.listWrap}>
      <table className={styles.list} data-testid="er-list">
        <caption className={styles.caption}>
          {t('explorer.er.list.caption', { schema: shown(diagram.schema, NAME_CAP) })}
        </caption>
        <thead>
          <tr>
            <th scope="col">{t('explorer.er.list.table')}</th>
            <th scope="col">{t('explorer.er.list.primaryKey')}</th>
            <th scope="col">{t('explorer.er.list.references')}</th>
            <th scope="col">{t('explorer.er.list.referencedBy')}</th>
          </tr>
        </thead>
        <tbody>
          {tables.map((table) => {
            const into = incoming.get(table.name) ?? [];
            return (
              <tr key={table.name} data-testid="er-list-row">
                <th scope="row">
                  <button
                    type="button"
                    className={styles.link}
                    onClick={() => onShow(table.name)}
                    aria-label={t('explorer.er.open', { name: shown(table.name, NAME_CAP) })}
                  >
                    {shown(table.name, NAME_CAP)}
                  </button>
                </th>
                <td>{table.primaryKey.length > 0 ? columnsText(table.primaryKey) : '–'}</td>
                <td>
                  {table.foreignKeys.length === 0 ? (
                    '–'
                  ) : (
                    <ul className={styles.keys}>
                      {table.foreignKeys.map((key, index) => (
                        <li key={index}>
                          {t('explorer.er.list.reference', {
                            name: keyText(t, key),
                            columns: columnsText(key.columns),
                            target: target(key),
                            referred: columnsText(key.referredColumns),
                            interpolation: { escapeValue: false },
                          })}
                          {key.referredSchema !== diagram.schema ||
                          !shownNames.has(key.referredTable)
                            ? ` ${t('explorer.er.list.outside')}`
                            : ''}
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td>
                  {into.length === 0 ? (
                    '–'
                  ) : (
                    <ul className={styles.keys}>
                      {into.map(({ from, key }, index) => (
                        <li key={index}>
                          {t('explorer.er.list.referenced', {
                            table: shown(from, NAME_CAP),
                            columns: columnsText(key.columns),
                            name: keyText(t, key),
                            interpolation: { escapeValue: false },
                          })}
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
});

function defaultSchema(catalog: Catalog): string {
  const withTables = (schema: string) =>
    catalog.objects.some((item) => item.schema === schema && item.kind === 'table');
  if (catalog.schemas.includes('public') && withTables('public')) return 'public';
  return catalog.schemas.find(withTables) ?? catalog.schemas[0] ?? '';
}

export interface ErDiagramProps {
  fetcher: typeof fetch;
  connectionId: string;
  catalog: Catalog;
  /** Show a table in the explorer's tree and details. */
  onShow: (object: CatalogObject) => void;
  /** The connection is gone (the explorer drops it). */
  onGone: (problem: Problem) => void;
  save?: SaveFile;
}

export function ErDiagram({
  fetcher,
  connectionId,
  catalog,
  onShow,
  onGone,
  save = browserSave,
}: ErDiagramProps) {
  const { t } = useTranslation();
  const schemaId = useId();
  const filterId = useId();
  const chooseId = useId();
  const [schema, setSchema] = useState(() => defaultSchema(catalog));
  const [answer, setAnswer] = useState<Answer | null>(null);
  /** The tables in the diagram, and the choice being edited under "Tables". */
  const [chosen, setChosen] = useState<Set<string>>(() => new Set());
  const [draft, setDraft] = useState<Set<string>>(() => new Set());
  const [filter, setFilter] = useState('');
  const [choosing, setChoosing] = useState(false);
  /** "Lay out again" on the shown choice: the count belongs to that choice (a new choice starts at 0). */
  const [relayout, setRelayout] = useState<{ of: ReadonlySet<string> | null; runs: number }>({
    of: null,
    runs: 0,
  });
  const runs = relayout.of === chosen ? relayout.runs : 0;
  const [message, setMessage] = useState('');
  const [exportProblem, setExportProblem] = useState('');
  const onGoneRef = useRef(onGone);
  useEffect(() => {
    onGoneRef.current = onGone;
  }, [onGone]);

  useEffect(() => {
    if (schema === '') return;
    const controller = new AbortController();
    void fetchDiagram(fetcher, connectionId, schema, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      const asked = { connectionId, schema, catalog };
      if (!result.ok) {
        setAnswer({ ...asked, result: { ok: false, problem: result.problem } });
        if (GONE.has(result.problem.code)) onGoneRef.current(result.problem);
        return;
      }
      const choice = defaultChoice(result.value);
      setChosen(choice);
      setDraft(new Set(choice));
      setAnswer({ ...asked, result: { ok: true, diagram: result.value } });
    });
    return () => controller.abort();
    // A new catalog (the explorer's Refresh) reads the diagram again.
  }, [fetcher, connectionId, schema, catalog]);

  const current =
    answer !== null &&
    answer.connectionId === connectionId &&
    answer.schema === schema &&
    answer.catalog === catalog
      ? answer.result
      : null;
  const load: Load =
    current === null
      ? { status: 'loading' }
      : current.ok
        ? { status: 'ready', diagram: current.diagram }
        : { status: 'problem', problem: current.problem };
  const diagram = current?.ok === true ? current.diagram : null;
  // The layout, measured (09 STU-023: 150 tables in under 2 s). "Lay out again" measures it again; the boxes and
  // lines are rebuilt only when the new layout places them differently (dagre is deterministic, so it does not).
  const first = useMemo(
    () => (diagram === null ? null : timedScene(diagram.schema, chosenTables(diagram, chosen))),
    [diagram, chosen],
  );
  const again = useMemo(
    () =>
      diagram === null || runs === 0
        ? null
        : timedScene(diagram.schema, chosenTables(diagram, chosen)),
    [diagram, chosen, runs],
  );
  const laidOut = again ?? first;
  const scene =
    first === null
      ? null
      : again === null || sameLayout(first.scene, again.scene)
        ? first.scene
        : again.scene;

  const show = useMemo(
    () => (name: string) => {
      const object = catalog.objects.find((item) => item.schema === schema && item.name === name);
      if (object !== undefined) onShow(object);
    },
    [catalog, schema, onShow],
  );

  const imageTexts: ImageTexts = {
    title: t('explorer.er.fileTitle', { schema: shown(schema, NAME_CAP) }),
    more: (count) => t('explorer.er.more', { count }),
    line: (line) =>
      t('explorer.er.list.reference', {
        name: keyText(t, line.key),
        columns: columnsText(line.key.columns),
        target: shown(line.to, NAME_CAP),
        referred: columnsText(line.key.referredColumns),
        interpolation: { escapeValue: false },
      }),
  };

  const saveSvg = () => {
    if (scene === null) return;
    const name = fileName(schema, 'svg');
    save(new Blob([sceneSvg(scene, pagePalette(), imageTexts)], { type: 'image/svg+xml' }), name);
    setExportProblem('');
    setMessage(t('explorer.er.saved', { name }));
  };

  const savePng = async () => {
    if (scene === null) return;
    const name = fileName(schema, 'png');
    const blob = await scenePng(scene, pagePalette(), imageTexts);
    if (blob === null) {
      setExportProblem(t('explorer.er.pngFailed'));
      return;
    }
    save(blob, name);
    setExportProblem('');
    setMessage(t('explorer.er.saved', { name }));
  };

  const total = diagram?.tables.length ?? 0;
  const query = filter.trim().toLowerCase();
  const listed =
    diagram === null
      ? []
      : diagram.tables.filter((table) => query === '' || table.name.toLowerCase().includes(query));
  const full = draft.size >= MAX_DIAGRAM_TABLES;
  const changed = draft.size !== chosen.size || [...draft].some((name) => !chosen.has(name));

  let status = '';
  if (laidOut !== null && scene !== null) {
    status = t('explorer.er.status', {
      count: scene.boxes.length,
      lines: scene.lines.length,
      ms: Math.max(1, Math.round(laidOut.ms)),
    });
  }

  return (
    <section
      className={styles.panel}
      aria-labelledby="er-title"
      aria-busy={load.status === 'loading'}
      data-testid="er-panel"
    >
      <div className={styles.toolbar}>
        <h2 id="er-title" className={styles.title}>
          {t('explorer.er.title')}
        </h2>
        <div className={styles.field}>
          <label htmlFor={schemaId} className={connectionStyles.label}>
            {t('explorer.er.schema')}
          </label>
          <select
            id={schemaId}
            className={connectionStyles.input}
            value={schema}
            onChange={(event) => {
              setSchema(event.target.value);
              setMessage('');
              setExportProblem('');
            }}
            data-testid="er-schema"
          >
            {catalog.schemas.map((name) => (
              <option key={name} value={name}>
                {shown(name, NAME_CAP)}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.actions}>
          <Button
            size="sm"
            icon={<RefreshCw />}
            aria-disabled={scene === null || undefined}
            onClick={() => {
              if (scene !== null)
                setRelayout((current) => ({
                  of: chosen,
                  runs: (current.of === chosen ? current.runs : 0) + 1,
                }));
            }}
            data-testid="er-relayout"
          >
            {t('explorer.er.layout')}
          </Button>
          <Button
            size="sm"
            icon={<Download />}
            aria-disabled={scene === null || scene.boxes.length === 0 || undefined}
            onClick={() => {
              if (scene !== null && scene.boxes.length > 0) saveSvg();
            }}
            data-testid="er-svg"
          >
            {t('explorer.er.svg')}
          </Button>
          <Button
            size="sm"
            icon={<Download />}
            aria-disabled={scene === null || scene.boxes.length === 0 || undefined}
            onClick={() => {
              if (scene !== null && scene.boxes.length > 0) void savePng();
            }}
            data-testid="er-png"
          >
            {t('explorer.er.png')}
          </Button>
        </div>
      </div>
      <p
        role="status"
        className={connectionStyles.hint}
        data-testid="er-status"
        data-layout-ms={laidOut?.ms}
      >
        {load.status === 'loading' ? t('explorer.er.loading') : status}
        {message !== '' ? ` ${message}` : ''}
      </p>
      {exportProblem !== '' ? (
        <p role="alert" className={connectionStyles.problem} data-testid="er-export-problem">
          {exportProblem}
        </p>
      ) : null}
      {load.status === 'problem' ? (
        <p role="alert" className={connectionStyles.problem} data-testid="er-problem">
          {problemText(t, load.problem)}
        </p>
      ) : null}
      {diagram !== null && total === 0 ? (
        <p className={connectionStyles.hint} data-testid="er-empty">
          {t('explorer.er.noTables')}
        </p>
      ) : null}
      {diagram !== null && total > MAX_DIAGRAM_TABLES ? (
        <p className={connectionStyles.hint} data-testid="er-capped">
          {t('explorer.er.capped', { total, max: MAX_DIAGRAM_TABLES })}
        </p>
      ) : null}
      {diagram !== null && total > 0 ? (
        // A button that shows and hides the choice; the list is rendered only while it is open (150 rows less in
        // the diagram's render, C-3 of the STU-023 review).
        <div className={styles.choose} data-testid="er-choose">
          <button
            type="button"
            className={styles.chooseToggle}
            aria-expanded={choosing}
            aria-controls={choosing ? chooseId : undefined}
            onClick={() => setChoosing((open) => !open)}
            data-testid="er-choose-toggle"
          >
            <ChevronRight aria-hidden="true" className={styles.chooseIcon} />
            {t('explorer.er.choose.summary', { count: chosen.size, total })}
          </button>
          {/* Rendered only while open: a closed choice costs no rows (150 labels) in the diagram's commit. */}
          {choosing ? (
            <div id={chooseId} className={styles.chooseBody}>
              <div className={styles.chooseTools}>
                <label htmlFor={filterId} className="tb-sr-only">
                  {t('explorer.er.choose.filter')}
                </label>
                <input
                  id={filterId}
                  type="search"
                  className={connectionStyles.input}
                  placeholder={t('explorer.er.choose.filter')}
                  value={filter}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(event) => setFilter(event.target.value)}
                  data-testid="er-choose-filter"
                />
                <Button
                  size="sm"
                  onClick={() => setDraft(defaultChoice(diagram))}
                  data-testid="er-choose-all"
                >
                  {t('explorer.er.choose.all', { max: MAX_DIAGRAM_TABLES })}
                </Button>
                <Button size="sm" onClick={() => setDraft(new Set())} data-testid="er-choose-none">
                  {t('explorer.er.choose.none')}
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  aria-disabled={!changed || draft.size === 0 || undefined}
                  onClick={() => {
                    if (changed && draft.size > 0) setChosen(new Set(draft));
                  }}
                  data-testid="er-choose-apply"
                >
                  {t('explorer.er.choose.apply', { count: draft.size })}
                </Button>
              </div>
              <p className={connectionStyles.hint} aria-live="polite">
                {draft.size === 0
                  ? t('explorer.er.choose.empty')
                  : full
                    ? t('explorer.er.choose.limit', { max: MAX_DIAGRAM_TABLES })
                    : ''}
              </p>
              <fieldset className={styles.chooseList}>
                <legend className="tb-sr-only">{t('explorer.er.choose.legend')}</legend>
                {listed.map((table) => {
                  const checked = draft.has(table.name);
                  return (
                    <label key={table.name} className={styles.chooseItem}>
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={!checked && full}
                        onChange={() =>
                          setDraft((current) => {
                            const next = new Set(current);
                            if (next.has(table.name)) next.delete(table.name);
                            else if (next.size < MAX_DIAGRAM_TABLES) next.add(table.name);
                            return next;
                          })
                        }
                      />
                      <span>{shown(table.name, NAME_CAP)}</span>
                    </label>
                  );
                })}
              </fieldset>
            </div>
          ) : null}
        </div>
      ) : null}
      {diagram !== null && scene !== null && scene.boxes.length > 0 ? (
        <>
          <ReactFlowProvider>
            <Canvas
              scene={scene}
              runs={runs}
              label={t('explorer.er.canvas', {
                schema: shown(schema, NAME_CAP),
                count: scene.boxes.length,
              })}
              onShow={show}
            />
          </ReactFlowProvider>
          <TableList scene={scene} diagram={diagram} onShow={show} />
        </>
      ) : null}
    </section>
  );
}

export default ErDiagram;
