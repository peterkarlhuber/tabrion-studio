/** The views of the shell (STU-005): start, settings, not found; the connection manager is in connections/ (STU-008). */
import { FolderOpen } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Trans, useTranslation } from 'react-i18next';

import type { Problem } from '../api/problem';
import {
  fetchSettings,
  saveSettings,
  type ServerSettings,
  type SettingsState,
} from '../api/profiles';
import { Button } from '../components/ui/Button';
import { EmptyState } from '../components/ui/EmptyState';
import { MAX_ENTRIES, visible } from '../lib/visible';
import connectionStyles from './connections/Connections.module.css';
import { Field } from './connections/Field';
import { issuesByField, problemText } from './connections/messages';
import {
  appearanceSaveCount,
  chooseDensity,
  chooseLanguage,
  chooseTheme,
  useAppearanceSaves,
  useAppearanceSync,
} from '../appearance';
import { currentLocale, SUPPORTED_LOCALES } from '../i18n';
import { navigate } from '../router';
import { useTheme, type Density, type ThemePreference } from '../theme';
import styles from './Views.module.css';

/** Inline code in translations (`<code>tabrion-studio</code>`); rendered as elements, never as HTML. */
export const TRANS_COMPONENTS = { code: <code /> };

/** The lockup that matches the surface: black wordmark on light, white wordmark on dark (no CSS filters). */
export function Lockup({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { theme } = useTheme();
  const src =
    theme === 'dark'
      ? '/branding/tabrion-studio-logo-on-dark.png'
      : '/branding/tabrion-studio-logo.png';
  return <img src={src} alt={t('start.logoAlt')} width={320} height={42} className={className} />;
}

function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={styles.pageHeader}>
      <h1 className={styles.title}>{title}</h1>
      {children}
    </div>
  );
}

/** The start page's next steps (08 section 4.1): link names differ from the activity bar's, so each name is one link. */
const START_STEPS = [
  { key: 'connections', path: '/connections' },
  { key: 'explorer', path: '/explorer' },
  { key: 'sql', path: '/sql' },
  { key: 'migrations', path: '/migrations' },
  { key: 'diagnostics', path: '/diagnostics' },
] as const;

export function StartView() {
  const { t } = useTranslation();
  return (
    <div className={styles.page}>
      <div className={styles.hero}>
        <Lockup className={styles.lockup} />
        <h1 className={styles.title}>{t('start.title')}</h1>
        <p className={styles.lead}>{t('start.lead')}</p>
      </div>
      <EmptyState
        icon={<FolderOpen />}
        title={t('start.projectsTitle')}
        description={
          <>
            <p>
              <Trans i18nKey="start.projectsBody" components={TRANS_COMPONENTS} />
            </p>
            <ul className={styles.steps} data-testid="start-steps">
              {START_STEPS.map(({ key, path }) => (
                <li key={key}>
                  <a
                    href={path}
                    onClick={(event) => {
                      // Like the activity bar: a modifier or middle click opens the link the browser's way.
                      if (
                        event.button !== 0 ||
                        event.metaKey ||
                        event.ctrlKey ||
                        event.shiftKey ||
                        event.altKey
                      )
                        return;
                      event.preventDefault();
                      navigate(path);
                    }}
                  >
                    {t(`start.steps.${key}.link`)}
                  </a>{' '}
                  <span className={styles.stepHint}>{t(`start.steps.${key}.hint`)}</span>
                </li>
              ))}
            </ul>
          </>
        }
      />
    </div>
  );
}

