/**
 * PostgreSQL role management in the Database Manager (STU-XDM-4; E-X6; STU-ADR-0008 section 6, gate G-S4, not
 * granted). Shown only on an unlocked admin connection of a PostgreSQL DEV or TEST profile; the server decides
 * everything that matters and refuses the rest (PROD and its aliases, PostgreSQL before 16, an over-privileged admin
 * role, Studio's own roles, SUPERUSER, BYPASSRLS, CREATEROLE, REPLICATION, server-file roles, K-2 grants).
 *
 * - **Roles:** the server's roles (not the predefined ones) with their attributes; Studio's own roles are marked and
 *   never changed; a name with characters that cannot be seen shows them as `<U+XXXX>` and cannot be acted on.
 * - **Change:** every change (create, alter, drop, membership, privileges) is looked at first (the preview changes
 *   nothing, shows the role's privileges, what refuses the change, warnings and which databases the K-2 check
 *   reached), then confirmed by typing the role's name (a membership: the member's). The apply button is never the
 *   default focus; it is the danger kind for drop and revoke.
 * - **Passwords:** for create and alter only, in a password field that the page sends once with the apply and then
 *   clears; never in the preview, never kept.
 */
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import {
  applyRole,
  DESTRUCTIVE_ROLE_ACTIONS,
  fetchRoles,
  KIND_PRIVILEGES,
  OBJECT_KINDS,
  previewRole,
  ROLE_ACTIONS,
  type ObjectKind,
  type RoleAction,
  type RoleListing,
  type RolePreview,
  type RolePrivilegesView,
  type RoleRequest,
  type RoleResult,
} from '../../api/databaseManager';
import type { Problem } from '../../api/problem';
import { Button } from '../../components/ui/Button';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { problemText } from '../connections/messages';

/** The `role.<code>` issues and preview refusals the page has its own text for. */
const ROLE_ISSUES = new Set([
  'action_invalid',
  'attribute_banned',
  'connection_limit',
  'grant_not_allowed',
  'member_is_role',
  'name_hidden',
  'name_invalid',
  'not_for_action',
  'nothing_to_change',
  'object_missing',
  'objects_invalid',
  'on_invalid',
  'options_incomplete',
  'options_invalid',
  'password_invalid',
  'privileges_invalid',
  'refused',
  'refused_by_tabrion',
  'trigger',
  'valid_until',
  'engine',
  'environment',
  'prod',
  'superuser',
  'bypass_rls',
  'createrole',
  'replication',
  'reachable_superuser',
  'reachable_bypass_rls',
  'reachable_createrole',
  'reachable_replication',
  'reachable_server_access',
  'server_access',
  'owns_objects',
  'current_user',
  'predefined_role',
  'security_definer',
  'schema_create',
  'system_object',
  'object_owner',
  'own_role',
  'own_role_reached',
  'own_role_unknown',
  'privileged_role',
  'escalation_grant',
  'exists',
]);

const ROLE_WARNINGS = new Set([
  'create_on_database',
  'create_on_schema',
  'createdb',
  'escalation_check_partial',
  'public_definers',
  'public_definers_superuser',
  'revoke_grantor',
]);

type T = ReturnType<typeof useTranslation>['t'];

function reasonText(t: T, code: string): string {
  const name = code.startsWith('role.') ? code.slice('role.'.length) : code;
  return ROLE_ISSUES.has(name)
    ? t(`databaseManager.roles.issues.${name}`)
    : t('databaseManager.issues.unknown', { code, interpolation: { escapeValue: false } });
}

function warningText(t: T, code: string): string {
  return ROLE_WARNINGS.has(code)
    ? t(`databaseManager.roles.warnings.${code}`)
    : t('databaseManager.issues.unknown', { code, interpolation: { escapeValue: false } });
}

