/**
 * The diagnostics view (STU-014, 03 sections 2 G and 6, 08 section 4.9): tabrion's findings and Studio's `STU-DX-*`
 * for a chosen profile. The diagnosis only reads; nothing on this page writes to a database (STU-026 stays locked).
 *
 * - **Run:** "Run diagnosis" opens the profile's read-only connection (`POST /api/connections`) and runs
 *   `POST /api/connections/{id}/diagnostics`. A PROD profile asks D-02 first unless it was confirmed in this page
 *   session: the shared gate of the connection manager (`useConnect`, STU-008), so one confirmation counts
 *   everywhere. It starts only on a click, since SQLite's integrity check can take a while. "Run again" uses the shown
 *   connection and never opens one, so D-02 cannot be skipped.
 * - **Cancel:** stops the running diagnosis (`.../diagnostics/cancel`); the answer then names the checks that did not
 *   run (`STU-DX-008`), and the page says that the result is incomplete. A profile switch or leaving the page cancels
 *   a running diagnosis too, and an answer that arrives after a newer request or a profile switch is dropped.
 * - **Findings:** sorted by severity, each with its severity (icon and word, never colour alone), code, title, detail,
 *   remedy and source. Titles of Studio's own codes are the page's en/de text, and so are their details and remedies,
 *   worded from the finding's `params` (TD-032, `studioText.ts`). tabrion's texts (and Studio's when the params do
 *   not fit) are the server's English text, marked `lang="en"`, escaped and capped (`lib/visible.ts`, U-1), because
 *   tabrion's details name database objects.
 * - **Backups (STU-025, TD-036):** the answer's backup hints (`STU-BK-*`) in their own list below the findings, shown
 *   like findings with en/de titles, details and remedies; they are not counted in the summary. With the hints on the page, the route lets
 *   tabrion's `TAB-PG-020` give way to `STU-BK-010` (one cause, one entry).
 * - **Report (STU-025, TD-036):** "Download Markdown report" and "Download JSON report" post
 *   `.../diagnostics/report` on the shown connection (never a new one, so D-02 cannot be skipped). The server runs the
 *   diagnosis again and answers a file without secrets, which the browser saves under the server's name. Cancel, a
 *   profile switch or leaving the page stops it like a run; a refusal (`studio.report_unavailable`) is the page's text.
 */
import { CircleQuestionMark, CircleX, Info, TriangleAlert } from 'lucide-react';
import type { TFunction } from 'i18next';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import {
  cancelDiagnostics,
  countFindings,
  downloadReport,
  runDiagnostics,
  type Backup,
  type Diagnosis,
  type Finding,
  type ReportFormat,
  type Severity,
} from '../../api/diagnostics';
import { openConnection } from '../../api/migrations';
import type { Problem } from '../../api/problem';
import { PROD_CONFIRMATION_REQUIRED } from '../../api/connections';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import { EnvBadge } from '../../components/status/Status';
import { Button } from '../../components/ui/Button';
import clsx from '../../lib/clsx';
import { saveFile, type SaveFile } from '../../lib/download';
import { capped, visible } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { problemText } from '../connections/messages';
import { useConnect } from '../connections/useConnect';
import { useReconnected } from '../../workspace';
import { studioText } from './studioText';
import viewStyles from '../Views.module.css';
import styles from './Diagnostics.module.css';

/** How many findings the page lists before "and N more" (a hostile or broken server could send thousands). */
export const MAX_FINDINGS = 100;
/** The longest code, title and text shown; a longer one is cut with a visible "…" (U-1c). */
export const MAX_CODE_LENGTH = 32;
export const MAX_TITLE_LENGTH = 160;
export const MAX_TEXT_LENGTH = 1000;
/** How many backup hints the page lists (the server gives a handful; a broken one could send more). */
export const MAX_HINTS = 20;