function Choice<T extends string>({
  name,
  legend,
  value,
  options,
  onChange,
}: {
  name: string;
  legend: string;
  value: T;
  options: { value: T; label: string; lang?: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className={styles.fieldset}>
      <legend className={styles.legend}>{legend}</legend>
      <div className={styles.options}>
        {options.map((option) => (
          <label key={option.value} className={styles.option} lang={option.lang}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/**
 * Queries and security (08 section 4.10), saved in Studio's own `settings.toml` (`PUT /api/settings`, STU-008). The
 * limits take effect with the query engine (STU-011); SQL logging is an opt-in with a warning.
 */
function ServerSettingsForm({ fetcher, revision }: { fetcher: typeof fetch; revision: number }) {
  const { t } = useTranslation();
  const [state, setState] = useState<SettingsState | null | 'loading'>('loading');
  const [form, setForm] = useState<{ logSql: boolean; maxRows: string; timeout: string } | null>(
    null,
  );
  const [problem, setProblem] = useState<Problem | null>(null);
  const [saved, setSaved] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  // How many appearance choices had been saved when `state` (and so its digest) was read.
  const readAtSaves = useRef(0);

  const apply = (next: SettingsState | null) => {
    setState(next);
    if (next !== null) {
      setForm({
        logSql: next.settings.logSql,
        maxRows: String(next.settings.maxRows),
        timeout: String(next.settings.statementTimeoutMs / 1000),
      });
    }
  };

  useEffect(() => {
    let active = true;
    const saves = appearanceSaveCount();
    void fetchSettings(fetcher).then((next) => {
      if (!active) return;
      readAtSaves.current = saves;
      // After a saved appearance choice (revision) only the digest is new: keep what the user typed.
      if (revision === 0 || form === null) apply(next);
      else setState(next);
    });
    return () => {
      active = false;
    };
    // apply only sets state; form is read once per load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetcher, revision]);

  if (state === 'loading') return <p className={connectionStyles.hint}>{t('settings.loading')}</p>;
  if (state === null || form === null) {
    return <p className={connectionStyles.problem}>{t('settings.unavailable')}</p>;
  }
  const fields = issuesByField(problem);
  const handWritten = state.file.handWritten || overwrite;
  const errors = state.issues.filter((issue) => issue.severity !== 'warning');

  const save = async () => {
    const seconds = Number(form.timeout);
    const settings: ServerSettings = {
      logSql: form.logSql,
      maxRows: /^\d+$/.test(form.maxRows) ? Number(form.maxRows) : NaN,
      statementTimeoutMs: Number.isFinite(seconds) ? Math.round(seconds * 1000) : NaN,
    };
    const local = [
      ...(Number.isNaN(settings.maxRows)
        ? [{ pointer: 'settings.limits.max_rows', code: 'config.type' }]
        : []),
      ...(Number.isNaN(settings.statementTimeoutMs) || form.timeout.trim() === ''
        ? [{ pointer: 'settings.limits.statement_timeout_ms', code: 'config.type' }]
        : []),
    ];
    setSaved(false);
    if (local.length > 0) {
      setProblem({
        code: 'studio.settings_invalid',
        kind: 'config',
        status: 0,
        logId: null,
        issues: local,
      });
      return;
    }
    // M-1 of stu-052-td021-review.md: an appearance choice saved since the form read the file changed only its own
    // key. Read the new digest first; the values typed here are kept.
    let digest = state.file.digest;
    if (appearanceSaveCount() !== readAtSaves.current) {
      const saves = appearanceSaveCount();
      const next = await fetchSettings(fetcher);
      if (next !== null) {
        setState(next);
        readAtSaves.current = saves;
        digest = next.file.digest;
      }
    }
    const result = await saveSettings(fetcher, settings, digest, handWritten);
    if (!result.ok) {
      setProblem(result.problem);
      if (result.problem.code === 'studio.config_hand_written') setOverwrite(true);
      if (result.problem.code === 'studio.config_stale') {
        // Someone else changed the file: show its new digest, keep what was typed, let the user save again.
        const saves = appearanceSaveCount();
        const next = await fetchSettings(fetcher);
        if (next !== null) {
          setState(next);
          readAtSaves.current = saves;
        }
      }
      return;
    }
    setProblem(null);
    setSaved(true);
    apply(await fetchSettings(fetcher));
  };

  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      {errors.length > 0 ? (
        <div className={connectionStyles.warning} data-testid="settings-issues">
          <p>{t('settings.fileIssues')}</p>
          <ul className={connectionStyles.issues}>
            {errors.slice(0, MAX_ENTRIES).map((issue, index) => (
              <li key={index}>
                <code>{visible(String(issue.pointer))}</code>{' '}
                <code>{visible(String(issue.code))}</code>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <section className={styles.card} aria-labelledby="settings-queries">
        <h2 id="settings-queries" className={styles.cardTitle}>
          {t('settings.queries')}
        </h2>
        <Field label={t('settings.maxRows')} issues={fields['limits.max_rows']}>
          {(props) => (
            <input
              {...props}
              className={connectionStyles.input}
              inputMode="numeric"
              value={form.maxRows}
              onChange={(event) => setForm({ ...form, maxRows: event.target.value })}
            />
          )}
        </Field>
        <Field label={t('settings.timeout')} issues={fields['limits.statement_timeout_ms']}>
          {(props) => (
            <input
              {...props}
              className={connectionStyles.input}
              inputMode="decimal"
              value={form.timeout}
              onChange={(event) => setForm({ ...form, timeout: event.target.value })}
            />
          )}
        </Field>
      </section>
      <section className={styles.card} aria-labelledby="settings-security">
        <h2 id="settings-security" className={styles.cardTitle}>
          {t('settings.security')}
        </h2>
        <label className={connectionStyles.check}>
          <input
            type="checkbox"
            checked={form.logSql}
            aria-describedby="settings-log-sql-warning"
            onChange={(event) => setForm({ ...form, logSql: event.target.checked })}
          />
          {t('settings.logSql')}
        </label>
        <p id="settings-log-sql-warning" className={connectionStyles.warning}>
          {t('settings.logSqlWarning')}
        </p>
      </section>
      {handWritten ? <p className={connectionStyles.warning}>{t('settings.handWritten')}</p> : null}
      <div aria-live="polite">
        {problem !== null && problem.code !== 'studio.config_hand_written' ? (
          <p className={connectionStyles.problem} data-testid="settings-problem">
            {problemText(t, problem)}
          </p>
        ) : null}
        {saved ? (
          <p className={connectionStyles.status} data-testid="settings-saved">
            {t('settings.savedServer')}
          </p>
        ) : null}
      </div>
      <div>
        <Button type="submit" variant="primary">
          {t(handWritten ? 'settings.overwrite' : 'settings.save')}
        </Button>
      </div>
    </form>
  );
}

/** Where the general choices are kept (TD-021): settings.toml, or this browser only when the file cannot be changed. */
function AppearanceNote() {
  const { t } = useTranslation();
  const sync = useAppearanceSync();
  return (
    <p className={connectionStyles.hint} role="status" data-testid="appearance-sync">
      {sync.kind === 'local'
        ? t(`settings.generalLocal.${sync.reason}`)
        : t('settings.generalHint')}
    </p>
  );
}

export function SettingsView({ fetcher = fetch }: { fetcher?: typeof fetch }) {
  const { t } = useTranslation();
  const { preference, density } = useTheme();
  const revision = useAppearanceSaves();
  return (
    <div className={styles.page}>
      <PageHeader title={t('settings.title')}>
        <p className={styles.lead}>{t('settings.saved')}</p>
      </PageHeader>
      <section className={styles.card} aria-labelledby="settings-general">
        <h2 id="settings-general" className={styles.cardTitle}>
          {t('settings.general')}
        </h2>
        <AppearanceNote />
        <Choice
          name="language"
          legend={t('settings.language')}
          value={currentLocale()}
          options={SUPPORTED_LOCALES.map((locale) => ({
            value: locale,
            label: t(`languages.${locale}`),
            lang: locale,
          }))}
          onChange={chooseLanguage}
        />
        <Choice<ThemePreference>
          name="theme"
          legend={t('settings.theme')}
          value={preference}
          options={[
            { value: 'system', label: t('settings.themeSystem') },
            { value: 'light', label: t('settings.themeLight') },
            { value: 'dark', label: t('settings.themeDark') },
          ]}
          onChange={chooseTheme}
        />
        <Choice<Density>
          name="density"
          legend={t('settings.density')}
          value={density}
          options={[
            { value: 'compact', label: t('settings.densityCompact') },
            { value: 'normal', label: t('settings.densityNormal') },
          ]}
          onChange={chooseDensity}
        />
      </section>
      <ServerSettingsForm fetcher={fetcher} revision={revision} />
    </div>
  );
}

export function NotFoundView() {
  const { t } = useTranslation();
  return (
    <div className={styles.page}>
      <PageHeader title={t('notFound.title')} />
      <p>{t('notFound.body')}</p>
      <p>
        <a
          href="/"
          onClick={(event) => {
            event.preventDefault();
            navigate('/');
          }}
        >
          {t('notFound.home')}
        </a>
      </p>
    </div>
  );
}
