/**
 * The "New connection" assistant of 08 section 4.2 (STU-008): three steps in one dialog, progress on top.
 *
 * 1. Database: SQLite file or PostgreSQL (host, port, database, user, TLS mode).
 * 2. Sign-in: where the password comes from. The system keyring, an environment variable, a file, or this session
 *    only; the profile file holds only the reference, never the password (03 section 4.4).
 * 3. Name and environment: the name (fixed once saved), the environment (required, no default), the read-only role.
 *
 * Saving writes the user's own `profiles.toml` (`POST` or `PUT /api/profiles`); an entered password then goes once to
 * `PUT /api/profiles/{id}/secret`. The connection test of step 3 needs the connection registry (STU-009) and is shown
 * disabled until then. "Save in project" is not offered: Studio never writes the project's file.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { Problem, ProblemIssue } from '../../api/problem';
import {
  createProfile,
  storeSecret,
  updateProfile,
  type Engine,
  type EnvironmentTag,
  type ProfileFields,
  type ProfileItem,
} from '../../api/profiles';
import { Button } from '../../components/ui/Button';
import { Dialog, type DialogAction } from '../../components/ui/Dialog';
import { Field } from './Field';
import { issuesByField, problemText } from './messages';
import styles from './Connections.module.css';

export type SecretChoice = 'keyring' | 'env' | 'file' | 'session' | 'none';

const SSL_MODES = ['disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'];
const STEP_OF_FIELD: Record<string, number> = {
  path: 1,
  host: 1,
  port: 1,
  database: 1,
  user: 1,
  sslmode: 1,
  password: 2,
  name: 3,
  environment: 3,
  read_only_role: 3,
};

interface Form {
  engine: Engine;
  path: string;
  host: string;
  port: string;
  database: string;
  user: string;
  sslmode: string;
  source: SecretChoice;
  envName: string;
  filePath: string;
  password: string;
  name: string;
  environment: EnvironmentTag | '';
  readOnlyRole: boolean;
}

function initialForm(item: ProfileItem | null, keyring: boolean): Form {
  if (item === null) {
    return {
      engine: 'sqlite',
      path: '',
      host: '',
      port: '',
      database: '',
      user: '',
      sslmode: '',
      source: keyring ? 'keyring' : 'session',
      envName: '',
      filePath: '',
      password: '',
      name: '',
      environment: '',
      readOnlyRole: false,
    };
  }
  const ref = item.password;
  let source: SecretChoice = 'none';
  if (ref?.source === 'keyring') source = 'keyring';
  else if (ref?.source === 'env') source = 'env';
  else if (ref?.source === 'file') source = 'file';
  else if (item.secret.hasSecret === true) source = 'session';
  return {
    engine: item.engine,
    path: item.path ?? '',
    host: item.host ?? '',
    port: item.port === undefined ? '' : String(item.port),
    database: item.database ?? '',
    user: item.user ?? '',
    sslmode: item.sslmode ?? '',
    source,
    envName: ref?.source === 'env' ? ref.target : '',
    filePath: ref?.source === 'file' ? ref.target : '',
    password: '',
    name: item.name,
    environment: item.environment,
    readOnlyRole: item.read_only_role === true,
  };
}

function reference(form: Form): string | null {
  switch (form.source) {
    case 'keyring':
      return 'keyring:';
    case 'env':
      return `env:${form.envName}`;
    case 'file':
      return `file:${form.filePath}`;
    default:
      return null;
  }
}

/** The profile as the file writes it; a malformed port goes to the server as typed, which names the field. */
export function profileOf(form: Form, original: ProfileItem | null): ProfileFields {
  const profile: Record<string, unknown> = {
    name: form.name,
    engine: form.engine,
    environment: form.environment,
    password: form.engine === 'sqlite' && form.source === 'none' ? null : reference(form),
  };
  if (form.engine === 'sqlite') {
    profile.path = form.path;
  } else {
    profile.host = form.host;
    profile.database = form.database;
    if (form.port !== '') profile.port = /^\d+$/.test(form.port) ? Number(form.port) : form.port;
    if (form.user !== '') profile.user = form.user;
    if (form.sslmode !== '') profile.sslmode = form.sslmode;
  }
  if (form.readOnlyRole) profile.read_only_role = true;
  else if (original?.read_only_role !== undefined) profile.read_only_role = false;
  if (original?.statement_timeout_ms !== undefined && original.engine === form.engine) {
    profile.statement_timeout_ms = original.statement_timeout_ms;
  }
  return profile as unknown as ProfileFields;
}