/** Studio's own codes: their titles are the page's en/de text (`diagnostics.titles.*`). */
const STUDIO_CODES = new Set([
  ...Array.from({ length: 10 }, (_, index) => `STU-DX-${String(index + 1).padStart(3, '0')}`),
  // The backup hints of STU-025.
  ...['001', '002', '003', '004', '005', '010', '011', '012', '013', '014'].map(
    (number) => `STU-BK-${number}`,
  ),
]);
/** "Not every check ran": the result is incomplete. */
const INCOMPLETE = 'STU-DX-008';

const ICONS: Record<Severity, ReactNode> = {
  error: <CircleX />,
  warning: <TriangleAlert />,
  info: <Info />,
  unknown: <CircleQuestionMark />,
};

type Phase = 'idle' | 'opening' | 'running' | 'canceling' | 'reporting';

/** Whether the page words this finding's detail and remedy itself (Studio's own code with fitting params). */
function isWorded(t: TFunction, finding: Finding): boolean {
  return (
    finding.source === 'studio' && STUDIO_CODES.has(finding.code) && studioText(t, finding) !== null
  );
}

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

function FindingItem({
  finding,
  index,
  hint = false,
}: {
  finding: Finding;
  index: number;
  /** A backup hint: listed under the "Backups" heading, so its title is one level lower. */
  hint?: boolean;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const own = finding.source === 'studio' && STUDIO_CODES.has(finding.code);
  // TD-032: Studio's details and remedies in en/de from their params; else the server's English text.
  const worded = own ? studioText(t, finding) : null;
  const Title = hint ? 'h4' : 'h3';
  return (
    <li
      className={clsx(styles.finding, styles[finding.severity])}
      aria-labelledby={titleId}
      data-testid={hint ? 'diagnostics-hint' : 'diagnostics-finding'}
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
      <Title id={titleId} className={styles.findingTitle} lang={own ? undefined : 'en'}>
        {own
          ? t(`diagnostics.titles.${finding.code}`)
          : cut(finding.title, MAX_TITLE_LENGTH) || t('diagnostics.result.noTitle')}
      </Title>
      {worded !== null ? (
        <>
          <p className={styles.text} data-testid="diagnostics-detail">
            {worded.detail}
          </p>
          <p className={styles.text} data-testid="diagnostics-remedy">
            <span className={styles.remedyLabel}>{t('diagnostics.result.remedy')}</span>{' '}
            <span>{worded.remedy}</span>
          </p>
        </>
      ) : (
        <>
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
        </>
      )}
    </li>
  );
}

/** The backup hints (STU-025): not findings, so they have their own heading and are not in the summary. */
function BackupHints({ backup, incomplete }: { backup: Backup; incomplete: boolean }) {
  const { t } = useTranslation();
  const listed = backup.hints.slice(0, MAX_HINTS);
  let state: ReactNode = null;
  if (backup.invalid) {
    state = (
      <p className={connectionStyles.warning} data-testid="diagnostics-backup-invalid">
        {t('diagnostics.backup.invalid')}
      </p>
    );
  } else if (listed.length === 0 && (incomplete || !backup.checked)) {
    // L-1 of the TD-036 review: no hints after a cancel or the time limit (or without a file to look next to) is no
    // sign that the backups are fine, so the page says that they were not checked.
    state = (
      <p className={connectionStyles.warning} data-testid="diagnostics-backup-unchecked">
        {t('diagnostics.backup.unchecked')}
      </p>
    );
  } else if (listed.length === 0) {
    state = (
      <p className={connectionStyles.status} data-testid="diagnostics-backup-none">
        {t('diagnostics.backup.none')}
      </p>
    );
  }
  return (
    <section
      className={styles.backup}
      aria-labelledby="diagnostics-backup"
      data-testid="diagnostics-backup"
    >
      <h3 id="diagnostics-backup" className={styles.backupTitle}>
        {t('diagnostics.backup.title')}
      </h3>
      <p className={connectionStyles.hint}>{t('diagnostics.backup.lead')}</p>
      {state}
      {listed.length > 0 ? (
        <ul className={styles.findings} data-testid="diagnostics-hints">
          {listed.map((hint, index) => (
            <FindingItem key={index} finding={hint} index={index} hint />
          ))}
        </ul>
      ) : null}
      {backup.hints.length > listed.length ? (
        <p className={connectionStyles.hint} data-testid="diagnostics-hints-more">
          {t('diagnostics.backup.more', { count: backup.hints.length - listed.length })}
        </p>
      ) : null}
    </section>
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
          {listed.some((finding) => !isWorded(t, finding)) ? (
            <p className={connectionStyles.hint} data-testid="diagnostics-server-text">
              {t('diagnostics.result.serverText')}
            </p>
          ) : null}
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
      {diagnosis.backup !== null ? (
        <BackupHints backup={diagnosis.backup} incomplete={incomplete} />
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

export function DiagnosticsView({
  fetcher = fetch,
  save = saveFile,
}: {
  fetcher?: typeof fetch;
  /** Where a downloaded report goes (the browser's download; tests pass their own). */
  save?: SaveFile;
}) {
  const { t } = useTranslation();
  const selectId = useId();
  const noteId = useId();
  const gate = useConnect(fetcher);
  const [profiles, setProfiles] = useState<ProfileItem[] | null>(null);
  const [selected, setSelected] = useState('');
  const [shown, setShownState] = useState<Shown | null>(null);
  /** The shown diagnosis as of now, for a click that lands before the next render (N-1 of the TD-036 review). */
  const shownNow = useRef<Shown | null>(null);
  const setShown = (next: Shown | null) => {
    shownNow.current = next;
    setShownState(next);
  };
  // TD-034: "Reconnect" in the banner replaced the diagnosis's connection; "Run again" and the report use the new one.
  useReconnected((event) => {
    const current = shownNow.current;
    if (current?.connectionId === event.from) setShown({ ...current, connectionId: event.to });
  });
  const [problem, setProblem] = useState<Problem | null>(null);
  const [phase, setPhaseState] = useState<Phase>('idle');
  /** The phase as of now, for a click that lands before the next render (review I-5). */
  const phaseNow = useRef<Phase>('idle');
  const setPhase = (next: Phase) => {
    phaseNow.current = next;
    setPhaseState(next);
  };
  const [message, setMessage] = useState('');
  /** The finished message repeats the summary below it: then it is only announced, not shown twice. */
  const [quiet, setQuiet] = useState(false);
  /** Counts runs; an answer is shown only if no newer run or profile switch came since. */
  const request = useRef(0);
  /** The connection whose diagnosis is running, so that a cancel, a profile switch or leaving the page can stop it. */
  const running = useRef<string | null>(null);
  /** Aborts the report request in flight (N-2 of the TD-036 review): a dropped report is not read to its end. */
  const reportAbort = useRef<AbortController | null>(null);
  const abortReport = () => {
    reportAbort.current?.abort();
    reportAbort.current = null;
  };

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
      abortReport();
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
    // The environment this page confirmed for (D-02 on the server, TD-038).
    const opened = await openConnection(fetcher, profile.id, profile.environment);
    if (mine !== request.current) return;
    if (!opened.ok) {
      setPhase('idle');
      setProblem(opened.problem);
      setMessage('');
      // The profile became PROD after this page read it: read the profiles again, so the next run asks D-02.
      if (opened.problem.code === PROD_CONFIRMATION_REQUIRED) {
        void fetchProfiles(fetcher).then((list) => {
          if (list !== null) setProfiles(list.profiles);
        });
      }
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

  /** The report of the shown diagnosis's connection: never opens one, so D-02 cannot be skipped. */
  const report = async (format: ReportFormat) => {
    // The phase and the diagnosis as of now, never the render's: a second press before the next render starts nothing
    // (N-1 of the TD-036 review, like Cancel in review I-5).
    const current = shownNow.current;
    if (current === null || phaseNow.current !== 'idle') return;
    const mine = ++request.current;
    const connectionId = current.connectionId;
    running.current = connectionId;
    const abort = new AbortController();
    reportAbort.current = abort;
    setPhase('reporting');
    setProblem(null);
    setMessage(t('diagnostics.download.making'));
    setQuiet(false);
    const result = await downloadReport(fetcher, connectionId, format, abort.signal);
    if (reportAbort.current === abort) reportAbort.current = null;
    if (running.current === connectionId && mine === request.current) running.current = null;
    if (mine !== request.current) return;
    setPhase('idle');
    if (result.ok) {
      save(result.value.blob, result.value.filename);
      setMessage(t('diagnostics.download.done', { filename: result.value.filename }));
    } else {
      setProblem(result.problem);
      setMessage('');
    }
  };

  const cancel = async () => {
    if (phaseNow.current === 'reporting') {
      // The report is dropped, not saved: its answer would be a diagnosis cut short by the cancel. The request is
      // aborted, and the server's diagnosis is stopped through the cancel route (N-2 of the TD-036 review).
      const mine = ++request.current;
      abortReport();
      const connection = running.current;
      running.current = null;
      setPhase('canceling');
      setMessage(t('diagnostics.canceling'));
      const canceled = connection !== null && (await cancelDiagnostics(fetcher, connection));
      if (mine !== request.current) return;
      setPhase('idle');
      // Only a cancel the server confirmed is "canceled": one that lands before the server registered the report's
      // diagnosis (it reads the names first) does not stop it, and the page says so.
      setMessage(
        t(canceled ? 'diagnostics.download.canceled' : 'diagnostics.download.cancelNotTaken'),
      );
      return;
    }
    if (phaseNow.current === 'opening') {
      // The diagnosis has not started: drop the request; the connection stays open for later.
      request.current += 1;
      setPhase('idle');
      setMessage(t('diagnostics.canceledBeforeStart'));
      return;
    }
    const connection = running.current;
    if (phaseNow.current !== 'running' || connection === null) return;
    const mine = request.current;
    setPhase('canceling');
    setMessage(t('diagnostics.canceling'));
    // The running diagnosis answers with what it found so far and STU-DX-008.
    const canceled = await cancelDiagnostics(fetcher, connection);
    // L-1: a cancel that did not take (refused, failed, or no run registered yet) says so and can be tried again.
    if (!canceled && mine === request.current && running.current === connection) {
      setPhase('running');
      setMessage(t('diagnostics.cancelFailed'));
    }
  };

  const switchProfile = (id: string) => {
    request.current += 1;
    abortReport();
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

  const canRun = profile !== null && !busy;
  const canCancel = phase === 'opening' || phase === 'running' || phase === 'reporting';
  const canRerun = !busy && shown !== null;
  const canReport = canRerun;
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
            {/* C-2: the buttons stay mounted and focusable (aria-disabled), so the keyboard focus never drops to
                the page when a run starts, ends or is canceled. */}
            <Button
              variant="primary"
              aria-disabled={!canRun}
              onClick={() => {
                if (!canRun || profile === null) return;
                gate.guard(profile, () => void run());
              }}
            >
              {runLabel}
            </Button>
            <Button aria-disabled={!canCancel} onClick={() => canCancel && void cancel()}>
              {t('diagnostics.cancel')}
            </Button>
            <Button aria-disabled={!canRerun} onClick={() => canRerun && void rerun()}>
              {t('diagnostics.rerun')}
            </Button>
          </div>
        )}
        {profiles !== null && profiles.length === 0 ? null : (
          <div className={styles.picker} data-testid="diagnostics-report">
            <Button
              aria-disabled={!canReport}
              aria-describedby={noteId}
              onClick={() => void report('markdown')}
            >
              {t('diagnostics.download.markdown')}
            </Button>
            <Button
              aria-disabled={!canReport}
              aria-describedby={noteId}
              onClick={() => void report('json')}
            >
              {t('diagnostics.download.json')}
            </Button>
            <p id={noteId} className={clsx(connectionStyles.hint, styles.note)}>
              {t('diagnostics.download.note')}
            </p>
          </div>
        )}
        <p
          role="status"
          className={quiet || message === '' ? 'tb-sr-only' : styles.message}
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
      {gate.dialog}
    </div>
  );
}
