/**
 * The runtime-role wizard (STU-CR-6 part 2; STU-ADR-0007 section 12, TX-30; gates G-S3 and G-S4, not granted).
 * Shown only on an unlocked admin connection of a PostgreSQL DEV or TEST profile and only while the server says both
 * the `schema_designer` and the `database_manager` flag are on; it uses the Database Manager's admin unlock (the
 * Schema Designer has none of its own). The server decides everything that matters and refuses the rest with the
 * role rules of the Database Manager.
 *
 * - **What it does:** create a login role (or take an existing one again, for example after a migration) and grant it
 *   the rows of one schema: read and write its tables, use its sequences, run its functions, connect to the
 *   database; never change the schema. The protected tables (the migration bookkeeping) stay read-only.
 * - **Preview first:** it changes nothing and shows the role's privileges, what refuses the run and the warnings;
 *   the run is confirmed by typing the role's name. The password of a new role is sent once with the run and then
 *   cleared; never in the preview, never kept.
 */
import { useEffect, useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import {
  applyRuntimeRole,
  previewRuntimeRole,
  type RuntimeRolePreview,
  type RuntimeRoleRequest,
  type RuntimeRoleResult,
} from '../../api/databaseManager';
import type { Problem } from '../../api/problem';
import { fetchWriteAreas } from '../../api/write';
import { Button } from '../../components/ui/Button';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { PrivilegesLine, reasonText, RoleProblem, warningText } from './DatabaseManagerRoles';

/** The migration bookkeeping a runtime role may only read, as suggested (one name per line). */
const DEFAULT_PROTECTED = 'alembic_version';

export function RuntimeRolePart({
  fetcher,
  connectionId,
  onRefused,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  onRefused: (why: Problem) => void;
}) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    let alive = true;
    void fetchWriteAreas(fetcher).then((areas) => {
      if (alive) setOn(areas.schema_designer && areas.database_manager);
    });
    return () => {
      alive = false;
    };
  }, [fetcher]);
  if (!on) return null;
  return <RuntimeRoleForm fetcher={fetcher} connectionId={connectionId} onRefused={onRefused} />;
}

function RuntimeRoleForm({
  fetcher,
  connectionId,
  onRefused,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  onRefused: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [role, setRole] = useState('');
  const [schema, setSchema] = useState('public');
  const [protectedText, setProtectedText] = useState(DEFAULT_PROTECTED);
  const [create, setCreate] = useState(true);
  const [preview, setPreview] = useState<{
    request: RuntimeRoleRequest;
    seen: RuntimeRolePreview;
  } | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);

  const changed = () => {
    setPreview(null);
    setProblem(null);
  };

  const look = async () => {
    changed();
    const request: RuntimeRoleRequest = {
      role,
      schema,
      create,
      protectedTables: protectedText
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line !== ''),
    };
    setBusy(true);
    const result = await previewRuntimeRole(fetcher, connectionId, request);
    setBusy(false);
    if (result.ok) setPreview({ request, seen: result.value });
    else {
      onRefused(result.problem);
      setProblem(result.problem);
    }
  };

  return (
    <section
      aria-labelledby={`${id}-title`}
      className={connectionStyles.form}
      data-testid="database-manager-runtime-role"
    >
      <h3 id={`${id}-title`}>{t('databaseManager.runtimeRole.title')}</h3>
      <p className={connectionStyles.hint}>{t('databaseManager.runtimeRole.lead')}</p>
      <fieldset className={connectionStyles.fieldset}>
        <legend className={connectionStyles.label}>{t('databaseManager.runtimeRole.kind')}</legend>
        {([true, false] as const).map((value) => (
          <label key={String(value)} className={connectionStyles.check}>
            <input
              type="radio"
              name={`${id}-create`}
              checked={create === value}
              onChange={() => {
                setCreate(value);
                changed();
              }}
              data-testid={`database-manager-runtime-${value ? 'create' : 'existing'}`}
            />
            {t(
              value ? 'databaseManager.runtimeRole.create' : 'databaseManager.runtimeRole.existing',
            )}
          </label>
        ))}
      </fieldset>
      <Field label={t('databaseManager.runtimeRole.role')}>
        {(props) => (
          <input
            {...props}
            className={connectionStyles.input}
            value={role}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => {
              setRole(event.target.value);
              changed();
            }}
            data-testid="database-manager-runtime-role-name"
          />
        )}
      </Field>
      <Field label={t('databaseManager.runtimeRole.schema')}>
        {(props) => (
          <input
            {...props}
            className={connectionStyles.input}
            value={schema}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => {
              setSchema(event.target.value);
              changed();
            }}
            data-testid="database-manager-runtime-schema"
          />
        )}
      </Field>
      <Field
        label={t('databaseManager.runtimeRole.protected')}
        hint={t('databaseManager.runtimeRole.protectedHint')}
      >
        {(props) => (
          <textarea
            {...props}
            className={connectionStyles.input}
            rows={2}
            value={protectedText}
            spellCheck={false}
            onChange={(event) => {
              setProtectedText(event.target.value);
              changed();
            }}
            data-testid="database-manager-runtime-protected"
          />
        )}
      </Field>
      <div className={connectionStyles.actions}>
        <Button
          onClick={() => void look()}
          disabled={busy || role === '' || schema === ''}
          data-testid="database-manager-runtime-preview"
        >
          {t('databaseManager.runtimeRole.preview')}
        </Button>
      </div>
      <RoleProblem problem={problem} />
      {preview !== null ? (
        <RuntimeConfirm
          key={preview.seen.state}
          fetcher={fetcher}
          connectionId={connectionId}
          request={preview.request}
          seen={preview.seen}
          onRefused={onRefused}
        />
      ) : null}
    </section>
  );
}

