/**
 * The Database Manager's "New database" wizard (STU-CR-2, STU-CR-3; STU-ADR-0007 section 1 as amended by
 * STU-ADR-0008 for gate G-S4). Rendered only while the server says the `database_manager` flag is on
 * (`GET /api/write/areas`); the page never decides the flag, and the server refuses both routes while it is off
 * (405), also when only the Schema Designer is on.
 *
 * - **SQLite file:** an absolute path in a folder of the user's own; the file name typed to confirm. The server
 *   checks the folder (no link, only the user may write it, not one of Studio's own folders), never overwrites a
 *   file and creates it `0600`.
 * - **PostgreSQL database:** on an open connection of a DEV or TEST PostgreSQL profile, after unlocking its
 *   **admin** profile (typed name, 15 minutes, as in the Database Manager); a write profile never creates a database; the database name typed to confirm. The server checks
 *   CREATEDB and the owner through tabrion and refuses PROD; when the role may not create databases, the page shows
 *   the guided steps (ask the administrator, then add a connection profile).
 *
 * After either, the page offers to save a connection profile for the new database through the normal profile route,
 * with the environment the user picks. The create button is never the default focus.
 */
import { useEffect, useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchOpenConnections, type OpenedConnection } from '../../api/connections';
import { fetchUnlocks } from '../../api/dataEditor';
import type { Problem, ProblemIssue } from '../../api/problem';
import {
  createProfile,
  fetchProfiles,
  profileFields,
  type EnvironmentTag,
  type ProfileFields,
  type ProfileItem,
} from '../../api/profiles';
import {
  createPostgresDatabase,
  createSqliteFile,
  fileName,
  type PostgresDatabaseFields,
} from '../../api/schemaDesigner';
import { fetchWriteAreas } from '../../api/write';
import { Button } from '../../components/ui/Button';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { problemText } from '../connections/messages';
import styles from '../Views.module.css';
import { UnlockForm } from './DataEditor';

type Engine = 'sqlite' | 'postgresql';

/** The issues of `problem` for one field (`create.<field>`). */
function fieldIssues(problem: Problem | null, field: string): ProblemIssue[] {
  return (problem?.issues ?? []).filter((issue) => issue.pointer === `create.${field}`);
}

export function CreateDatabaseSection({
  fetcher = fetch,
  onSaved,
}: {
  fetcher?: typeof fetch;
  /** A connection profile was saved for a new database: the connections list reads the profiles again. */
  onSaved?: () => void;
}) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    let alive = true;
    void fetchWriteAreas(fetcher).then((areas) => {
      if (alive) setOn(areas.database_manager);
    });
    return () => {
      alive = false;
    };
  }, [fetcher]);
  if (!on) return null;
  return <CreateDatabase fetcher={fetcher} onSaved={onSaved} />;
}

function CreateDatabase({ fetcher, onSaved }: { fetcher: typeof fetch; onSaved?: () => void }) {
  const { t } = useTranslation();
  const id = useId();
  const [engine, setEngine] = useState<Engine>('sqlite');
  return (
    <section className={styles.card} aria-labelledby={`${id}-title`} data-testid="create-database">
      <h2 id={`${id}-title`} className={styles.cardTitle}>
        {t('createDatabase.title')}
      </h2>
      <p className={styles.lead}>{t('createDatabase.lead')}</p>
      <fieldset className={connectionStyles.form}>
        <legend className={connectionStyles.label}>{t('createDatabase.engine')}</legend>
        {(['sqlite', 'postgresql'] as const).map((value) => (
          <label key={value}>
            <input
              type="radio"
              name={`${id}-engine`}
              value={value}
              checked={engine === value}
              onChange={() => setEngine(value)}
            />{' '}
            {t(`createDatabase.engines.${value}`)}
          </label>
        ))}
      </fieldset>
      {engine === 'sqlite' ? (
        <SqliteWizard fetcher={fetcher} onSaved={onSaved} />
      ) : (
        <PostgresWizard fetcher={fetcher} onSaved={onSaved} />
      )}
    </section>
  );
}

function ProblemLine({ problem }: { problem: Problem | null }) {
  const { t } = useTranslation();
  if (problem === null) return null;
  return (
    <p role="alert" className={connectionStyles.error} data-testid="create-database-problem">
      {problemText(t, problem)}
    </p>
  );
}

