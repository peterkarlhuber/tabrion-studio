/**
 * The Database Manager's backup and restore (STU-XDM-2; STU-ADR-0008, gate G-S4, not granted). Rendered only while
 * the server says the `database_manager` flag is on (`GET /api/write/areas`); the page never decides the flag, and the
 * server refuses every route while it is off (405).
 *
 * - **Backups:** the list of Studio's own backups with their count and size. Studio never deletes a backup (review
 *   I-3); the page says how much space they take. A PostgreSQL backup is never complete (E-X5): the page says so.
 * - **Make a backup:** on an open connection of a DEV or TEST profile, after unlocking one of its admin profiles
 *   (typed name, as in the Data Editor).
 * - **Restore:** a preview first (changes nothing; on PostgreSQL the number of extensions in the archive), then the
 *   target (PostgreSQL: only a new database, run as a separate lean restore profile; SQLite: a new file or the
 *   connection's own file, backed up first) and its name typed to confirm. The restore button is never the default
 *   focus.
 * - **Roles (STU-XDM-4, PostgreSQL only):** see `DatabaseManagerRoles.tsx`.
 * - **Profile pairs, import and export (STU-XDM-5):** see `DatabaseManagerProfiles.tsx`; they need no connection.
 * - **Delete (STU-XDM-3):** a preview first (changes nothing): on PostgreSQL another database of the server by its name
 *   (never a maintenance database or the connected one) with the number of other sessions on it; on SQLite this
 *   connection's own file. Then its name typed to confirm. Ending the other sessions (`force`, PostgreSQL 13 or later)
 *   is offered only while there are some, is never preselected and needs the name typed once more. The server makes
 *   a verified backup first and refuses the delete if that fails. The delete button is never the default focus.
 *
 * The page sends IDs, the target's name and what the user typed; never a path, a tool or an environment.
 */
import { useEffect, useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchOpenConnections } from '../../api/connections';
import { fetchUnlocks } from '../../api/dataEditor';
import {
  fetchBackups,
  makeBackup,
  previewDelete,
  previewRestore,
  runDelete,
  runRestore,
  type BackupItem,
  type BackupListing,
  type DeletePreview,
  type DeleteResult,
  type RestorePreview,
  type RestoreResult,
  type ToolState,
} from '../../api/databaseManager';
import type { Problem } from '../../api/problem';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import { fetchWriteAreas, fetchWriteProfiles, type WriteProfileItem } from '../../api/write';
import { formatBytes } from '../../components/export/ExportDialog';
import { Button } from '../../components/ui/Button';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { problemText } from '../connections/messages';
import styles from '../Views.module.css';
import { UnlockForm } from './DataEditor';
import { ProfilesPart } from './DatabaseManagerProfiles';
import { RolesPart } from './DatabaseManagerRoles';

export function DatabaseManagerSection({ fetcher = fetch }: { fetcher?: typeof fetch }) {
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
  return <DatabaseManager fetcher={fetcher} />;
}

/** The problems after which the unlock is gone: the page shows the unlock again. */
const LOCKED = new Set([
  'studio.write_locked',
  'studio.write_target_changed',
  'studio.admin_over_privileged',
]);

/** The fixed issue codes the page has its own text for (`tool.*`, `backup.*`, `restore.*`). */
const ISSUE_TEXTS = new Set([
  'tool.not_configured',
  'tool.relative',
  'tool.missing',
  'tool.link',
  'tool.not_a_program',
  'tool.writable',
  'tool.owner',
  'tool.folder',
  'tool.platform',
  'tool.wrong_program',
  'tool.too_old',
  'tool.not_started',
  'tool.timeout',
  'tool.auth',
  'tool.connect',
  'tool.permission',
  'tool.version',
  'tool.other',
  'backup.changed',
  'backup.link',
  'backup.metadata_invalid',
  'backup.missing',
  'backup.not_a_file',
  'backup.not_custom_format',
  'restore.role_required',
  'restore.role_unknown',
  'restore.role_is_admin',
  'restore.in_use',
  'restore.integrity',
  'restore.folder_unsafe',
  'restore.reserved_folder',
  'restore.platform',
  'delete.environment',
  'delete.maintenance',
  'delete.connected',
  'delete.prod',
  'delete.platform',
  'delete.folder_unsafe',
  'delete.reserved_folder',
  'delete.force_not_offered',
  'delete.force_unsupported',
  'delete.force_not_a_flag',
  'delete.target',
  'delete.in_use',
  'delete.refused',
  'delete.failed',
]);