function RuntimeConfirm({
  fetcher,
  connectionId,
  request,
  seen,
  onRefused,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  request: RuntimeRoleRequest;
  seen: RuntimeRolePreview;
  onRefused: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const [typed, setTyped] = useState('');
  const [password, setPassword] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<RuntimeRoleResult | null>(null);
  const refusedNow = seen.refusals.length > 0;
  const none = t('databaseManager.roles.noneListed');

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    setBusy(true);
    const sent = request.create && password !== '' ? password : null;
    setPassword('');
    const result = await applyRuntimeRole(fetcher, connectionId, request, {
      state: seen.state,
      typed,
      password: sent,
    });
    setBusy(false);
    if (!result.ok) {
      onRefused(result.problem);
      setProblem(result.problem);
      return;
    }
    setDone(result.value);
    setTyped('');
  };

  if (done !== null) {
    return (
      <div
        role="status"
        className={connectionStyles.status}
        data-testid="database-manager-runtime-done"
      >
        <p>
          {t(
            done.created
              ? 'databaseManager.runtimeRole.doneCreated'
              : 'databaseManager.runtimeRole.doneGranted',
            {
              role: done.role,
              schema: done.schema,
              count: done.tables,
              interpolation: { escapeValue: false },
            },
          )}
          {done.auditPending ? ` ${t('databaseManager.auditPending')}` : ''}
        </p>
        {!done.connect ? (
          <p className={connectionStyles.warning}>{t('databaseManager.runtimeRole.noConnect')}</p>
        ) : null}
        {done.canCreate ? (
          <p className={connectionStyles.warning}>{t('databaseManager.runtimeRole.canCreate')}</p>
        ) : null}
      </div>
    );
  }

  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => void submit(event)}
      data-testid="database-manager-runtime-confirm"
    >
      <p className={connectionStyles.hint}>
        {t('databaseManager.roles.previewOn', {
          database: seen.database,
          environment: seen.environment ?? '',
          interpolation: { escapeValue: false },
        })}
      </p>
      <ul className={connectionStyles.issues} data-testid="database-manager-runtime-seen">
        {seen.target !== null ? (
          <PrivilegesLine label={t('databaseManager.roles.targetLabel')} view={seen.target} />
        ) : null}
        {seen.schema !== null ? (
          <li>
            {t('databaseManager.runtimeRole.schemaLine', {
              count: seen.schema.tables,
              found: seen.schema.protectedFound.join(', ') || none,
              missing: seen.schema.protectedMissing.join(', ') || none,
              interpolation: { escapeValue: false },
            })}
          </li>
        ) : null}
      </ul>
      {seen.k2 !== null ? (
        <p className={connectionStyles.hint} data-testid="database-manager-runtime-k2">
          {t('databaseManager.roles.k2', {
            checked: seen.k2.checked.join(', ') || none,
            unchecked: seen.k2.unchecked.join(', ') || none,
            noConnect: seen.k2.noConnect,
            interpolation: { escapeValue: false },
          })}
        </p>
      ) : null}
      {refusedNow ? (
        <div
          role="alert"
          className={connectionStyles.error}
          data-testid="database-manager-runtime-refusals"
        >
          <p>{t('databaseManager.runtimeRole.refused')}</p>
          <ul className={connectionStyles.issues}>
            {seen.refusals.map((code) => (
              <li key={code}>{reasonText(t, code)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {seen.warnings.length > 0 ? (
        <ul className={connectionStyles.issues} data-testid="database-manager-runtime-warnings">
          {seen.warnings.map((code) => (
            <li key={code} className={connectionStyles.warning}>
              {warningText(t, code)}
            </li>
          ))}
        </ul>
      ) : null}
      {!refusedNow ? (
        <>
          {request.create ? (
            <Field
              label={t('databaseManager.roles.password')}
              hint={t('databaseManager.runtimeRole.passwordHint')}
            >
              {(props) => (
                <input
                  {...props}
                  type="password"
                  className={connectionStyles.input}
                  value={password}
                  autoComplete="new-password"
                  onChange={(event) => setPassword(event.target.value)}
                  data-testid="database-manager-runtime-password"
                />
              )}
            </Field>
          ) : null}
          <Field
            label={t('databaseManager.roles.type', {
              name: seen.typed,
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
                data-testid="database-manager-runtime-typed"
              />
            )}
          </Field>
          <RoleProblem problem={problem} />
          <div className={connectionStyles.actions}>
            <Button
              type="submit"
              variant="primary"
              disabled={busy || typed !== seen.typed}
              data-testid="database-manager-runtime-apply"
            >
              {t(
                request.create
                  ? 'databaseManager.runtimeRole.applyCreate'
                  : 'databaseManager.runtimeRole.applyGrant',
              )}
            </Button>
          </div>
        </>
      ) : null}
    </form>
  );
}