function SqliteWizard({ fetcher, onSaved }: { fetcher: typeof fetch; onSaved?: () => void }) {
  const { t } = useTranslation();
  const [path, setPath] = useState('');
  const [typed, setTyped] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<{ path: string; name: string; pending: boolean } | null>(
    null,
  );
  const name = fileName(path);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    setBusy(true);
    const result = await createSqliteFile(fetcher, path, typed);
    setBusy(false);
    if (!result.ok) {
      setProblem(result.problem);
      return;
    }
    setCreated({
      path: result.value.path,
      name: result.value.fileName,
      pending: result.value.auditPending,
    });
    setTyped('');
  };

  if (created !== null) {
    const stem = created.name.replace(/\.(?:db|sqlite3?)$/i, '');
    return (
      <div className={connectionStyles.form}>
        <p role="status" className={connectionStyles.status} data-testid="create-database-done">
          {t('createDatabase.sqlite.done', {
            path: created.path,
            interpolation: { escapeValue: false },
          })}
          {created.pending ? ` ${t('createDatabase.auditPending')}` : ''}
        </p>
        <SaveProfile
          fetcher={fetcher}
          fields={{ engine: 'sqlite', path: created.path }}
          defaultName={stem}
          defaultEnvironment="dev"
          onSaved={onSaved}
        />
        <div className={connectionStyles.actions}>
          <Button
            onClick={() => {
              setCreated(null);
              setPath('');
            }}
          >
            {t('createDatabase.another')}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => void submit(event)}
      data-testid="create-sqlite"
    >
      <Field
        label={t('createDatabase.sqlite.path')}
        hint={t('createDatabase.sqlite.pathHint')}
        issues={fieldIssues(problem, 'path')}
      >
        {(props) => (
          <input
            {...props}
            className={connectionStyles.input}
            value={path}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => {
              setPath(event.target.value);
              setProblem(null);
            }}
          />
        )}
      </Field>
      <p className={connectionStyles.hint}>{t('createDatabase.sqlite.rules')}</p>
      {name !== '' ? (
        <Field
          label={t('createDatabase.sqlite.type', { name, interpolation: { escapeValue: false } })}
        >
          {(props) => (
            <input
              {...props}
              className={connectionStyles.input}
              value={typed}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setTyped(event.target.value)}
            />
          )}
        </Field>
      ) : null}
      <ProblemLine problem={(problem?.issues ?? []).length > 0 ? null : problem} />
      <div className={connectionStyles.actions}>
        <Button type="submit" variant="primary" disabled={busy || name === '' || typed !== name}>
          {t('createDatabase.sqlite.create')}
        </Button>
      </div>
    </form>
  );
}

/** The open connections of the user's own PostgreSQL profiles that are not PROD (the only ones that can unlock). */
function candidates(opened: OpenedConnection[], profiles: ProfileItem[]) {
  return opened.flatMap((connection) => {
    const profile = profiles.find((item) => item.id === connection.profileId);
    return profile !== undefined &&
      profile.source === 'user' &&
      profile.engine === 'postgresql' &&
      profile.environment !== 'prod'
      ? [{ connection, profile }]
      : [];
  });
}

const EMPTY_FIELDS: PostgresDatabaseFields = {
  name: '',
  owner: '',
  template: 'template1',
  encoding: 'UTF8',
  locale: '',
};