function missing(form: Form, step: number): ProblemIssue[] {
  const required = (field: string, value: string): ProblemIssue[] =>
    value.trim() === '' ? [{ pointer: `profile.${field}`, code: 'config.required' }] : [];
  if (step === 1) {
    return form.engine === 'sqlite'
      ? required('path', form.path)
      : [...required('host', form.host), ...required('database', form.database)];
  }
  if (step === 2) {
    if (form.source === 'env') return required('password', form.envName);
    if (form.source === 'file') return required('password', form.filePath);
    return [];
  }
  return [...required('name', form.name), ...required('environment', form.environment)];
}

export interface WizardResult {
  name: string;
  /** Why an entered password was not stored, if it was not. */
  secretProblem: Problem | null;
}

export function ConnectionWizard({
  fetcher,
  item,
  digest,
  handWritten,
  keyringAvailable,
  onSaved,
  onStale,
  onCancel,
}: {
  fetcher: typeof fetch;
  /** The user profile to edit; null for a new one. */
  item: ProfileItem | null;
  digest: string | null;
  handWritten: boolean;
  keyringAvailable: boolean;
  onSaved: (result: WizardResult) => void;
  /** The file changed meanwhile: the owner reads the list (and its digest) again. */
  onStale: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState<Form>(() => initialForm(item, keyringAvailable));
  const [step, setStep] = useState(1);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const stepRef = useRef<HTMLDivElement>(null);
  const shownStep = useRef(1);
  const editing = item !== null;

  useEffect(() => {
    // A new step replaces the buttons that had the focus: move it to the step's first field. (Compared with the
    // step shown before, not "first render": StrictMode runs effects twice.)
    if (shownStep.current === step) return;
    shownStep.current = step;
    stepRef.current?.querySelector<HTMLElement>('input:not([disabled]), select')?.focus();
  }, [step]);

  const set = <K extends keyof Form>(key: K, value: Form[K]) =>
    setForm((current) => ({ ...current, [key]: value }));
  const fields = issuesByField(problem);

  const next = () => {
    const issues = missing(form, step);
    if (issues.length > 0) {
      setProblem({
        code: 'studio.profile_invalid',
        kind: 'config',
        status: 0,
        logId: null,
        issues,
      });
      return;
    }
    setProblem(null);
    setStep(step + 1);
  };

  const save = async () => {
    const issues = missing(form, 3);
    if (issues.length > 0) {
      setProblem({
        code: 'studio.profile_invalid',
        kind: 'config',
        status: 0,
        logId: null,
        issues,
      });
      return;
    }
    setBusy(true);
    const profile = profileOf(form, item);
    const confirmed = overwrite || handWritten;
    const result =
      item === null
        ? await createProfile(fetcher, profile, digest, confirmed)
        : await updateProfile(fetcher, item.id, profile, digest, confirmed);
    if (!result.ok) {
      setBusy(false);
      setProblem(result.problem);
      if (result.problem.code === 'studio.config_hand_written') setOverwrite(true);
      if (result.problem.code === 'studio.config_stale') onStale();
      const first = result.problem.issues?.[0]?.pointer.split('.')[1];
      if (first !== undefined && STEP_OF_FIELD[first] !== undefined) {
        setStep(STEP_OF_FIELD[first]);
      }
      return;
    }
    let secretProblem: Problem | null = null;
    const id = result.value.id ?? item?.id ?? `user:${form.name}`;
    if (form.password !== '' && (form.source === 'keyring' || form.source === 'session')) {
      const stored = await storeSecret(fetcher, id, form.password, form.source);
      if (!stored.ok) secretProblem = stored.problem;
    }
    setBusy(false);
    onSaved({ name: form.name, secretProblem });
  };

  const actions: DialogAction[] = [{ id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel }];
  if (step > 1) {
    actions.push({
      id: 'back',
      label: t('connections.wizard.back'),
      onSelect: () => {
        setProblem(null);
        setStep(step - 1);
      },
    });
  }
  actions.push(
    step < 3
      ? { id: 'next', label: t('connections.wizard.next'), variant: 'primary', onSelect: next }
      : {
          id: 'save',
          label: t(
            overwrite || handWritten ? 'connections.wizard.overwrite' : 'connections.wizard.save',
          ),
          variant: 'primary',
          onSelect: () => {
            if (!busy) void save();
          },
        },
  );

  const general = (problem?.issues ?? []).filter(
    (issue) => STEP_OF_FIELD[issue.pointer.split('.')[1] ?? ''] === undefined,
  );
  // Field issues are shown at their fields; anything else (a stale file, a general issue) on top.
  const showProblem =
    problem !== null && (problem.code !== 'studio.profile_invalid' || general.length > 0);

  return (
    <Dialog
      open
      testId="connection-wizard"
      title={
        editing
          ? t('connections.wizard.titleEdit', { name: item.name })
          : t('connections.wizard.titleNew')
      }
      description={
        <>
          <p className={styles.stepLabel}>
            {t('connections.wizard.step', { step })}: {t(`connections.wizard.steps.${step}`)}
          </p>
          <p>{t('connections.wizard.description')}</p>
        </>
      }
      initialFocus="cancel"
      onCancel={onCancel}
      actions={actions}
    >
      <ol className={styles.progress} aria-label={t('connections.wizard.titleNew')}>
        {[1, 2, 3].map((n) => (
          <li
            key={n}
            className={n === step ? styles.current : n < step ? styles.done : undefined}
            aria-current={n === step ? 'step' : undefined}
          >
            {t(`connections.wizard.steps.${n}`)}
          </li>
        ))}
      </ol>
      {handWritten && problem?.code !== 'studio.config_hand_written' ? (
        <p className={styles.warning}>{t('connections.wizard.handWritten')}</p>
      ) : null}
      {showProblem ? (
        <p className={styles.problem} role="alert" data-testid="wizard-problem">
          {problem.code === 'studio.config_hand_written'
            ? t('connections.wizard.handWritten')
            : problemText(t, problem)}
        </p>
      ) : null}
      <div ref={stepRef} className={styles.form} data-testid={`wizard-step-${step}`}>
        {step === 1 ? (
          <>
            <fieldset className={styles.fieldset}>
              <legend className={styles.label}>{t('connections.wizard.type')}</legend>
              <div className={styles.choices}>
                {(['sqlite', 'postgresql'] as Engine[]).map((engine) => (
                  <label key={engine} className={styles.choice}>
                    <input
                      type="radio"
                      name="engine"
                      value={engine}
                      checked={form.engine === engine}
                      onChange={() => set('engine', engine)}
                    />
                    {t(`connections.wizard.${engine}`)}
                  </label>
                ))}
              </div>
            </fieldset>
            {form.engine === 'sqlite' ? (
              <Field
                label={t('connections.wizard.path')}
                hint={t('connections.wizard.pathHint')}
                issues={fields.path}
              >
                {(props) => (
                  <input
                    {...props}
                    className={styles.input}
                    value={form.path}
                    spellCheck={false}
                    autoComplete="off"
                    required
                    onChange={(event) => set('path', event.target.value)}
                  />
                )}
              </Field>
            ) : (
              <>
                <div className={styles.row}>
                  <Field label={t('connections.wizard.host')} issues={fields.host}>
                    {(props) => (
                      <input
                        {...props}
                        className={styles.input}
                        value={form.host}
                        spellCheck={false}
                        autoComplete="off"
                        required
                        onChange={(event) => set('host', event.target.value)}
                      />
                    )}
                  </Field>
                  <Field
                    label={t('connections.wizard.port')}
                    hint={t('connections.wizard.portHint')}
                    issues={fields.port}
                  >
                    {(props) => (
                      <input
                        {...props}
                        className={styles.input}
                        value={form.port}
                        inputMode="numeric"
                        autoComplete="off"
                        onChange={(event) => set('port', event.target.value)}
                      />
                    )}
                  </Field>
                </div>
                <Field label={t('connections.wizard.database')} issues={fields.database}>
                  {(props) => (
                    <input
                      {...props}
                      className={styles.input}
                      value={form.database}
                      spellCheck={false}
                      autoComplete="off"
                      required
                      onChange={(event) => set('database', event.target.value)}
                    />
                  )}
                </Field>
                <Field label={t('connections.wizard.user')} issues={fields.user}>
                  {(props) => (
                    <input
                      {...props}
                      className={styles.input}
                      value={form.user}
                      spellCheck={false}
                      autoComplete="off"
                      onChange={(event) => set('user', event.target.value)}
                    />
                  )}
                </Field>
                <Field label={t('connections.wizard.sslmode')} issues={fields.sslmode}>
                  {(props) => (
                    <select
                      {...props}
                      className={styles.input}
                      value={form.sslmode}
                      onChange={(event) => set('sslmode', event.target.value)}
                    >
                      <option value="">{t('connections.wizard.sslAuto')}</option>
                      {SSL_MODES.map((mode) => (
                        <option key={mode} value={mode}>
                          {mode}
                        </option>
                      ))}
                    </select>
                  )}
                </Field>
              </>
            )}
          </>
        ) : null}
        {step === 2 ? (
          <>
            <fieldset className={styles.fieldset}>
              <legend className={styles.label}>{t('connections.wizard.passwordSource')}</legend>
              <div className={styles.choicesColumn}>
                {(['keyring', 'env', 'file', 'session', 'none'] as SecretChoice[]).map((source) => (
                  <label key={source} className={styles.choice}>
                    <input
                      type="radio"
                      name="source"
                      value={source}
                      checked={form.source === source}
                      onChange={() => set('source', source)}
                    />
                    {source === 'keyring' && !keyringAvailable
                      ? t('connections.wizard.sourceKeyringMissing')
                      : t(`connections.wizard.source${source[0]?.toUpperCase()}${source.slice(1)}`)}
                  </label>
                ))}
              </div>
            </fieldset>
            {form.source === 'env' ? (
              <Field label={t('connections.wizard.envName')} issues={fields.password}>
                {(props) => (
                  <input
                    {...props}
                    className={styles.input}
                    value={form.envName}
                    spellCheck={false}
                    autoComplete="off"
                    onChange={(event) => set('envName', event.target.value)}
                  />
                )}
              </Field>
            ) : null}
            {form.source === 'file' ? (
              <Field
                label={t('connections.wizard.filePath')}
                hint={t('connections.wizard.filePathHint')}
                issues={fields.password}
              >
                {(props) => (
                  <input
                    {...props}
                    className={styles.input}
                    value={form.filePath}
                    spellCheck={false}
                    autoComplete="off"
                    onChange={(event) => set('filePath', event.target.value)}
                  />
                )}
              </Field>
            ) : null}
            {form.source === 'keyring' || form.source === 'session' ? (
              <Field
                label={t('connections.wizard.password')}
                hint={t(
                  editing
                    ? 'connections.wizard.passwordKeep'
                    : 'connections.wizard.passwordOptional',
                )}
              >
                {(props) => (
                  <input
                    {...props}
                    className={styles.input}
                    type="password"
                    value={form.password}
                    autoComplete="new-password"
                    onChange={(event) => set('password', event.target.value)}
                  />
                )}
              </Field>
            ) : null}
          </>
        ) : null}
        {step === 3 ? (
          <>
            <Field
              label={t('connections.wizard.name')}
              hint={editing ? t('connections.wizard.nameFixed') : undefined}
              issues={fields.name}
            >
              {(props) => (
                <input
                  {...props}
                  className={styles.input}
                  value={form.name}
                  readOnly={editing}
                  spellCheck={false}
                  autoComplete="off"
                  required
                  onChange={(event) => set('name', event.target.value)}
                />
              )}
            </Field>
            <fieldset
              className={styles.fieldset}
              aria-invalid={fields.environment ? true : undefined}
            >
              <legend className={styles.label}>{t('connections.wizard.environment')}</legend>
              <div className={styles.choices}>
                {(['dev', 'test', 'prod'] as EnvironmentTag[]).map((environment) => (
                  <label key={environment} className={styles.choice}>
                    <input
                      type="radio"
                      name="environment"
                      value={environment}
                      checked={form.environment === environment}
                      onChange={() => set('environment', environment)}
                    />
                    {t(`header.env.${environment}`)}
                  </label>
                ))}
              </div>
              {fields.environment ? <p className={styles.error}>{t('issues.required')}</p> : null}
            </fieldset>
            {form.engine === 'postgresql' ? (
              <label className={styles.check}>
                <input
                  type="checkbox"
                  checked={form.readOnlyRole}
                  onChange={(event) => set('readOnlyRole', event.target.checked)}
                />
                {t('connections.wizard.readOnlyRole')}
              </label>
            ) : null}
            <div className={styles.testRow}>
              <Button disabled>{t('connections.wizard.test')}</Button>
              <p className={styles.hint}>{t('connections.wizard.testLater')}</p>
            </div>
            <p className={styles.hint}>{t('connections.wizard.savedIn')}</p>
          </>
        ) : null}
      </div>
    </Dialog>
  );
}