function issueText(t: ReturnType<typeof useTranslation>['t'], code: string): string {
  return ISSUE_TEXTS.has(code)
    ? t(`databaseManager.issues.${code}`)
    : t('databaseManager.issues.unknown', { code, interpolation: { escapeValue: false } });
}

function ProblemLine({ problem }: { problem: Problem | null }) {
  const { t } = useTranslation();
  if (problem === null) return null;
  const issues = problem.issues ?? [];
  return (
    <div role="alert" className={connectionStyles.error} data-testid="database-manager-problem">
      <p>{problemText(t, problem)}</p>
      {issues.length > 0 ? (
        <ul className={connectionStyles.issues}>
          {issues.map((issue) => (
            <li key={`${issue.pointer}:${issue.code}`}>{issueText(t, issue.code)}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ToolLine({ name, state }: { name: string; state: ToolState }) {
  const { t } = useTranslation();
  return (
    <li data-testid={`database-manager-tool-${name}`}>
      {state.ok
        ? t('databaseManager.tools.ok', {
            name,
            version: state.version ?? '',
            interpolation: { escapeValue: false },
          })
        : t('databaseManager.tools.notUsable', {
            name,
            reason: issueText(t, state.reason ?? ''),
            interpolation: { escapeValue: false },
          })}
    </li>
  );
}

function DatabaseManager({ fetcher }: { fetcher: typeof fetch }) {
  const { t } = useTranslation();
  const id = useId();
  const [listing, setListing] = useState<BackupListing | null | 'failed'>(null);
  /** Counts the changes (a backup or restore made): the list is read again after each. */
  const [changes, setChanges] = useState(0);
  useEffect(() => {
    let alive = true;
    void fetchBackups(fetcher).then((value) => {
      if (alive) setListing(value ?? 'failed');
    });
    return () => {
      alive = false;
    };
  }, [fetcher, changes]);

  return (
    <section className={styles.card} aria-labelledby={`${id}-title`} data-testid="database-manager">
      <h2 id={`${id}-title`} className={styles.cardTitle}>
        {t('databaseManager.title')}
      </h2>
      <p className={styles.lead}>{t('databaseManager.lead')}</p>
      <p className={connectionStyles.warning}>{t('databaseManager.notComplete')}</p>
      {listing === null ? (
        <p className={connectionStyles.hint}>{t('databaseManager.loading')}</p>
      ) : listing === 'failed' ? (
        <p className={connectionStyles.error}>{t('databaseManager.listFailed')}</p>
      ) : (
        <>
          <ul className={connectionStyles.issues} aria-label={t('databaseManager.tools.title')}>
            <ToolLine name="pg_dump" state={listing.tools.pg_dump} />
            <ToolLine name="pg_restore" state={listing.tools.pg_restore} />
          </ul>
          <ConnectionPart
            fetcher={fetcher}
            listing={listing}
            onChanged={() => setChanges((value) => value + 1)}
          />
        </>
      )}
      <ProfilesPart fetcher={fetcher} />
    </section>
  );
}

/** The open connections of the user's own profiles that are not PROD (the only ones whose admin profiles unlock). */
function candidates(
  opened: { connectionId: string; profileId: string | null }[],
  profiles: ProfileItem[],
) {
  return opened.flatMap((connection) => {
    const profile = profiles.find((item) => item.id === connection.profileId);
    return profile !== undefined && profile.source === 'user' && profile.environment !== 'prod'
      ? [{ connectionId: connection.connectionId, profile }]
      : [];
  });
}

function ConnectionPart({
  fetcher,
  listing,
  onChanged,
}: {
  fetcher: typeof fetch;
  listing: BackupListing;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [targets, setTargets] = useState<ReturnType<typeof candidates> | null>(null);
  const [chosen, setChosen] = useState('');
  const [unlock, setUnlock] = useState<{
    connectionId: string;
    unlocked: boolean;
    ended: Problem | null;
  } | null>(null);

  useEffect(() => {
    let alive = true;
    void Promise.all([fetchOpenConnections(fetcher), fetchProfiles(fetcher)]).then(
      ([opened, list]) => {
        if (!alive) return;
        const found = candidates(opened ?? [], list?.profiles ?? []);
        setTargets(found);
        setChosen((current) => current || (found[0]?.connectionId ?? ''));
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
          ended: null,
        });
      }
    });
    return () => {
      alive = false;
    };
  }, [fetcher, chosen]);

  const target = targets?.find((item) => item.connectionId === chosen) ?? targets?.[0];
  const engine = target?.profile.engine;
  const backups = listing.backups.filter((item) => engine === undefined || item.engine === engine);

  return (
    <>
      <BackupList listing={listing} backups={backups} />
      {targets === null ? (
        <p className={connectionStyles.hint}>{t('databaseManager.loading')}</p>
      ) : target === undefined ? (
        <p className={connectionStyles.hint} data-testid="database-manager-none">
          {t('databaseManager.noConnection')}
        </p>
      ) : (
        <div className={connectionStyles.form}>
          {targets.length > 1 ? (
            <Field label={t('databaseManager.connection')}>
              {(props) => (
                <select
                  {...props}
                  className={connectionStyles.input}
                  value={target.connectionId}
                  onChange={(event) => setChosen(event.target.value)}
                >
                  {targets.map((item) => (
                    <option key={item.connectionId} value={item.connectionId}>
                      {item.profile.name}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          ) : (
            <p className={connectionStyles.hint}>
              {t('databaseManager.on', {
                name: target.profile.name,
                interpolation: { escapeValue: false },
              })}
            </p>
          )}
          {unlock === null || unlock.connectionId !== target.connectionId ? (
            <p className={connectionStyles.hint}>{t('databaseManager.loading')}</p>
          ) : unlock.unlocked ? (
            <Unlocked
              fetcher={fetcher}
              connectionId={target.connectionId}
              profile={target.profile}
              backups={backups}
              onChanged={onChanged}
              onLocked={(why) =>
                setUnlock({ connectionId: target.connectionId, unlocked: false, ended: why })
              }
            />
          ) : (
            <>
              <ProblemLine problem={unlock.ended} />
              <UnlockForm
                fetcher={fetcher}
                connectionId={target.connectionId}
                profileId={target.profile.id}
                kind="admin"
                lead={t('databaseManager.unlockLead')}
                empty={t('databaseManager.noAdminProfile')}
                onUnlocked={() =>
                  setUnlock({ connectionId: target.connectionId, unlocked: true, ended: null })
                }
              />
            </>
          )}
        </div>
      )}
    </>
  );
}

function BackupList({ listing, backups }: { listing: BackupListing; backups: BackupItem[] }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  return (
    <>
      <p className={connectionStyles.hint} data-testid="database-manager-size">
        {t('databaseManager.size', {
          count: listing.count,
          size: formatBytes(listing.totalBytes, locale),
          interpolation: { escapeValue: false },
        })}
      </p>
      {backups.length === 0 ? (
        <p className={connectionStyles.hint}>{t('databaseManager.noBackups')}</p>
      ) : (
        <ul className={connectionStyles.issues} data-testid="database-manager-backups">
          {backups.map((item) => (
            <li key={item.file}>
              <code>{item.file}</code>{' '}
              {t('databaseManager.backupLine', {
                database: item.database ?? '?',
                size: formatBytes(item.size, locale),
                interpolation: { escapeValue: false },
              })}
              {item.complete ? '' : ` ${t('databaseManager.incomplete')}`}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function Unlocked({
  fetcher,
  connectionId,
  profile,
  backups,
  onChanged,
  onLocked,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  profile: ProfileItem;
  backups: BackupItem[];
  onChanged: () => void;
  onLocked: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const [made, setMade] = useState<BackupItem | null>(null);

  const refused = (why: Problem) => {
    if (LOCKED.has(why.code)) onLocked(why);
    else setProblem(why);
  };

  const backup = async () => {
    setProblem(null);
    setMade(null);
    setBusy(true);
    const result = await makeBackup(fetcher, connectionId);
    setBusy(false);
    if (!result.ok) {
      refused(result.problem);
      return;
    }
    setMade(result.value);
    onChanged();
  };

  return (
    <div className={connectionStyles.form} data-testid="database-manager-unlocked">
      <div className={connectionStyles.actions}>
        <Button onClick={() => void backup()} disabled={busy} data-testid="database-manager-backup">
          {t('databaseManager.backup')}
        </Button>
      </div>
      {made !== null ? (
        <p role="status" className={connectionStyles.status} data-testid="database-manager-made">
          {t('databaseManager.made', { file: made.file, interpolation: { escapeValue: false } })}
        </p>
      ) : null}
      <ProblemLine problem={problem} />
      <RestorePart
        fetcher={fetcher}
        connectionId={connectionId}
        profile={profile}
        backups={backups}
        onChanged={onChanged}
        onRefused={refused}
      />
      <DeletePart
        fetcher={fetcher}
        connectionId={connectionId}
        profile={profile}
        onChanged={onChanged}
        onRefused={refused}
      />
      {profile.engine === 'postgresql' ? (
        <RolesPart
          fetcher={fetcher}
          connectionId={connectionId}
          locked={LOCKED}
          onLocked={onLocked}
        />
      ) : null}
    </div>
  );
}

function RestorePart({
  fetcher,
  connectionId,
  profile,
  backups,
  onChanged,
  onRefused,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  profile: ProfileItem;
  backups: BackupItem[];
  onChanged: () => void;
  onRefused: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [backupId, setBackupId] = useState('');
  const [preview, setPreview] = useState<RestorePreview | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen = backups.some((item) => item.file === backupId)
    ? backupId
    : (backups[0]?.file ?? '');

  if (backups.length === 0) return null;

  const look = async () => {
    setProblem(null);
    setPreview(null);
    setBusy(true);
    const result = await previewRestore(fetcher, connectionId, chosen);
    setBusy(false);
    if (result.ok) setPreview(result.value);
    else if (LOCKED.has(result.problem.code)) onRefused(result.problem);
    else setProblem(result.problem);
  };

  return (
    <section aria-labelledby={`${id}-title`} className={connectionStyles.form}>
      <h3 id={`${id}-title`}>{t('databaseManager.restore.title')}</h3>
      <p className={connectionStyles.hint}>{t('databaseManager.restore.lead')}</p>
      <Field label={t('databaseManager.restore.backup')}>
        {(props) => (
          <select
            {...props}
            className={connectionStyles.input}
            value={chosen}
            onChange={(event) => {
              setBackupId(event.target.value);
              setPreview(null);
            }}
          >
            {backups.map((item) => (
              <option key={item.file} value={item.file}>
                {item.file}
              </option>
            ))}
          </select>
        )}
      </Field>
      <div className={connectionStyles.actions}>
        <Button onClick={() => void look()} disabled={busy} data-testid="database-manager-preview">
          {t('databaseManager.restore.preview')}
        </Button>
      </div>
      <ProblemLine problem={problem} />
      {preview !== null && preview.backup === chosen ? (
        <RestoreForm
          key={preview.backup}
          fetcher={fetcher}
          connectionId={connectionId}
          profile={profile}
          preview={preview}
          onDone={onChanged}
          onRefused={onRefused}
        />
      ) : null}
    </section>
  );
}

function RestoreForm({
  fetcher,
  connectionId,
  profile,
  preview,
  onDone,
  onRefused,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  profile: ProfileItem;
  preview: RestorePreview;
  onDone: () => void;
  onRefused: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [target, setTarget] = useState<'new' | 'existing'>('new');
  const [name, setName] = useState('');
  const [typed, setTyped] = useState('');
  const [roles, setRoles] = useState<WriteProfileItem[] | null>(null);
  const [role, setRole] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<RestoreResult | null>(null);

  useEffect(() => {
    if (!preview.restoreProfileRequired) return undefined;
    let alive = true;
    void fetchWriteProfiles(fetcher).then((list) => {
      if (!alive) return;
      const own = list.filter((item) => item.kind === 'admin' && item.readProfileId === profile.id);
      setRoles(own);
      setRole((current) => current || (own[0]?.id ?? ''));
    });
    return () => {
      alive = false;
    };
  }, [fetcher, preview.restoreProfileRequired, profile.id]);

  const expected = target === 'existing' ? (preview.existingName ?? '') : name;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    setBusy(true);
    const result = await runRestore(fetcher, connectionId, {
      backupId: preview.backup,
      target,
      name,
      typed,
      restoreProfileId: preview.restoreProfileRequired ? role : null,
    });
    setBusy(false);
    if (!result.ok) {
      if (LOCKED.has(result.problem.code)) onRefused(result.problem);
      else setProblem(result.problem);
      return;
    }
    setDone(result.value);
    setTyped('');
    onDone();
  };

  if (done !== null) {
    return (
      <p role="status" className={connectionStyles.status} data-testid="database-manager-restored">
        {t(done.verified ? 'databaseManager.restore.verified' : 'databaseManager.restore.done', {
          target: done.target,
          interpolation: { escapeValue: false },
        })}
        {done.preBackup !== null
          ? ` ${t('databaseManager.restore.preBackup', { file: done.preBackup, interpolation: { escapeValue: false } })}`
          : ''}
        {done.auditPending ? ` ${t('databaseManager.auditPending')}` : ''}
      </p>
    );
  }

  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => void submit(event)}
      data-testid="database-manager-restore"
    >
      {preview.extensions !== null ? (
        <p className={connectionStyles.hint} data-testid="database-manager-extensions">
          {t('databaseManager.restore.extensions', { count: preview.extensions })}
        </p>
      ) : null}
      {preview.targets.length > 1 ? (
        <fieldset className={connectionStyles.fieldset}>
          <legend className={connectionStyles.label}>{t('databaseManager.restore.target')}</legend>
          {preview.targets.map((value) => (
            <label key={value} className={connectionStyles.choice}>
              <input
                type="radio"
                name={`${id}-target`}
                value={value}
                checked={target === value}
                onChange={() => {
                  setTarget(value);
                  setTyped('');
                }}
              />{' '}
              {value === 'existing'
                ? t('databaseManager.restore.existing', {
                    name: preview.existingName ?? '',
                    interpolation: { escapeValue: false },
                  })
                : t('databaseManager.restore.new')}
            </label>
          ))}
        </fieldset>
      ) : (
        <p className={connectionStyles.hint}>{t('databaseManager.restore.onlyNew')}</p>
      )}
      {target === 'existing' ? (
        <p className={connectionStyles.warning}>{t('databaseManager.restore.overwrite')}</p>
      ) : (
        <Field
          label={t(
            profile.engine === 'sqlite'
              ? 'databaseManager.restore.fileName'
              : 'databaseManager.restore.databaseName',
          )}
        >
          {(props) => (
            <input
              {...props}
              className={connectionStyles.input}
              value={name}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setName(event.target.value)}
            />
          )}
        </Field>
      )}
      {preview.restoreProfileRequired ? (
        roles === null ? (
          <p className={connectionStyles.hint}>{t('databaseManager.loading')}</p>
        ) : roles.length === 0 ? (
          <p className={connectionStyles.error}>{t('databaseManager.restore.noRole')}</p>
        ) : (
          <Field
            label={t('databaseManager.restore.role')}
            hint={t('databaseManager.restore.roleHint')}
          >
            {(props) => (
              <select
                {...props}
                className={connectionStyles.input}
                value={role}
                onChange={(event) => setRole(event.target.value)}
              >
                {roles.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            )}
          </Field>
        )
      ) : null}
      {expected !== '' ? (
        <Field
          label={t('databaseManager.restore.type', {
            name: expected,
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
      <ProblemLine problem={problem} />
      <div className={connectionStyles.actions}>
        <Button
          type="submit"
          variant="primary"
          disabled={
            busy ||
            expected === '' ||
            typed !== expected ||
            (preview.restoreProfileRequired && role === '')
          }
        >
          {t('databaseManager.restore.run')}
        </Button>
      </div>
    </form>
  );
}

function DeletePart({
  fetcher,
  connectionId,
  profile,
  onChanged,
  onRefused,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  profile: ProfileItem;
  onChanged: () => void;
  onRefused: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const postgres = profile.engine !== 'sqlite';
  const [name, setName] = useState('');
  const [preview, setPreview] = useState<DeletePreview | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);

  const look = async () => {
    setProblem(null);
    setPreview(null);
    setBusy(true);
    const result = await previewDelete(fetcher, connectionId, postgres ? name : null);
    setBusy(false);
    if (result.ok) setPreview(result.value);
    else if (LOCKED.has(result.problem.code)) onRefused(result.problem);
    else setProblem(result.problem);
  };

  return (
    <section
      aria-labelledby={`${id}-title`}
      className={connectionStyles.form}
      data-testid="database-manager-delete-part"
    >
      <h3 id={`${id}-title`}>{t('databaseManager.delete.title')}</h3>
      <p className={connectionStyles.hint}>
        {t(postgres ? 'databaseManager.delete.leadPostgres' : 'databaseManager.delete.leadSqlite')}
      </p>
      {postgres ? (
        <Field label={t('databaseManager.delete.databaseName')}>
          {(props) => (
            <input
              {...props}
              className={connectionStyles.input}
              value={name}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => {
                setName(event.target.value);
                setPreview(null);
              }}
              data-testid="database-manager-delete-name"
            />
          )}
        </Field>
      ) : null}
      <div className={connectionStyles.actions}>
        <Button
          onClick={() => void look()}
          disabled={busy || (postgres && name === '')}
          data-testid="database-manager-delete-preview"
        >
          {t('databaseManager.delete.preview')}
        </Button>
      </div>
      <ProblemLine problem={problem} />
      {preview !== null ? (
        <DeleteForm
          key={`${preview.target}:${preview.sessions ?? ''}`}
          fetcher={fetcher}
          connectionId={connectionId}
          preview={preview}
          onDone={onChanged}
          onRefused={onRefused}
        />
      ) : null}
    </section>
  );
}

function DeleteForm({
  fetcher,
  connectionId,
  preview,
  onDone,
  onRefused,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  preview: DeletePreview;
  onDone: () => void;
  onRefused: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const [typed, setTyped] = useState('');
  const [force, setForce] = useState(false);
  const [forceTyped, setForceTyped] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<DeleteResult | null>(null);
  const expected = preview.target;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    setBusy(true);
    const result = await runDelete(fetcher, connectionId, {
      name: preview.engine === 'postgresql' ? expected : null,
      typed,
      force: preview.forceOffered && force,
      forceTyped,
    });
    setBusy(false);
    if (!result.ok) {
      if (LOCKED.has(result.problem.code)) onRefused(result.problem);
      else setProblem(result.problem);
      return;
    }
    setDone(result.value);
    setTyped('');
    setForceTyped('');
    onDone();
  };

  if (done !== null) {
    return (
      <p role="status" className={connectionStyles.status} data-testid="database-manager-deleted">
        {t('databaseManager.delete.done', {
          target: done.target,
          file: done.preBackup,
          interpolation: { escapeValue: false },
        })}
        {done.auditPending ? ` ${t('databaseManager.auditPending')}` : ''}
      </p>
    );
  }

  const ready = typed === expected && (!force || (preview.forceOffered && forceTyped === expected));

  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => void submit(event)}
      data-testid="database-manager-delete"
    >
      <p className={connectionStyles.warning} data-testid="database-manager-delete-warning">
        {t(
          preview.engine === 'postgresql'
            ? 'databaseManager.delete.warningPostgres'
            : 'databaseManager.delete.warningSqlite',
          { name: expected, interpolation: { escapeValue: false } },
        )}
      </p>
      {preview.sessions !== null ? (
        <p className={connectionStyles.hint} data-testid="database-manager-delete-sessions">
          {t('databaseManager.delete.sessions', { count: preview.sessions })}
        </p>
      ) : null}
      {preview.sessions !== null && preview.sessions > 0 && !preview.forceOffered ? (
        <p className={connectionStyles.hint}>{t('databaseManager.delete.forceUnavailable')}</p>
      ) : null}
      <Field
        label={t('databaseManager.delete.type', {
          name: expected,
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
            data-testid="database-manager-delete-typed"
          />
        )}
      </Field>
      {preview.forceOffered ? (
        <>
          <label className={connectionStyles.check}>
            <input
              type="checkbox"
              checked={force}
              onChange={(event) => {
                setForce(event.target.checked);
                setForceTyped('');
              }}
              data-testid="database-manager-delete-force"
            />
            {t('databaseManager.delete.force')}
          </label>
          {force ? (
            <Field
              label={t('databaseManager.delete.forceType', {
                name: expected,
                interpolation: { escapeValue: false },
              })}
            >
              {(props) => (
                <input
                  {...props}
                  className={connectionStyles.input}
                  value={forceTyped}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(event) => setForceTyped(event.target.value)}
                  data-testid="database-manager-delete-force-typed"
                />
              )}
            </Field>
          ) : null}
        </>
      ) : null}
      <ProblemLine problem={problem} />
      <div className={connectionStyles.actions}>
        <Button
          type="submit"
          variant="danger"
          disabled={busy || !ready}
          data-testid="database-manager-delete-run"
        >
          {t('databaseManager.delete.run')}
        </Button>
      </div>
    </form>
  );
}