function PostgresWizard({ fetcher, onSaved }: { fetcher: typeof fetch; onSaved?: () => void }) {
  const { t } = useTranslation();
  const [targets, setTargets] = useState<ReturnType<typeof candidates> | null>(null);
  const [chosen, setChosen] = useState('');
  /** Whether the chosen connection is unlocked, as last read for it; null until read. */
  const [unlock, setUnlock] = useState<{
    connectionId: string;
    unlocked: boolean;
    ended?: Problem | null;
  } | null>(null);

  useEffect(() => {
    let alive = true;
    void Promise.all([fetchOpenConnections(fetcher), fetchProfiles(fetcher)]).then(
      ([opened, list]) => {
        if (!alive) return;
        const found = candidates(opened ?? [], list?.profiles ?? []);
        setTargets(found);
        setChosen((current) => current || (found[0]?.connection.connectionId ?? ''));
      },
    );
    return () => {
      alive = false;
    };
  }, [fetcher]);

  useEffect(() => {
    if (chosen === '') return undefined;
    let alive = true;
    void fetchUnlocks(fetcher).then((list) => {
      if (alive) {
        setUnlock({
          connectionId: chosen,
          unlocked: list.some((item) => item.connectionId === chosen),
        });
      }
    });
    return () => {
      alive = false;
    };
  }, [fetcher, chosen]);

  if (targets === null)
    return <p className={connectionStyles.hint}>{t('createDatabase.loading')}</p>;
  if (targets.length === 0) {
    return (
      <p className={connectionStyles.hint} data-testid="create-postgres-none">
        {t('createDatabase.postgres.noConnection')}
      </p>
    );
  }
  const target = targets.find((item) => item.connection.connectionId === chosen) ?? targets[0];
  if (target === undefined) return null;
  const connectionId = target.connection.connectionId;
  const unlocked = unlock?.connectionId === connectionId ? unlock.unlocked : null;
  const ended = unlock?.connectionId === connectionId ? (unlock.ended ?? null) : null;
  const setUnlocked = (value: boolean, why: Problem | null = null) =>
    setUnlock({ connectionId, unlocked: value, ended: why });
  return (
    <div className={connectionStyles.form}>
      {targets.length > 1 ? (
        <Field label={t('createDatabase.postgres.connection')}>
          {(props) => (
            <select
              {...props}
              className={connectionStyles.input}
              value={target.connection.connectionId}
              onChange={(event) => setChosen(event.target.value)}
            >
              {targets.map((item) => (
                <option key={item.connection.connectionId} value={item.connection.connectionId}>
                  {item.profile.name}
                </option>
              ))}
            </select>
          )}
        </Field>
      ) : (
        <p className={connectionStyles.hint}>
          {t('createDatabase.postgres.on', {
            name: target.profile.name,
            interpolation: { escapeValue: false },
          })}
        </p>
      )}
      {unlocked === null ? (
        <p className={connectionStyles.hint}>{t('createDatabase.loading')}</p>
      ) : unlocked ? (
        <PostgresForm
          fetcher={fetcher}
          connectionId={target.connection.connectionId}
          source={target.profile}
          onSaved={onSaved}
          onLocked={(why) => setUnlocked(false, why)}
        />
      ) : (
        <>
          <ProblemLine problem={ended} />
          <UnlockForm
            fetcher={fetcher}
            connectionId={target.connection.connectionId}
            profileId={target.profile.id}
            kind="admin"
            lead={t('createDatabase.postgres.unlockLead')}
            empty={t('createDatabase.postgres.noAdminProfile')}
            onUnlocked={() => setUnlocked(true)}
          />
        </>
      )}
    </div>
  );
}

/** The problems after which the unlock is gone: the page shows the unlock again. */
const LOCKED = new Set([
  'studio.write_locked',
  'studio.write_target_changed',
  // The admin role's privilege check, again at the start of the run: the server ended the unlock.
  'studio.admin_over_privileged',
]);

function PostgresForm({
  fetcher,
  connectionId,
  source,
  onSaved,
  onLocked,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  source: ProfileItem;
  onSaved?: () => void;
  /** The unlock ended; `why` is the problem that ended it, shown above the unlock. */
  onLocked: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const [fields, setFields] = useState<PostgresDatabaseFields>(EMPTY_FIELDS);
  const [typed, setTyped] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<{ name: string; pending: boolean } | null>(null);
  const set = (key: keyof PostgresDatabaseFields, value: string) => {
    setFields((current) => ({ ...current, [key]: value }));
    setProblem(null);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    setBusy(true);
    const result = await createPostgresDatabase(fetcher, connectionId, fields, typed);
    setBusy(false);
    if (!result.ok) {
      setProblem(result.problem);
      if (LOCKED.has(result.problem.code)) onLocked(result.problem);
      return;
    }
    setCreated({ name: result.value.database, pending: result.value.auditPending });
    setTyped('');
  };

  if (created !== null) {
    const base = profileFields(source);
    // The source profile's server, role, TLS and secret reference; its own name and environment are not copied.
    const connection = Object.fromEntries(
      Object.entries(base).filter(([key]) => key !== 'name' && key !== 'environment'),
    ) as Omit<ProfileFields, 'name' | 'environment'>;
    return (
      <div className={connectionStyles.form}>
        <p role="status" className={connectionStyles.status} data-testid="create-database-done">
          {t('createDatabase.postgres.done', {
            name: created.name,
            interpolation: { escapeValue: false },
          })}
          {created.pending ? ` ${t('createDatabase.auditPending')}` : ''}
        </p>
        <SaveProfile
          fetcher={fetcher}
          fields={{ ...connection, database: created.name }}
          defaultName={`${source.name}-${created.name}`.slice(0, 64)}
          defaultEnvironment={base.environment}
          onSaved={onSaved}
        />
        <div className={connectionStyles.actions}>
          <Button
            onClick={() => {
              setCreated(null);
              setFields(EMPTY_FIELDS);
            }}
          >
            {t('createDatabase.another')}
          </Button>
        </div>
      </div>
    );
  }

  const textField = (key: 'name' | 'owner' | 'encoding' | 'locale', hint?: string) => (
    <Field
      label={t(`createDatabase.postgres.${key}`)}
      hint={hint}
      issues={fieldIssues(problem, key)}
    >
      {(props) => (
        <input
          {...props}
          className={connectionStyles.input}
          value={fields[key]}
          autoComplete="off"
          spellCheck={false}
          required={key === 'name' || key === 'encoding'}
          onChange={(event) => set(key, event.target.value)}
        />
      )}
    </Field>
  );

  const invalid = (problem?.issues ?? []).length > 0;
  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => void submit(event)}
      data-testid="create-postgres"
    >
      {textField('name')}
      {textField('owner', t('createDatabase.postgres.ownerHint'))}
      <Field
        label={t('createDatabase.postgres.template')}
        hint={t('createDatabase.postgres.templateHint')}
      >
        {(props) => (
          <select
            {...props}
            className={connectionStyles.input}
            value={fields.template}
            onChange={(event) =>
              set('template', event.target.value === 'template0' ? 'template0' : 'template1')
            }
          >
            <option value="template1">{t('createDatabase.postgres.templates.template1')}</option>
            <option value="template0">{t('createDatabase.postgres.templates.template0')}</option>
          </select>
        )}
      </Field>
      {textField('encoding')}
      {textField('locale', t('createDatabase.postgres.localeHint'))}
      {fields.name !== '' ? (
        <Field
          label={t('createDatabase.postgres.type', {
            name: fields.name,
            interpolation: { escapeValue: false },
          })}
        >
          {(props) => (
            <input
              {...props}
              className={connectionStyles.input}
              value={typed}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setTyped(event.target.value)}
            />
          )}
        </Field>
      ) : null}
      <ProblemLine problem={invalid ? null : problem} />
      {problem?.code === 'studio.schema_createdb_required' ? (
        <div className={connectionStyles.warning} data-testid="create-postgres-guided">
          <p>{t('createDatabase.postgres.guided.lead')}</p>
          <ol className={connectionStyles.issues}>
            <li>{t('createDatabase.postgres.guided.ask')}</li>
            <li>{t('createDatabase.postgres.guided.profile')}</li>
          </ol>
        </div>
      ) : null}
      <div className={connectionStyles.actions}>
        <Button
          type="submit"
          variant="primary"
          disabled={busy || fields.name === '' || typed !== fields.name}
        >
          {t('createDatabase.postgres.create')}
        </Button>
      </div>
    </form>
  );
}