function RoleProblem({ problem }: { problem: Problem | null }) {
  const { t } = useTranslation();
  if (problem === null) return null;
  const issues = problem.issues ?? [];
  return (
    <div
      role="alert"
      className={connectionStyles.error}
      data-testid="database-manager-roles-problem"
    >
      <p>{problemText(t, problem)}</p>
      {issues.length > 0 ? (
        <ul className={connectionStyles.issues}>
          {issues.map((issue) => (
            <li key={`${issue.pointer}:${issue.code}`}>{reasonText(t, issue.code)}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

const MEMBERSHIP = new Set<RoleAction>(['grant_membership', 'revoke_membership']);
const OPTIONS = new Set<RoleAction>(['create', 'alter']);
const PRIVILEGES = new Set<RoleAction>(['grant_privileges', 'revoke_privileges']);

export function RolesPart({
  fetcher,
  connectionId,
  locked,
  onLocked,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  /** The problems after which the unlock is gone. */
  locked: ReadonlySet<string>;
  onLocked: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [listing, setListing] = useState<RoleListing | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [changes, setChanges] = useState(0);
  /** The latest callback, so that a new one from the parent does not read the list again. */
  const lockedRef = useRef(onLocked);
  useEffect(() => {
    lockedRef.current = onLocked;
  }, [onLocked]);

  useEffect(() => {
    let alive = true;
    void fetchRoles(fetcher, connectionId).then((result) => {
      if (!alive) return;
      if (result.ok) {
        setListing(result.value);
        setProblem(null);
      } else if (locked.has(result.problem.code)) lockedRef.current(result.problem);
      else setProblem(result.problem);
    });
    return () => {
      alive = false;
    };
  }, [fetcher, connectionId, changes, locked]);

  const refused = (why: Problem) => {
    if (locked.has(why.code)) lockedRef.current(why);
  };

  return (
    <section
      aria-labelledby={`${id}-title`}
      className={connectionStyles.form}
      data-testid="database-manager-roles"
    >
      <h3 id={`${id}-title`}>{t('databaseManager.roles.title')}</h3>
      <p className={connectionStyles.hint}>{t('databaseManager.roles.lead')}</p>
      <RoleProblem problem={problem} />
      {listing === null ? (
        problem === null ? (
          <p className={connectionStyles.hint}>{t('databaseManager.loading')}</p>
        ) : null
      ) : (
        <>
          <RoleList listing={listing} />
          <RoleChange
            fetcher={fetcher}
            connectionId={connectionId}
            listing={listing}
            onRefused={refused}
            onChanged={() => setChanges((value) => value + 1)}
          />
        </>
      )}
    </section>
  );
}

function RoleList({ listing }: { listing: RoleListing }) {
  const { t } = useTranslation();
  if (listing.roles.length === 0) {
    return <p className={connectionStyles.hint}>{t('databaseManager.roles.none')}</p>;
  }
  return (
    <>
      <ul
        className={connectionStyles.issues}
        aria-label={t('databaseManager.roles.listLabel')}
        data-testid="database-manager-roles-list"
      >
        {listing.roles.map((role) => (
          <li key={role.name}>
            <code>{role.display}</code>{' '}
            {role.attributes.length > 0
              ? t('databaseManager.roles.attributes', {
                  list: role.attributes.join(', '),
                  interpolation: { escapeValue: false },
                })
              : t('databaseManager.roles.noAttributes')}
            {role.own ? ` ${t('databaseManager.roles.own')}` : ''}
            {role.hidden ? ` ${t('databaseManager.roles.hidden')}` : ''}
          </li>
        ))}
      </ul>
      {listing.truncated ? (
        <p className={connectionStyles.hint}>{t('databaseManager.roles.truncated')}</p>
      ) : null}
    </>
  );
}

interface Draft {
  action: RoleAction;
  role: string;
  member: string;
  login: boolean;
  inherit: boolean;
  createDb: boolean;
  connectionLimit: string;
  validUntil: string;
  on: ObjectKind;
  privileges: string[];
  objects: string;
}

const EMPTY: Draft = {
  action: 'create',
  role: '',
  member: '',
  login: false,
  inherit: true,
  createDb: false,
  connectionLimit: '',
  validUntil: '',
  on: 'table',
  privileges: [],
  objects: '',
};

/** The request of a draft, or the key of the field that is not valid yet. */
function toRequest(draft: Draft): RoleRequest | 'connectionLimit' {
  const request: RoleRequest = { action: draft.action, role: draft.role };
  if (MEMBERSHIP.has(draft.action)) request.member = draft.member;
  if (OPTIONS.has(draft.action)) {
    const limit = draft.connectionLimit.trim();
    if (limit !== '' && !/^\d{1,10}$/.test(limit)) return 'connectionLimit';
    request.options = {
      login: draft.login,
      inherit: draft.inherit,
      create_db: draft.createDb,
      connection_limit: limit === '' ? null : Number(limit),
      valid_until: draft.validUntil.trim() === '' ? null : draft.validUntil.trim(),
    };
  }
  if (PRIVILEGES.has(draft.action)) {
    request.on = draft.on;
    request.privileges = draft.privileges;
    request.objects = draft.objects
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line !== '');
  }
  return request;
}

function RoleChange({
  fetcher,
  connectionId,
  listing,
  onRefused,
  onChanged,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  listing: RoleListing;
  onRefused: (why: Problem) => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [preview, setPreview] = useState<{ request: RoleRequest; seen: RolePreview } | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);

  const change = (next: Partial<Draft>) => {
    setDraft((current) => ({ ...current, ...next }));
    setPreview(null);
    setInvalid(false);
  };

  /** Alter sets every option: start from the role's attributes so nothing changes unasked. */
  const chooseRole = (name: string, action: RoleAction = draft.action) => {
    const known = listing.roles.find((item) => item.name === name);
    if (action === 'alter' && known !== undefined) {
      change({
        action,
        role: name,
        login: known.attributes.includes('login'),
        inherit: known.attributes.includes('inherit'),
        createDb: known.attributes.includes('createdb'),
        connectionLimit: known.connectionLimit === null ? '' : String(known.connectionLimit),
        validUntil: known.validUntil ?? '',
      });
    } else {
      change({ action, role: name });
    }
  };

  const look = async () => {
    setProblem(null);
    setPreview(null);
    const request = toRequest(draft);
    if (request === 'connectionLimit') {
      setInvalid(true);
      return;
    }
    setBusy(true);
    const result = await previewRole(fetcher, connectionId, request);
    setBusy(false);
    if (result.ok) setPreview({ request, seen: result.value });
    else {
      onRefused(result.problem);
      setProblem(result.problem);
    }
  };

  const ready =
    draft.role !== '' &&
    (!MEMBERSHIP.has(draft.action) || draft.member !== '') &&
    (!PRIVILEGES.has(draft.action) || (draft.privileges.length > 0 && draft.objects.trim() !== ''));
  const privileges = KIND_PRIVILEGES[draft.on].filter(
    (name) => draft.action === 'revoke_privileges' || name !== 'TRIGGER',
  );

  return (
    <div className={connectionStyles.form} data-testid="database-manager-roles-change">
      <h4 id={`${id}-title`}>{t('databaseManager.roles.change')}</h4>
      <Field label={t('databaseManager.roles.action')}>
        {(props) => (
          <select
            {...props}
            className={connectionStyles.input}
            value={draft.action}
            onChange={(event) => chooseRole(draft.role, event.target.value as RoleAction)}
            data-testid="database-manager-roles-action"
          >
            {ROLE_ACTIONS.map((action) => (
              <option key={action} value={action}>
                {t(`databaseManager.roles.actions.${action}`)}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field
        label={t(
          MEMBERSHIP.has(draft.action)
            ? 'databaseManager.roles.group'
            : 'databaseManager.roles.role',
        )}
      >
        {(props) => (
          <input
            {...props}
            className={connectionStyles.input}
            value={draft.role}
            autoComplete="off"
            spellCheck={false}
            list={`${id}-roles`}
            onChange={(event) => chooseRole(event.target.value)}
            data-testid="database-manager-roles-role"
          />
        )}
      </Field>
      <datalist id={`${id}-roles`}>
        {listing.roles
          .filter((role) => !role.hidden)
          .map((role) => (
            <option key={role.name} value={role.name} />
          ))}
      </datalist>
      {MEMBERSHIP.has(draft.action) ? (
        <Field label={t('databaseManager.roles.member')}>
          {(props) => (
            <input
              {...props}
              className={connectionStyles.input}
              value={draft.member}
              autoComplete="off"
              spellCheck={false}
              list={`${id}-roles`}
              onChange={(event) => change({ member: event.target.value })}
              data-testid="database-manager-roles-member"
            />
          )}
        </Field>
      ) : null}
      {OPTIONS.has(draft.action) ? (
        <fieldset className={connectionStyles.fieldset}>
          <legend className={connectionStyles.label}>{t('databaseManager.roles.options')}</legend>
          <p className={connectionStyles.hint}>{t('databaseManager.roles.optionsHint')}</p>
          {(
            [
              ['login', draft.login],
              ['inherit', draft.inherit],
              ['createDb', draft.createDb],
            ] as const
          ).map(([key, value]) => (
            <label key={key} className={connectionStyles.check}>
              <input
                type="checkbox"
                checked={value}
                onChange={(event) => change({ [key]: event.target.checked })}
                data-testid={`database-manager-roles-${key}`}
              />
              {t(`databaseManager.roles.option.${key}`)}
            </label>
          ))}
          <Field
            label={t('databaseManager.roles.option.connectionLimit')}
            hint={t('databaseManager.roles.option.connectionLimitHint')}
          >
            {(props) => (
              <input
                {...props}
                className={connectionStyles.input}
                inputMode="numeric"
                value={draft.connectionLimit}
                aria-invalid={invalid ? true : props['aria-invalid']}
                onChange={(event) => change({ connectionLimit: event.target.value })}
                data-testid="database-manager-roles-limit"
              />
            )}
          </Field>
          <Field
            label={t('databaseManager.roles.option.validUntil')}
            hint={t('databaseManager.roles.option.validUntilHint')}
          >
            {(props) => (
              <input
                {...props}
                className={connectionStyles.input}
                value={draft.validUntil}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => change({ validUntil: event.target.value })}
                data-testid="database-manager-roles-valid-until"
              />
            )}
          </Field>
        </fieldset>
      ) : null}
      {PRIVILEGES.has(draft.action) ? (
        <>
          <Field label={t('databaseManager.roles.on')}>
            {(props) => (
              <select
                {...props}
                className={connectionStyles.input}
                value={draft.on}
                onChange={(event) =>
                  change({ on: event.target.value as ObjectKind, privileges: [] })
                }
                data-testid="database-manager-roles-on"
              >
                {OBJECT_KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {t(`databaseManager.roles.kinds.${kind}`)}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <fieldset className={connectionStyles.fieldset}>
            <legend className={connectionStyles.label}>
              {t('databaseManager.roles.privileges')}
            </legend>
            <div className={connectionStyles.choices}>
              {privileges.map((name) => (
                <label key={name} className={connectionStyles.check}>
                  <input
                    type="checkbox"
                    checked={draft.privileges.includes(name)}
                    onChange={(event) =>
                      change({
                        privileges: event.target.checked
                          ? [...draft.privileges, name]
                          : draft.privileges.filter((item) => item !== name),
                      })
                    }
                    data-testid={`database-manager-roles-privilege-${name}`}
                  />
                  <code>{name}</code>
                </label>
              ))}
            </div>
          </fieldset>
          <Field
            label={t('databaseManager.roles.objects')}
            hint={t('databaseManager.roles.objectsHint')}
          >
            {(props) => (
              <textarea
                {...props}
                className={connectionStyles.input}
                rows={3}
                value={draft.objects}
                spellCheck={false}
                onChange={(event) => change({ objects: event.target.value })}
                data-testid="database-manager-roles-objects"
              />
            )}
          </Field>
        </>
      ) : null}
      {invalid ? (
        <p role="alert" className={connectionStyles.error}>
          {t('databaseManager.roles.issues.connection_limit')}
        </p>
      ) : null}
      <div className={connectionStyles.actions}>
        <Button
          onClick={() => void look()}
          disabled={busy || !ready}
          data-testid="database-manager-roles-preview"
        >
          {t('databaseManager.roles.preview')}
        </Button>
      </div>
      <RoleProblem problem={problem} />
      {preview !== null ? (
        <RoleConfirm
          key={preview.seen.state}
          fetcher={fetcher}
          connectionId={connectionId}
          request={preview.request}
          seen={preview.seen}
          onRefused={onRefused}
          onDone={onChanged}
        />
      ) : null}
    </div>
  );
}

function PrivilegesLine({ label, view }: { label: string; view: RolePrivilegesView }) {
  const { t } = useTranslation();
  const none = t('databaseManager.roles.noneListed');
  return (
    <li>
      {t('databaseManager.roles.privilegesLine', {
        label,
        role: view.role,
        attributes: view.attributes.join(', ') || none,
        memberOf: view.memberOf.join(', ') || none,
        dangerous: view.dangerous.join(', ') || none,
        owned: view.ownedObjects,
        interpolation: { escapeValue: false },
      })}
    </li>
  );
}

function RoleConfirm({
  fetcher,
  connectionId,
  request,
  seen,
  onRefused,
  onDone,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  request: RoleRequest;
  seen: RolePreview;
  onRefused: (why: Problem) => void;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [typed, setTyped] = useState('');
  const [password, setPassword] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<RoleResult | null>(null);
  const destructive = DESTRUCTIVE_ROLE_ACTIONS.has(seen.action);
  const refusedNow = seen.refusals.length > 0;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    setBusy(true);
    const sent = OPTIONS.has(request.action) && password !== '' ? password : null;
    setPassword('');
    const result = await applyRole(fetcher, connectionId, request, {
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
    onDone();
  };

  if (done !== null) {
    return (
      <div
        role="status"
        className={connectionStyles.status}
        data-testid="database-manager-roles-done"
      >
        <p>
          {t(`databaseManager.roles.done.${done.action}`, {
            role: done.role,
            member: done.member ?? '',
            interpolation: { escapeValue: false },
          })}
          {done.auditPending ? ` ${t('databaseManager.auditPending')}` : ''}
        </p>
        {done.differences.length > 0 ? (
          <p className={connectionStyles.warning} data-testid="database-manager-roles-differences">
            {t('databaseManager.roles.differences', {
              list: done.differences.join(', '),
              interpolation: { escapeValue: false },
            })}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => void submit(event)}
      data-testid="database-manager-roles-confirm"
    >
      <p className={destructive ? connectionStyles.warning : connectionStyles.hint}>
        {t('databaseManager.roles.previewOn', {
          database: seen.database,
          environment: seen.environment ?? '',
          interpolation: { escapeValue: false },
        })}
      </p>
      <ul className={connectionStyles.issues} data-testid="database-manager-roles-seen">
        {seen.target !== null ? (
          <PrivilegesLine label={t('databaseManager.roles.targetLabel')} view={seen.target} />
        ) : null}
        {seen.member !== null ? (
          <PrivilegesLine label={t('databaseManager.roles.memberLabel')} view={seen.member} />
        ) : null}
      </ul>
      {seen.k2 !== null ? (
        <p className={connectionStyles.hint} data-testid="database-manager-roles-k2">
          {t('databaseManager.roles.k2', {
            checked: seen.k2.checked.join(', ') || t('databaseManager.roles.noneListed'),
            unchecked: seen.k2.unchecked.join(', ') || t('databaseManager.roles.noneListed'),
            noConnect: seen.k2.noConnect,
            interpolation: { escapeValue: false },
          })}
        </p>
      ) : null}
      {refusedNow ? (
        <div
          role="alert"
          className={connectionStyles.error}
          data-testid="database-manager-roles-refusals"
        >
          <p>{t('databaseManager.roles.refused')}</p>
          <ul className={connectionStyles.issues}>
            {seen.refusals.map((code) => (
              <li key={code}>{reasonText(t, code)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {seen.warnings.length > 0 ? (
        <ul className={connectionStyles.issues} data-testid="database-manager-roles-warnings">
          {seen.warnings.map((code) => (
            <li key={code} className={connectionStyles.warning}>
              {warningText(t, code)}
            </li>
          ))}
        </ul>
      ) : null}
      {!refusedNow ? (
        <>
          {OPTIONS.has(request.action) ? (
            <Field
              label={t('databaseManager.roles.password')}
              hint={t('databaseManager.roles.passwordHint')}
            >
              {(props) => (
                <input
                  {...props}
                  type="password"
                  className={connectionStyles.input}
                  value={password}
                  autoComplete="new-password"
                  onChange={(event) => setPassword(event.target.value)}
                  data-testid="database-manager-roles-password"
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
                data-testid="database-manager-roles-typed"
              />
            )}
          </Field>
          <RoleProblem problem={problem} />
          <div className={connectionStyles.actions}>
            <Button
              type="submit"
              variant={destructive ? 'danger' : 'primary'}
              disabled={busy || typed !== seen.typed}
              data-testid="database-manager-roles-apply"
            >
              {t(`databaseManager.roles.apply.${seen.action}`)}
            </Button>
          </div>
        </>
      ) : null}
    </form>
  );
}
