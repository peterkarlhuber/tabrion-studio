/**
 * The diagnostics view (STU-014, 03 sections 2 G and 6, 08 section 4.9): tabrion's findings and Studio's `STU-DX-*`
 * for a chosen profile. The diagnosis only reads; nothing on this page writes to a database (STU-026 stays locked).
 *
 * - **Run:** "Run diagnosis" opens the profile's read-only connection (`POST /api/connections`; D-02 first for a PROD
 *   profile) and runs `POST /api/connections/{id}/diagnostics`. It starts only on a click, since SQLite's integrity
 *   check can take a while. "Run again" uses the shown connection and never opens one, so D-02 cannot be skipped.
 * - **Cancel:** stops the running diagnosis (`.../diagnostics/cancel`); the answer then names the checks that did not
 *   run (`STU-DX-008`), and the page says that the result is incomplete. A profile switch or leaving the page cancels
 *   a running diagnosis too, and an answer that arrives after a newer request or a profile switch is dropped.
 * - **Findings:** sorted by severity, each with its severity (icon and word, never colour alone), code, title, detail,
 *   remedy and source. Titles of Studio's own codes are the page's en/de text; every other text is the server's
 *   English text, escaped and capped (`lib/visible.ts`, U-1), because tabrion's details name database objects.
 */
import { CircleQuestionMark, CircleX, Info, TriangleAlert } from 'lucide-react';
import type { TFunction } from 'i18next';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import {
  cancelDiagnostics,
  countFindings,
  runDiagnostics,
  type Diagnosis,
  type Finding,
  type Severity,
} from '../../api/diagnostics';
import { openConnection } from '../../api/migrations';
import type { Problem } from '../../api/problem';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import { ProdConnectDialog } from '../../components/dialogs/StudioDialogs';
import { EnvBadge } from '../../components/status/Status';
import { Button } from '../../components/ui/Button';
import clsx from '../../lib/clsx';
import { capped, visible } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { problemText } from '../connections/messages';
import viewStyles from '../Views.module.css';
import styles from './Diagnostics.module.css';

/** 03 section 5.3: production profiles get a 15 s statement timeout (D-02 names it). */
const PROD_TIMEOUT_S = 15;
/** How many findings the page lists before "and N more" (a hostile or broken server could send thousands). */
export const MAX_FINDINGS = 100;
/** The longest code, title and text shown; a longer one is cut with a visible "…" (U-1c). */
export const MAX_CODE_LENGTH = 32;
export const MAX_TITLE_LENGTH = 160;
export const MAX_TEXT_LENGTH = 1000;

/** Studio's own finding codes: their titles are the page's en/de text (`diagnostics.titles.*`). */
const STUDIO_CODES = new Set(
  Array.from({ length: 10 }, (_, index) => `STU-DX-${String(index + 1).padStart(3, '0')}`),
);
/** "Not every check ran": the result is incomplete. */
const INCOMPLETE = 'STU-DX-008';

const ICONS: Record<Severity, ReactNode> = {
  error: <CircleX />,
  warning: <TriangleAlert />,
  info: <Info />,
  unknown: <CircleQuestionMark />,
};

type Phase = 'idle' | 'opening' | 'running' | 'canceling';

/** `capped(value, limit)` as one string, with "…" when it was cut. */
function cut(value: string, limit: number): string {
  const [shown, wasCut] = capped(value, limit);
  return wasCut ? `${shown}…` : shown;
}

function summaryText(t: TFunction, findings: Finding[]): string {
  if (findings.length === 0) return t('diagnostics.result.none');
  const counts = countFindings(findings);
  return (['error', 'warning', 'info', 'unknown'] as const)
    .filter((severity) => counts[severity] > 0)
    .map((severity) => t(`diagnostics.count.${severity}`, { count: counts[severity] }))
    .join(' · ');
}

function FindingItem({ finding, index }: { finding: Finding; index: number }) {
  const { t } = useTranslation();
  const titleId = useId();
  const own = finding.source === 'studio' && STUDIO_CODES.has(finding.code);
  return (
    <li
      className={clsx(styles.finding, styles[finding.severity])}
      aria-labelledby={titleId}
      data-testid="diagnostics-finding"
      data-severity={finding.severity}
      data-index={index}
    >
      <p className={styles.findingHead}>
        <span className={styles.severity} data-testid="diagnostics-severity">
          <span className={styles.icon} aria-hidden="true">
            {ICONS[finding.severity]}
          </span>
          {t(`diagnostics.severity.${finding.severity}`)}
        </span>
        <code className={styles.code} data-testid="diagnostics-code">
          {cut(finding.code, MAX_CODE_LENGTH)}
        </code>
        <span className={styles.source}>{t(`diagnostics.source.${finding.source}`)}</span>
      </p>
      <h3 id={titleId} className={styles.findingTitle} lang={own ? undefined : 'en'}>
        {own
          ? t(`diagnostics.titles.${finding.code}`)
          : cut(finding.title, MAX_TITLE_LENGTH) || t('diagnostics.result.noTitle')}
      </h3>
      {finding.detail !== '' ? (
        <p className={styles.text} lang="en" data-testid="diagnostics-detail">
          {cut(finding.detail, MAX_TEXT_LENGTH)}
        </p>
      ) : null}
      {finding.remedy !== '' ? (
        <p className={styles.text} data-testid="diagnostics-remedy">
          <span className={styles.remedyLabel}>{t('diagnostics.result.remedy')}</span>{' '}
          <span lang="en">{cut(finding.remedy, MAX_TEXT_LENGTH)}</span>
        </p>
      ) : null}
    </li>
  );
}

