/**
 * The Schema Designer's read-only schema comparison (STU-XSD-2; STU-ADR-0007 sections 1 to 3, gate G-S3, not
 * granted). Rendered only while the server says the `schema_designer` flag is on; the server refuses the route
 * while it is off (405) and decides everything that matters.
 *
 * - **Two sides:** a reference (expected) and a compared side (actual). Each is a schema of an open connection
 *   (any environment, PROD included: it is only read), this session's draft of a connection, or a snapshot file the
 *   user picks (`SchemaSnapshot.to_json`, level 2; at most one side). The page sends the file's text as it is.
 * - **Result:** the verdict (same, different, or inconclusive when a feature marker hides part of the schema),
 *   both fingerprints, every difference as a table (kind, table, column, name, breaking), and the feature markers
 *   of each side (domains, partitions, rules ...).
 * - **Plan preview** (on request): the plan from the compared side to the reference, shown with its steps, SQL and
 *   warnings and never run; the server makes it only for a DEV or TEST connection of the user's own profiles and
 *   never from or to a file.
 */
import type { TFunction } from 'i18next';
import { useEffect, useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchOpenConnections, type OpenedConnection } from '../../api/connections';
import type { Problem } from '../../api/problem';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import {
  compareSchemas,
  MAX_SNAPSHOT_BYTES,
  type CompareSide,
  type Comparison,
  type FeatureMarker,
  type SideSummary,
} from '../../api/schemaCompare';
import { fetchWriteAreas } from '../../api/write';
import { Button } from '../../components/ui/Button';
import { visible } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { issueText, problemText } from '../connections/messages';
import styles from '../Views.module.css';
import { PlanIssues, PlanSteps, Warnings } from './SchemaDesigner';
import designer from './SchemaDesigner.module.css';

export function SchemaCompareSection({ fetcher = fetch }: { fetcher?: typeof fetch }) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    let alive = true;
    void fetchWriteAreas(fetcher).then((areas) => {
      if (alive) setOn(areas.schema_designer);
    });
    return () => {
      alive = false;
    };
  }, [fetcher]);
  if (!on) return null;
  return <SchemaCompare fetcher={fetcher} />;
}

type Source = 'connection' | 'draft' | 'file';

/** The `lock_timeout` a run of the designer uses (the server's warning carries its own value). */
const LOCK_TIMEOUT_S = 5;

interface SideState {
  source: Source;
  connectionId: string;
  schema: string;
  file: { name: string; text: string } | null;
  fileProblem: 'too_large' | 'unreadable' | null;
}

interface Option {
  connectionId: string;
  name: string;
  environment: string | null;
}

function options(opened: OpenedConnection[], profiles: ProfileItem[]): Option[] {
  return opened.map((connection) => {
    const profile = profiles.find((item) => item.id === connection.profileId);
    const name = profile?.name ?? connection.profileId ?? connection.connectionId;
    const environment = connection.environment ?? profile?.environment ?? null;
    return {
      connectionId: connection.connectionId,
      name: visible(name),
      environment: environment === null ? null : environment.toUpperCase(),
    };
  });
}

function emptySide(connectionId: string): SideState {
  return { source: 'connection', connectionId, schema: '', file: null, fileProblem: null };
}

function request(side: SideState): CompareSide | null {
  if (side.source === 'file')
    return side.file === null ? null : { source: 'file', text: side.file.text };
  if (side.connectionId === '') return null;
  if (side.source === 'draft') return { source: 'draft', connectionId: side.connectionId };
  return { source: 'connection', connectionId: side.connectionId, schema: side.schema.trim() };
}

