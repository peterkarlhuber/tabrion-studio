/**
 * Profile pairs, import and export of the Database Manager (STU-XDM-5; STU-ADR-0008 section 8; TX-45, GS4-21).
 * Part of the Database Manager's card, so it shows only while the server says the `database_manager` flag is on.
 *
 * - **Pairs:** each of the user's read profiles with the write and admin profiles coupled to it and whether the pair
 *   of server and secret reference is confirmed (I-109 j). Pairs are made in "Write and admin profiles"; a PROD
 *   profile has none.
 * - **Export:** read profiles to a new `*.tabrion-profile.toml`. A preview first (writes nothing; names the write and
 *   admin profiles that are left out), then the file name typed. Never a secret, never a write or admin profile.
 * - **Import:** a preview of the file first (changes nothing), then the profiles chosen, each under its own or a new
 *   name (never over an existing profile), and the file name typed. Every imported profile is a read profile; an
 *   `env:` reference stays, any other password reference becomes one the user enters when connecting.
 *
 * No action button is the default focus; the server decides everything that matters.
 */
import { useEffect, useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import {
  PROFILE_FILE_SUFFIX,
  previewProfileExport,
  previewProfileImport,
  runProfileExport,
  runProfileImport,
  type ExportPreview,
  type ExportResult,
  type ImportPreview,
  type ImportPreviewProfile,
  type ImportResult,
} from '../../api/dbmProfiles';
import type { Problem, ProblemIssue } from '../../api/problem';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import { fileName } from '../../api/schemaDesigner';
import { fetchWriteProfiles, type WriteProfileItem } from '../../api/write';
import { Button } from '../../components/ui/Button';
import { visible } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { issueText, problemText } from '../connections/messages';

type T = ReturnType<typeof useTranslation>['t'];

function Refusal({ problem, testId }: { problem: Problem | null; testId: string }) {
  const { t } = useTranslation();
  if (problem === null) return null;
  const issues = problem.issues ?? [];
  return (
    <div role="alert" className={connectionStyles.error} data-testid={testId}>
      <p>{problemText(t, problem)}</p>
      {issues.length > 0 ? (
        <ul className={connectionStyles.issues}>
          {issues.map((issue) => (
            <li key={`${issue.pointer} ${issue.code}`}>{issueText(t, issue)}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function fieldIssues(problem: Problem | null, prefix: string, field: string): ProblemIssue[] {
  return (problem?.issues ?? []).filter((issue) => issue.pointer === `${prefix}.${field}`);
}

export function ProfilesPart({ fetcher }: { fetcher: typeof fetch }) {
  const { t } = useTranslation();
  const id = useId();
  const [reads, setReads] = useState<ProfileItem[] | null>(null);
  const [writes, setWrites] = useState<WriteProfileItem[]>([]);
  /** Counts the imports: the pairs and the export's list are read again after each. */
  const [changes, setChanges] = useState(0);

  useEffect(() => {
    let alive = true;
    void Promise.all([fetchProfiles(fetcher), fetchWriteProfiles(fetcher)]).then(
      ([list, pairs]) => {
        if (!alive) return;
        setReads(list?.profiles ?? []);
        setWrites(pairs);
      },
    );
    return () => {
      alive = false;
    };
  }, [fetcher, changes]);

  return (
    <section
      aria-labelledby={`${id}-title`}
      className={connectionStyles.form}
      data-testid="dbm-profiles"
    >
      <h3 id={`${id}-title`}>{t('dbmProfiles.title')}</h3>
      <p className={connectionStyles.hint}>{t('dbmProfiles.lead')}</p>
      {reads === null ? (
        <p className={connectionStyles.hint}>{t('dbmProfiles.loading')}</p>
      ) : (
        <>
          <Pairs reads={reads} writes={writes} />
          <ExportPart key={`export-${changes}`} fetcher={fetcher} reads={reads} />
          <ImportPart fetcher={fetcher} onImported={() => setChanges((value) => value + 1)} />
        </>
      )}
    </section>
  );
}

// --- pairs -----------------------------------------------------------------------------------------------------------

function pairState(write: WriteProfileItem): 'confirmed' | 'unconfirmed' | 'none' {
  if (write.confirmed === null) return 'none';
  return write.confirmed ? 'confirmed' : 'unconfirmed';
}

function Pairs({ reads, writes }: { reads: ProfileItem[]; writes: WriteProfileItem[] }) {
  const { t } = useTranslation();
  const id = useId();
  const own = reads.filter((item) => item.source === 'user');
  return (
    <div aria-labelledby={`${id}-title`} role="group">
      <h4 id={`${id}-title`}>{t('dbmProfiles.pairs.title')}</h4>
      <p className={connectionStyles.hint}>{t('dbmProfiles.pairs.lead')}</p>
      {own.length === 0 ? (
        <p className={connectionStyles.hint}>{t('dbmProfiles.pairs.none')}</p>
      ) : (
        <ul className={connectionStyles.issues} data-testid="dbm-pairs">
          {own.map((read) => {
            const coupled = writes.filter((write) => write.readProfileId === read.id);
            return (
              <li key={read.id} data-testid={`dbm-pair-${read.name}`}>
                {t('dbmProfiles.pairs.read', {
                  name: visible(read.name),
                  environment: read.environment.toUpperCase(),
                  interpolation: { escapeValue: false },
                })}{' '}
                {read.environment === 'prod'
                  ? t('dbmProfiles.pairs.prod')
                  : coupled.length === 0
                    ? t('dbmProfiles.pairs.unpaired')
                    : coupled
                        .map((write) =>
                          t(`dbmProfiles.pairs.partner.${pairState(write)}`, {
                            name: visible(write.name),
                            kind: t(`dbmProfiles.kinds.${write.kind}`),
                            interpolation: { escapeValue: false },
                          }),
                        )
                        .join(' ')}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// --- export ----------------------------------------------------------------------------------------------------------

function ExportPart({ fetcher, reads }: { fetcher: typeof fetch; reads: ProfileItem[] }) {
  const { t } = useTranslation();
  const id = useId();
  const [chosen, setChosen] = useState<string[]>([]);
  const [path, setPath] = useState('');
  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [done, setDone] = useState<ExportResult | null>(null);
  // In the order the page lists them, so the file follows the list.
  const ids = reads.map((item) => item.id).filter((item) => chosen.includes(item));

  function reset() {
    setPreview(null);
    setTyped('');
    setProblem(null);
    setDone(null);
  }

  async function look() {
    reset();
    setBusy(true);
    const result = await previewProfileExport(fetcher, ids, path);
    setBusy(false);
    if (result.ok) setPreview(result.value);
    else setProblem(result.problem);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (preview === null || typed !== preview.fileName) return;
    setBusy(true);
    setProblem(null);
    const result = await runProfileExport(fetcher, ids, path, typed, preview.state);
    setBusy(false);
    if (!result.ok) {
      setProblem(result.problem);
      return;
    }
    setDone(result.value);
    setPreview(null);
    setTyped('');
  }

  return (
    <div aria-labelledby={`${id}-title`} role="group" data-testid="dbm-export">
      <h4 id={`${id}-title`}>{t('dbmProfiles.export.title')}</h4>
      <p className={connectionStyles.hint}>{t('dbmProfiles.export.lead')}</p>
      {reads.length === 0 ? (
        <p className={connectionStyles.hint}>{t('dbmProfiles.export.none')}</p>
      ) : (
        <fieldset className={connectionStyles.fieldset}>
          <legend className={connectionStyles.label}>{t('dbmProfiles.export.profiles')}</legend>
          <div className={connectionStyles.choicesColumn}>
            {reads.map((profile) => (
              <label key={profile.id} className={connectionStyles.check}>
                <input
                  type="checkbox"
                  checked={chosen.includes(profile.id)}
                  onChange={(event) => {
                    reset();
                    setChosen((current) =>
                      event.target.checked
                        ? [...current, profile.id]
                        : current.filter((item) => item !== profile.id),
                    );
                  }}
                  data-testid={`dbm-export-pick-${profile.id}`}
                />
                {t('dbmProfiles.export.profileLine', {
                  name: visible(profile.name),
                  source: t(`dbmProfiles.sources.${profile.source}`),
                  environment: profile.environment.toUpperCase(),
                  interpolation: { escapeValue: false },
                })}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <Field
        label={t('dbmProfiles.export.path')}
        hint={t('dbmProfiles.pathHint', { suffix: PROFILE_FILE_SUFFIX })}
        issues={fieldIssues(problem, 'export', 'path')}
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
              reset();
            }}
            data-testid="dbm-export-path"
          />
        )}
      </Field>
      <p className={connectionStyles.hint}>{t('dbmProfiles.export.rules')}</p>
      <div className={connectionStyles.actions}>
        <Button
          onClick={() => void look()}
          disabled={busy || ids.length === 0 || fileName(path) === ''}
          data-testid="dbm-export-preview"
        >
          {t('dbmProfiles.export.preview')}
        </Button>
      </div>
      <Refusal problem={problem} testId="dbm-export-problem" />
      {done !== null ? (
        <p role="status" className={connectionStyles.status} data-testid="dbm-export-done">
          {t('dbmProfiles.export.done', {
            count: done.profiles,
            file: visible(done.fileName),
            interpolation: { escapeValue: false },
          })}
          {done.auditPending ? ` ${t('dbmProfiles.auditPending')}` : ''}
        </p>
      ) : null}
      {preview !== null ? (
        <form
          className={connectionStyles.form}
          onSubmit={(event) => void submit(event)}
          noValidate
          data-testid="dbm-export-form"
        >
          <ul className={connectionStyles.issues} aria-label={t('dbmProfiles.export.written')}>
            {preview.profiles.map((item) => (
              <li key={item.id}>
                {t('dbmProfiles.export.writtenLine', {
                  name: visible(item.name),
                  environment: (item.environment ?? '').toUpperCase(),
                  password: t(`dbmProfiles.export.password.${item.password ?? 'none'}`),
                  interpolation: { escapeValue: false },
                })}
              </li>
            ))}
          </ul>
          {preview.leftOut.length > 0 ? (
            <p className={connectionStyles.hint} data-testid="dbm-export-left-out">
              {t('dbmProfiles.export.leftOut', {
                names: preview.leftOut
                  .map((item) =>
                    t('dbmProfiles.export.leftOutItem', {
                      name: visible(item.name),
                      kind: t(`dbmProfiles.kinds.${item.kind}`),
                      interpolation: { escapeValue: false },
                    }),
                  )
                  .join(', '),
                interpolation: { escapeValue: false },
              })}
            </p>
          ) : null}
          <Field
            label={t('dbmProfiles.type', {
              name: visible(preview.fileName),
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
                data-testid="dbm-export-typed"
              />
            )}
          </Field>
          <div className={connectionStyles.actions}>
            <Button
              type="submit"
              variant="primary"
              disabled={busy || typed !== preview.fileName}
              data-testid="dbm-export-run"
            >
              {t('dbmProfiles.export.submit')}
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

// --- import ----------------------------------------------------------------------------------------------------------

/** The `import.*` and `config.*` reasons a profile of the file is not imported, as text. */
function refusalText(t: T, code: string): string {
  return issueText(t, { pointer: 'import.profiles', code });
}

function target(t: T, item: ImportPreviewProfile): string {
  if (item.engine === 'sqlite') return visible(item.path ?? '');
  return t('dbmProfiles.import.server', {
    host: visible(item.host ?? ''),
    port: item.port ?? 5432,
    database: visible(item.database ?? ''),
    interpolation: { escapeValue: false },
  });
}

function passwordText(t: T, item: ImportPreviewProfile): string {
  if (item.password === null) return t('dbmProfiles.import.password.none');
  if (item.password.source === 'env') {
    return t('dbmProfiles.import.password.env', {
      name: visible(item.password.target),
      interpolation: { escapeValue: false },
    });
  }
  return t('dbmProfiles.import.password.keyring');
}

function ImportPart({ fetcher, onImported }: { fetcher: typeof fetch; onImported: () => void }) {
  const { t } = useTranslation();
  const id = useId();
  const [path, setPath] = useState('');
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  /** The profiles chosen, by their name in the file, with the name they get. */
  const [names, setNames] = useState<Record<string, string>>({});
  const [typed, setTyped] = useState('');
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [done, setDone] = useState<ImportResult | null>(null);

  function reset() {
    setPreview(null);
    setNames({});
    setTyped('');
    setOverwrite(false);
    setProblem(null);
  }

  async function look() {
    reset();
    setDone(null);
    setBusy(true);
    const result = await previewProfileImport(fetcher, path);
    setBusy(false);
    if (result.ok) setPreview(result.value);
    else setProblem(result.problem);
  }

  const chosen = preview === null ? [] : preview.profiles.filter((item) => item.name in names);
  const ready =
    preview !== null &&
    chosen.length > 0 &&
    chosen.every((item) => (names[item.name] ?? '') !== '') &&
    !chosen.some((item) => item.nameTaken && names[item.name] === item.name) &&
    typed === preview.fileName &&
    (!preview.userFile.handWritten || overwrite);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || preview === null) return;
    setBusy(true);
    setProblem(null);
    const choices = chosen.map((item) => ({ name: item.name, as: names[item.name] ?? item.name }));
    const result = await runProfileImport(fetcher, preview, choices, typed, overwrite);
    setBusy(false);
    if (!result.ok) {
      setProblem(result.problem);
      return;
    }
    setDone(result.value);
    reset();
    onImported();
  }

  return (
    <div aria-labelledby={`${id}-title`} role="group" data-testid="dbm-import">
      <h4 id={`${id}-title`}>{t('dbmProfiles.import.title')}</h4>
      <p className={connectionStyles.hint}>{t('dbmProfiles.import.lead')}</p>
      <Field
        label={t('dbmProfiles.import.path')}
        hint={t('dbmProfiles.pathHint', { suffix: PROFILE_FILE_SUFFIX })}
        issues={fieldIssues(problem, 'import', 'path')}
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
              reset();
            }}
            data-testid="dbm-import-path"
          />
        )}
      </Field>
      <p className={connectionStyles.hint}>{t('dbmProfiles.import.rules')}</p>
      <div className={connectionStyles.actions}>
        <Button
          onClick={() => void look()}
          disabled={busy || fileName(path) === ''}
          data-testid="dbm-import-preview"
        >
          {t('dbmProfiles.import.preview')}
        </Button>
      </div>
      <Refusal problem={problem} testId="dbm-import-problem" />
      {done !== null ? (
        <p role="status" className={connectionStyles.status} data-testid="dbm-import-done">
          {t('dbmProfiles.import.done', {
            count: done.imported.length,
            names: done.imported.map((name) => visible(name)).join(', '),
            interpolation: { escapeValue: false },
          })}
          {done.auditPending ? ` ${t('dbmProfiles.auditPending')}` : ''}
        </p>
      ) : null}
      {preview !== null ? (
        <form
          className={connectionStyles.form}
          onSubmit={(event) => void submit(event)}
          noValidate
          data-testid="dbm-import-form"
        >
          <fieldset className={connectionStyles.fieldset}>
            <legend className={connectionStyles.label}>{t('dbmProfiles.import.profiles')}</legend>
            {preview.profiles.map((item, index) => (
              <ImportRow
                key={item.name}
                index={index}
                item={item}
                newName={names[item.name] ?? null}
                onChange={(value) => {
                  setProblem(null);
                  setNames((current) =>
                    value === null
                      ? Object.fromEntries(
                          Object.entries(current).filter(([name]) => name !== item.name),
                        )
                      : { ...current, [item.name]: value },
                  );
                }}
              />
            ))}
          </fieldset>
          {preview.userFile.handWritten ? (
            <label className={connectionStyles.check}>
              <input
                type="checkbox"
                checked={overwrite}
                onChange={(event) => setOverwrite(event.target.checked)}
                data-testid="dbm-import-overwrite"
              />
              {t('dbmProfiles.import.overwrite')}
            </label>
          ) : null}
          <Field
            label={t('dbmProfiles.type', {
              name: visible(preview.fileName),
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
                data-testid="dbm-import-typed"
              />
            )}
          </Field>
          <div className={connectionStyles.actions}>
            <Button
              type="submit"
              variant="primary"
              disabled={busy || !ready}
              data-testid="dbm-import-run"
            >
              {t('dbmProfiles.import.submit')}
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

function ImportRow({
  index,
  item,
  newName,
  onChange,
}: {
  index: number;
  item: ImportPreviewProfile;
  newName: string | null;
  onChange: (value: string | null) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const picked = newName !== null;
  const taken = picked && newName === item.name && item.nameTaken;
  return (
    <div className={connectionStyles.field} data-testid={`dbm-import-row-${index}`}>
      <label className={connectionStyles.check}>
        <input
          type="checkbox"
          checked={picked}
          disabled={!item.importable}
          aria-describedby={`${id}-details`}
          onChange={(event) => onChange(event.target.checked ? item.name : null)}
          data-testid={`dbm-import-pick-${index}`}
        />
        {t('dbmProfiles.import.profileLine', {
          name: visible(item.name),
          environment: (item.environment ?? '').toUpperCase(),
          interpolation: { escapeValue: false },
        })}
      </label>
      <div id={`${id}-details`}>
        <p className={connectionStyles.hint}>
          {t('dbmProfiles.import.details', {
            target: target(t, item),
            password: passwordText(t, item),
            interpolation: { escapeValue: false },
          })}
        </p>
        {item.notImported.length > 0 ? (
          <p className={connectionStyles.hint}>
            {t('dbmProfiles.import.notImported', { keys: item.notImported.join(', ') })}
          </p>
        ) : null}
        {item.importable ? null : (
          <ul className={connectionStyles.issues} data-testid={`dbm-import-refusals-${index}`}>
            {item.refusals.map((refusal) => (
              <li key={`${refusal.field} ${refusal.code}`}>{refusalText(t, refusal.code)}</li>
            ))}
          </ul>
        )}
        {item.nameTaken && !picked ? (
          <p className={connectionStyles.hint}>{t('dbmProfiles.import.nameTaken')}</p>
        ) : null}
      </div>
      {picked ? (
        <Field
          label={t('dbmProfiles.import.as', {
            name: visible(item.name),
            interpolation: { escapeValue: false },
          })}
          issues={taken ? [{ pointer: 'import.profiles', code: 'import.name_taken' }] : []}
        >
          {(props) => (
            <input
              {...props}
              className={connectionStyles.input}
              value={newName}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => onChange(event.target.value)}
              data-testid={`dbm-import-as-${index}`}
            />
          )}
        </Field>
      ) : null}
    </div>
  );
}