function DiagnosisResult({
  diagnosis,
  name,
  environment,
}: {
  diagnosis: Diagnosis;
  name: string;
  environment: ProfileItem['environment'];
}) {
  const { t, i18n } = useTranslation();
  const listed = diagnosis.findings.slice(0, MAX_FINDINGS);
  const incomplete = diagnosis.findings.some((finding) => finding.code === INCOMPLETE);
  const time =
    diagnosis.ranAt === null
      ? null
      : new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'medium' }).format(
          diagnosis.ranAt,
        );
  return (
    <div className={styles.result} data-testid="diagnostics-diagnosis">
      <p className={styles.resultHead}>
        <EnvBadge environment={environment} size="sm" />
        <span className={styles.resultName}>{cut(name, 64)}</span>
        {time !== null && diagnosis.ranAt !== null ? (
          <span className={connectionStyles.hint}>
            {t('diagnostics.result.ranAt')}{' '}
            <time dateTime={new Date(diagnosis.ranAt).toISOString()}>{time}</time>
          </span>
        ) : null}
      </p>
      <p
        className={
          diagnosis.findings.length === 0 ? connectionStyles.status : connectionStyles.warning
        }
        data-testid="diagnostics-summary"
      >
        {summaryText(t, diagnosis.findings)}
      </p>
      {incomplete ? (
        <p className={connectionStyles.warning} data-testid="diagnostics-incomplete">
          {t('diagnostics.result.incomplete')}
        </p>
      ) : null}
      {listed.length > 0 ? (
        <>
          <p className={connectionStyles.hint}>{t('diagnostics.result.serverText')}</p>
          <ul className={styles.findings} data-testid="diagnostics-findings">
            {listed.map((finding, index) => (
              <FindingItem key={index} finding={finding} index={index} />
            ))}
          </ul>
        </>
      ) : null}
      {diagnosis.findings.length > listed.length ? (
        <p className={connectionStyles.hint} data-testid="diagnostics-more">
          {t('diagnostics.result.more', { count: diagnosis.findings.length - listed.length })}
        </p>
      ) : null}
    </div>
  );
}

/** The profile a shown diagnosis belongs to (its name and environment are kept: the profile list may change). */
interface Shown {
  diagnosis: Diagnosis;
  /** The connection the page ran it on ("Run again" uses it, not the id in the answer). */
  connectionId: string;
  name: string;
  environment: ProfileItem['environment'];
}