function SidePicker({
  which,
  side,
  choices,
  problem,
  onChange,
}: {
  which: 'expected' | 'actual';
  side: SideState;
  choices: Option[];
  problem: Problem | null;
  onChange: (side: SideState) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const issues = (problem?.issues ?? []).filter(
    (issue) =>
      issue.pointer === `compare.${which}` || issue.pointer.startsWith(`compare.${which}.`),
  );
  async function pick(file: File | undefined) {
    if (file === undefined) {
      onChange({ ...side, file: null, fileProblem: null });
      return;
    }
    if (file.size > MAX_SNAPSHOT_BYTES) {
      onChange({ ...side, file: null, fileProblem: 'too_large' });
      return;
    }
    try {
      onChange({ ...side, file: { name: file.name, text: await file.text() }, fileProblem: null });
    } catch {
      onChange({ ...side, file: null, fileProblem: 'unreadable' });
    }
  }
  return (
    <fieldset className={connectionStyles.fieldset} data-testid={`schema-compare-${which}`}>
      <legend className={connectionStyles.label}>{t(`schemaCompare.side.${which}`)}</legend>
      <p className={connectionStyles.hint}>{t(`schemaCompare.side.${which}Hint`)}</p>
      <div
        className={connectionStyles.choices}
        role="radiogroup"
        aria-label={t(`schemaCompare.side.${which}`)}
      >
        {(['connection', 'draft', 'file'] as const).map((source) => (
          <label key={source} className={connectionStyles.choice}>
            <input
              type="radio"
              name={`${id}-source`}
              value={source}
              checked={side.source === source}
              onChange={() => onChange({ ...side, source })}
              data-testid={`schema-compare-${which}-source-${source}`}
            />
            {t(`schemaCompare.sources.${source}`)}
          </label>
        ))}
      </div>
      {side.source === 'file' ? (
        <Field label={t('schemaCompare.file')} hint={t('schemaCompare.fileHint')}>
          {(props) => (
            <input
              {...props}
              type="file"
              accept=".json,application/json"
              className={connectionStyles.input}
              onChange={(event) => void pick(event.target.files?.[0])}
              data-testid={`schema-compare-${which}-file-input`}
            />
          )}
        </Field>
      ) : (
        <Field label={t('schemaCompare.connection')}>
          {(props) => (
            <select
              {...props}
              className={connectionStyles.input}
              value={side.connectionId}
              onChange={(event) => onChange({ ...side, connectionId: event.target.value })}
              data-testid={`schema-compare-${which}-connection`}
            >
              {choices.map((choice) => (
                <option key={choice.connectionId} value={choice.connectionId}>
                  {choice.environment === null
                    ? choice.name
                    : t('schemaCompare.connectionOption', {
                        name: choice.name,
                        environment: choice.environment,
                        interpolation: { escapeValue: false },
                      })}
                </option>
              ))}
            </select>
          )}
        </Field>
      )}
      {side.source === 'connection' ? (
        <Field label={t('schemaCompare.schema')} hint={t('schemaCompare.schemaHint')}>
          {(props) => (
            <input
              {...props}
              className={connectionStyles.input}
              value={side.schema}
              onChange={(event) => onChange({ ...side, schema: event.target.value })}
              autoComplete="off"
              spellCheck={false}
              data-testid={`schema-compare-${which}-schema`}
            />
          )}
        </Field>
      ) : null}
      {side.source === 'draft' ? (
        <p className={connectionStyles.hint}>{t('schemaCompare.draftHint')}</p>
      ) : null}
      {side.source === 'file' && side.file !== null ? (
        <p className={connectionStyles.hint} data-testid={`schema-compare-${which}-file-name`}>
          {t('schemaCompare.fileChosen', {
            name: visible(side.file.name),
            interpolation: { escapeValue: false },
          })}
        </p>
      ) : null}
      {side.fileProblem !== null ? (
        <p role="alert" className={connectionStyles.error}>
          {t(`schemaCompare.fileProblems.${side.fileProblem}`)}
        </p>
      ) : null}
      {issues.length > 0 ? (
        <ul className={connectionStyles.issues}>
          {issues.map((issue) => (
            <li key={`${issue.pointer}:${issue.code}`}>{issueText(t, issue)}</li>
          ))}
        </ul>
      ) : null}
    </fieldset>
  );
}

function SchemaCompare({ fetcher }: { fetcher: typeof fetch }) {
  const { t } = useTranslation();
  const id = useId();
  const [choices, setChoices] = useState<Option[] | null>(null);
  const [expected, setExpected] = useState<SideState>(emptySide(''));
  const [actual, setActual] = useState<SideState>(emptySide(''));
  const [plan, setPlan] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [result, setResult] = useState<Comparison | null>(null);

  useEffect(() => {
    let alive = true;
    void Promise.all([fetchOpenConnections(fetcher), fetchProfiles(fetcher)]).then(
      ([opened, list]) => {
        if (!alive) return;
        const found = options(opened ?? [], list?.profiles ?? []);
        setChoices(found);
        const first = found[0]?.connectionId ?? '';
        setExpected((side) => (side.connectionId === '' ? { ...side, connectionId: first } : side));
        setActual((side) => (side.connectionId === '' ? { ...side, connectionId: first } : side));
      },
    );
    return () => {
      alive = false;
    };
  }, [fetcher]);

  const left = request(expected);
  const right = request(actual);
  const twoFiles = expected.source === 'file' && actual.source === 'file';

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (left === null || right === null || twoFiles) return;
    setBusy(true);
    setProblem(null);
    const answer = await compareSchemas(fetcher, left, right, plan);
    setBusy(false);
    if (answer.ok) {
      setResult(answer.value);
    } else {
      setResult(null);
      setProblem(answer.problem);
    }
  }

  let body;
  if (choices === null) {
    body = <p className={connectionStyles.hint}>{t('schemaCompare.loading')}</p>;
  } else {
    const general = (problem?.issues ?? []).filter((issue) => issue.pointer === 'compare.plan');
    body = (
      <form className={designer.designer} onSubmit={(event) => void submit(event)} noValidate>
        {choices.length === 0 ? (
          <p className={connectionStyles.hint} data-testid="schema-compare-none">
            {t('schemaCompare.noConnection')}
          </p>
        ) : null}
        <SidePicker
          which="expected"
          side={expected}
          choices={choices}
          problem={problem}
          onChange={setExpected}
        />
        <SidePicker
          which="actual"
          side={actual}
          choices={choices}
          problem={problem}
          onChange={setActual}
        />
        {twoFiles ? (
          <p role="alert" className={connectionStyles.error} data-testid="schema-compare-two-files">
            {t('schemaCompare.issues.two_files')}
          </p>
        ) : null}
        <label className={connectionStyles.check}>
          <input
            type="checkbox"
            checked={plan}
            onChange={(event) => setPlan(event.target.checked)}
            data-testid="schema-compare-plan"
          />
          {t('schemaCompare.planToggle')}
        </label>
        <p className={connectionStyles.hint}>{t('schemaCompare.planHint')}</p>
        <div className={designer.toolbar}>
          <Button
            type="submit"
            variant="primary"
            disabled={busy || left === null || right === null || twoFiles}
            data-testid="schema-compare-submit"
          >
            {t('schemaCompare.submit')}
          </Button>
        </div>
        {problem !== null ? (
          <div role="alert" className={connectionStyles.error} data-testid="schema-compare-problem">
            <p>{problemText(t, problem)}</p>
            {general.length > 0 ? (
              <ul className={connectionStyles.issues}>
                {general.map((issue) => (
                  <li key={issue.code}>{issueText(t, issue)}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
        {result !== null ? <Result comparison={result} /> : null}
      </form>
    );
  }
  return (
    <section className={styles.card} aria-labelledby={`${id}-title`} data-testid="schema-compare">
      <h2 id={`${id}-title`} className={styles.cardTitle}>
        {t('schemaCompare.title')}
      </h2>
      <p className={styles.lead}>{t('schemaCompare.lead')}</p>
      {body}
    </section>
  );
}

function sourceText(t: TFunction, side: SideSummary): string {
  return t(`schemaCompare.sources.${side.source}`, { defaultValue: side.source });
}

function markerText(t: TFunction, marker: FeatureMarker): string {
  const kind = t(`schemaCompare.features.${marker.known ? marker.kind : 'unknown'}`, {
    defaultValue: marker.kind,
  });
  if (marker.known && marker.name === null) return kind;
  return t('schemaCompare.markerNamed', {
    kind,
    name: visible(marker.known && marker.name !== null ? marker.name : marker.marker),
    interpolation: { escapeValue: false },
  });
}

function Result({ comparison }: { comparison: Comparison }) {
  const { t } = useTranslation();
  const id = useId();
  const sides = [
    ['expected', comparison.expected],
    ['actual', comparison.actual],
  ] as const;
  const plan = comparison.plan;
  return (
    <section
      className={designer.table}
      aria-labelledby={`${id}-title`}
      data-testid="schema-compare-result"
      data-verdict={comparison.verdict}
    >
      <h3 id={`${id}-title`} className={designer.tableName}>
        {t('schemaCompare.resultTitle')}
      </h3>
      <p className={connectionStyles.status} role="status" data-testid="schema-compare-verdict">
        {t(`schemaCompare.verdicts.${comparison.verdict}`)}{' '}
        {comparison.differences.length > 0
          ? t('schemaCompare.differenceCount', {
              count: comparison.differences.length,
              breaking: comparison.breaking,
            })
          : null}
      </p>
      {comparison.fingerprintOnly ? (
        <p className={connectionStyles.warning}>{t('schemaCompare.fingerprintOnly')}</p>
      ) : null}
      {comparison.flagged ? (
        <p className={connectionStyles.warning} data-testid="schema-compare-flagged">
          {t('schemaCompare.flaggedLead')}
        </p>
      ) : null}
      {comparison.unknownFeatures ? (
        <p className={connectionStyles.warning} data-testid="schema-compare-unknown">
          {t('schemaCompare.unknownLead')}
        </p>
      ) : null}
      <div className={designer.scroll}>
        <table className={designer.grid}>
          <caption className="tb-sr-only">{t('schemaCompare.sidesTitle')}</caption>
          <thead>
            <tr>
              <th scope="col">{t('schemaCompare.column.side')}</th>
              <th scope="col">{t('schemaCompare.column.source')}</th>
              <th scope="col">{t('schemaCompare.column.tables')}</th>
              <th scope="col">{t('schemaCompare.column.triggers')}</th>
              <th scope="col">{t('schemaCompare.column.policies')}</th>
              <th scope="col">{t('schemaCompare.column.fingerprint')}</th>
            </tr>
          </thead>
          <tbody>
            {sides.map(([role, side]) => (
              <tr key={role}>
                <th scope="row">{t(`schemaCompare.side.${role}`)}</th>
                <td>
                  {sourceText(t, side)}
                  {side.schema !== null ? (
                    <>
                      {' '}
                      <span className={designer.code}>{visible(side.schema)}</span>
                    </>
                  ) : null}
                  {side.environment !== null ? (
                    <>
                      {' '}
                      {t('schemaCompare.environment', {
                        environment: side.environment.toUpperCase(),
                      })}
                    </>
                  ) : null}
                </td>
                <td>{side.tables}</td>
                <td>{side.triggers}</td>
                <td>{side.policies}</td>
                <td className={designer.hash}>{side.fingerprint}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sides.some(([, side]) => side.flagged.length > 0 || side.features.length > 0) ? (
        <div data-testid="schema-compare-features">
          <h4 className={designer.tableName}>{t('schemaCompare.featuresTitle')}</h4>
          <ul className={connectionStyles.issues}>
            {sides.flatMap(([role, side]) => [
              ...side.features.map((marker) => (
                <li key={`${role}:db:${marker.marker}`} data-testid="schema-compare-feature">
                  {t('schemaCompare.featureOf', {
                    side: t(`schemaCompare.side.${role}`),
                    marker: markerText(t, marker),
                    interpolation: { escapeValue: false },
                  })}
                </li>
              )),
              ...side.flagged.flatMap((table) =>
                table.features.map((marker) => (
                  <li
                    key={`${role}:${table.table}:${marker.marker}`}
                    data-testid="schema-compare-feature"
                  >
                    {t('schemaCompare.featureOfTable', {
                      side: t(`schemaCompare.side.${role}`),
                      table: visible(table.table),
                      marker: markerText(t, marker),
                      interpolation: { escapeValue: false },
                    })}
                  </li>
                )),
              ),
            ])}
          </ul>
        </div>
      ) : null}
      {comparison.differences.length > 0 ? (
        <div className={designer.scroll}>
          <table className={designer.grid} data-testid="schema-compare-differences">
            <caption className="tb-sr-only">{t('schemaCompare.differencesTitle')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('schemaCompare.column.kind')}</th>
                <th scope="col">{t('schemaCompare.column.table')}</th>
                <th scope="col">{t('schemaCompare.column.column')}</th>
                <th scope="col">{t('schemaCompare.column.name')}</th>
                <th scope="col">{t('schemaCompare.column.breaking')}</th>
              </tr>
            </thead>
            <tbody>
              {comparison.differences.map((difference, position) => (
                <tr
                  key={position}
                  data-testid="schema-compare-difference"
                  data-kind={difference.kind}
                >
                  <th scope="row">
                    {t(`schemaCompare.kinds.${difference.kind}`, { defaultValue: difference.kind })}
                  </th>
                  <td className={designer.code}>{visible(difference.table)}</td>
                  <td className={designer.code}>
                    {difference.column === null ? '' : visible(difference.column)}
                  </td>
                  <td className={designer.code}>
                    {difference.name === null ? '' : visible(difference.name)}
                  </td>
                  <td>{difference.breaking ? t('schemaDesigner.yes') : t('schemaDesigner.no')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {plan !== null ? (
        <section className={designer.table} data-testid="schema-compare-plan-result">
          <h4 className={designer.tableName}>{t('schemaCompare.planTitle')}</h4>
          {plan.status === 'refused' ? (
            <div className={connectionStyles.warning} data-testid="schema-compare-plan-refused">
              <p>{problemText(t, plan.problem)}</p>
              <ul className={connectionStyles.issues}>
                {(plan.problem.issues ?? [])
                  .filter((issue) => issue.pointer.startsWith('compare.'))
                  .map((issue) => (
                    <li key={issue.code}>{issueText(t, issue)}</li>
                  ))}
              </ul>
              <PlanIssues problem={plan.problem} />
            </div>
          ) : (
            <>
              <p className={connectionStyles.status}>
                {plan.steps.length === 0
                  ? t('schemaCompare.planEmpty')
                  : t('schemaCompare.planSteps', { count: plan.steps.length })}{' '}
                {t('schemaCompare.planNotRun')}
              </p>
              <dl className={connectionStyles.details}>
                <dt>{t('schemaDesigner.sourceFingerprint')}</dt>
                <dd className={designer.hash}>{plan.sourceFingerprint}</dd>
                <dt>{t('schemaDesigner.targetFingerprint')}</dt>
                <dd className={designer.hash}>{plan.targetFingerprint}</dd>
                <dt>{t('schemaDesigner.serverVersion')}</dt>
                <dd>{plan.serverVersion.join('.')}</dd>
              </dl>
              <PlanSteps steps={plan.steps} />
              <Warnings warnings={plan.warnings} lockTimeoutS={LOCK_TIMEOUT_S} />
            </>
          )}
        </section>
      ) : null}
    </section>
  );
}