const ENVIRONMENTS: EnvironmentTag[] = ['dev', 'test', 'prod'];

/** Save a read profile for the new database through the normal profile route (the user's profiles.toml). */
function SaveProfile({
  fetcher,
  fields,
  defaultName,
  defaultEnvironment,
  onSaved,
}: {
  fetcher: typeof fetch;
  fields: Omit<ProfileFields, 'name' | 'environment'>;
  defaultName: string;
  defaultEnvironment: EnvironmentTag;
  onSaved?: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [name, setName] = useState(defaultName);
  const [environment, setEnvironment] = useState<EnvironmentTag>(defaultEnvironment);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [saved, setSaved] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    const list = await fetchProfiles(fetcher);
    const result = await createProfile(
      fetcher,
      { ...fields, name, environment } as ProfileFields,
      list?.userFile.digest ?? null,
    );
    if (!result.ok) {
      setProblem(result.problem);
      return;
    }
    setSaved(true);
    onSaved?.();
  };

  if (saved) {
    return (
      <p role="status" className={connectionStyles.status} data-testid="create-database-saved">
        {t('createDatabase.profile.saved', { name, interpolation: { escapeValue: false } })}
      </p>
    );
  }
  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => void submit(event)}
      aria-labelledby={`${id}-title`}
      data-testid="create-database-profile"
    >
      <h3 id={`${id}-title`}>{t('createDatabase.profile.title')}</h3>
      <p className={connectionStyles.hint}>{t('createDatabase.profile.lead')}</p>
      <Field label={t('createDatabase.profile.name')}>
        {(props) => (
          <input
            {...props}
            className={connectionStyles.input}
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        )}
      </Field>
      <Field label={t('createDatabase.profile.environment')}>
        {(props) => (
          <select
            {...props}
            className={connectionStyles.input}
            value={environment}
            onChange={(event) =>
              setEnvironment(ENVIRONMENTS.find((e) => e === event.target.value) ?? 'dev')
            }
          >
            {ENVIRONMENTS.map((value) => (
              <option key={value} value={value}>
                {t(`createDatabase.profile.environments.${value}`)}
              </option>
            ))}
          </select>
        )}
      </Field>
      <ProblemLine problem={problem} />
      <div className={connectionStyles.actions}>
        <Button type="submit">{t('createDatabase.profile.save')}</Button>
      </div>
    </form>
  );
}