export function DiagnosticsView({ fetcher = fetch }: { fetcher?: typeof fetch }) {
  const { t } = useTranslation();
  const selectId = useId();
  const [profiles, setProfiles] = useState<ProfileItem[] | null>(null);
  const [selected, setSelected] = useState('');
  const [shown, setShown] = useState<Shown | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [dialog, setDialog] = useState(false);
  const [message, setMessage] = useState('');
  /** The finished message repeats the summary below it: then it is only announced, not shown twice. */
  const [quiet, setQuiet] = useState(false);
  /** Counts runs; an answer is shown only if no newer run or profile switch came since. */
  const request = useRef(0);
  /** The connection whose diagnosis is running, so that a cancel, a profile switch or leaving the page can stop it. */
  const running = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    void fetchProfiles(fetcher).then((list) => {
      if (!active) return;
      const items = list?.profiles ?? [];
      setProfiles(items);
      setSelected((current) => current || (items[0]?.id ?? ''));
    });
    return () => {
      active = false;
    };
  }, [fetcher]);

  // Leaving the page stops a running diagnosis; its answer is dropped.
  useEffect(
    () => () => {
      request.current += 1;
      const connection = running.current;
      running.current = null;
      if (connection !== null) void cancelDiagnostics(fetcher, connection);
    },
    [fetcher],
  );

  const profile = profiles?.find((item) => item.id === selected) ?? null;
  const busy = phase !== 'idle';

  /** Run the diagnosis on an open connection, for the request `mine`. */
  const diagnose = async (
    mine: number,
    connectionId: string,
    target: Omit<Shown, 'diagnosis' | 'connectionId'>,
  ): Promise<void> => {
    running.current = connectionId;
    setPhase('running');
    const result = await runDiagnostics(fetcher, connectionId);
    if (running.current === connectionId && mine === request.current) running.current = null;
    if (mine !== request.current) return;
    setPhase('idle');
    if (result.ok) {
      // PROD wins if either side says so (fail closed); else the server's word, else the profile's.
      const environment =
        target.environment === 'prod' || result.value.environment === 'prod'
          ? 'prod'
          : (result.value.environment ?? target.environment);
      setShown({ diagnosis: result.value, connectionId, name: target.name, environment });
      setMessage(t('diagnostics.finished', { summary: summaryText(t, result.value.findings) }));
      setQuiet(true);
    } else {
      setProblem(result.problem);
      setMessage('');
    }
  };

  const run = async () => {
    if (profile === null) return;
    const target = { name: profile.name, environment: profile.environment };
    const mine = ++request.current;
    setPhase('opening');
    setProblem(null);
    setShown(null);
    setMessage(t('diagnostics.opening'));
    setQuiet(false);
    const opened = await openConnection(fetcher, profile.id);
    if (mine !== request.current) return;
    if (!opened.ok) {
      setPhase('idle');
      setProblem(opened.problem);
      setMessage('');
      return;
    }
    setMessage(t('diagnostics.runningNote'));
    await diagnose(mine, opened.value, target);
  };

  /** Run again on the shown diagnosis's connection: never opens one, so D-02 cannot be skipped. */
  const rerun = async () => {
    if (shown === null) return;
    const mine = ++request.current;
    const target = { name: shown.name, environment: shown.environment };
    const connectionId = shown.connectionId;
    setProblem(null);
    setShown(null);
    setMessage(t('diagnostics.runningNote'));
    setQuiet(false);
    await diagnose(mine, connectionId, target);
  };

  const cancel = async () => {
    if (phase === 'opening') {
      // The diagnosis has not started: drop the request; the connection stays open for later.
      request.current += 1;
      setPhase('idle');
      setMessage(t('diagnostics.canceledBeforeStart'));
      return;
    }
    const connection = running.current;
    if (phase !== 'running' || connection === null) return;
    setPhase('canceling');
    setMessage(t('diagnostics.canceling'));
    // The running diagnosis answers with what it found so far and STU-DX-008.
    await cancelDiagnostics(fetcher, connection);
  };

  const switchProfile = (id: string) => {
    request.current += 1;
    const connection = running.current;
    running.current = null;
    if (connection !== null) void cancelDiagnostics(fetcher, connection);
    setPhase('idle');
    setQuiet(false);
    setSelected(id);
    setShown(null);
    setProblem(null);
    setMessage('');
  };

  let runLabel = t('diagnostics.run');
  if (phase === 'opening') runLabel = t('diagnostics.opening');
  else if (phase === 'running') runLabel = t('diagnostics.running');
  else if (phase === 'canceling') runLabel = t('diagnostics.canceling');

  return (
    <div className={viewStyles.page}>
      <div className={viewStyles.pageHeader}>
        <h1 className={viewStyles.title}>{t('diagnostics.title')}</h1>
        <p className={viewStyles.lead}>{t('diagnostics.lead')}</p>
      </div>
      <section
        className={viewStyles.card}
        aria-labelledby="diagnostics-run"
        data-testid="diagnostics-card"
      >
        <h2 id="diagnostics-run" className={viewStyles.cardTitle}>
          {t('diagnostics.cardTitle')}
        </h2>
        {profiles !== null && profiles.length === 0 ? (
          <p className={connectionStyles.hint}>{t('diagnostics.noProfiles')}</p>
        ) : (
          <div className={styles.picker}>
            <label htmlFor={selectId} className={connectionStyles.label}>
              {t('diagnostics.profile')}
            </label>
            <select
              id={selectId}
              className={connectionStyles.input}
              value={selected}
              disabled={profiles === null}
              onChange={(event) => switchProfile(event.target.value)}
            >
              {(profiles ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {`${visible(item.name)} (${item.environment.toUpperCase()})`}
                </option>
              ))}
            </select>
            <Button
              variant="primary"
              disabled={profile === null || busy}
              onClick={() => (profile?.environment === 'prod' ? setDialog(true) : void run())}
            >
              {runLabel}
            </Button>
            {busy ? (
              <Button disabled={phase === 'canceling'} onClick={() => void cancel()}>
                {t('diagnostics.cancel')}
              </Button>
            ) : null}
            {!busy && shown !== null ? (
              <Button onClick={() => void rerun()}>{t('diagnostics.rerun')}</Button>
            ) : null}
          </div>
        )}
        <p
          role="status"
          className={quiet ? 'tb-sr-only' : styles.message}
          data-testid="diagnostics-message"
        >
          {message}
        </p>
        {problem !== null ? (
          <p role="alert" className={connectionStyles.problem} data-testid="diagnostics-problem">
            {problemText(t, problem)}
          </p>
        ) : null}
        <div aria-busy={busy} data-testid="diagnostics-result">
          {shown !== null ? (
            <DiagnosisResult
              diagnosis={shown.diagnosis}
              name={shown.name}
              environment={shown.environment}
            />
          ) : null}
        </div>
      </section>
      {dialog && profile !== null ? (
        <ProdConnectDialog
          open
          name={visible(profile.name)}
          timeoutSeconds={PROD_TIMEOUT_S}
          onCancel={() => setDialog(false)}
          onConnect={() => {
            setDialog(false);
            void run();
          }}
        />
      ) : null}
    </div>
  );
}
