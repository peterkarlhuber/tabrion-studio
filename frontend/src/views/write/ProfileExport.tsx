/**
 * The profile export of the Schema Designer area (STU-CR-6 part 1; TX-30, GS3-22, W-10; STU-ADR-0007 section 12).
 * Rendered only while the server says the `schema_designer` flag is on; the server refuses the route while it is off
 * (405) and decides everything that matters.
 *
 * The user picks read profiles, names a new file (`*.tabrion-profile.toml`) and types its name to confirm. The page
 * sends only the profiles' ids, the path and the typed name: the server writes each profile from an allowlist of its
 * fields, read only, with an `env:` reference at most (a keyring or file reference becomes `prompt`), refuses a
 * profile with several hosts, and creates the file (`0600`) without ever overwriting one. No write or admin profile
 * is offered, and no secret is ever read or sent.
 */
import { useEffect, useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import type { Problem, ProblemIssue } from '../../api/problem';
import { EXPORT_SUFFIX, exportProfiles, type ExportedFile } from '../../api/profileExport';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import { fileName } from '../../api/schemaDesigner';
import { fetchWriteAreas } from '../../api/write';
import { Button } from '../../components/ui/Button';
import { visible } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { issueText, problemText } from '../connections/messages';
import styles from '../Views.module.css';

export function ProfileExportSection({ fetcher = fetch }: { fetcher?: typeof fetch }) {
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
  return <ProfileExport fetcher={fetcher} />;
}

/** The issues of `problem` for one field (`export.<field>`). */
function fieldIssues(problem: Problem | null, field: string): ProblemIssue[] {
  return (problem?.issues ?? []).filter((issue) => issue.pointer === `export.${field}`);
}

/** What the file says about a profile's password: an `env:` reference stays, any other becomes `prompt`. */
function passwordNote(profile: ProfileItem): 'none' | 'env' | 'prompt' {
  if (profile.password === null) return 'none';
  return profile.password.source === 'env' ? 'env' : 'prompt';
}

function ProfileExport({ fetcher }: { fetcher: typeof fetch }) {
  const { t } = useTranslation();
  const id = useId();
  const [profiles, setProfiles] = useState<ProfileItem[] | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [path, setPath] = useState('');
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [done, setDone] = useState<ExportedFile | null>(null);
  const name = fileName(path);

  useEffect(() => {
    let alive = true;
    void fetchProfiles(fetcher).then((list) => {
      if (alive) setProfiles(list?.profiles ?? []);
    });
    return () => {
      alive = false;
    };
  }, [fetcher]);

  function toggle(profileId: string, on: boolean) {
    setProblem(null);
    setChosen((current) =>
      on ? [...current, profileId] : current.filter((item) => item !== profileId),
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (chosen.length === 0 || name === '' || typed !== name) return;
    setBusy(true);
    setProblem(null);
    // In the order the page lists them, so the file follows the list.
    const ids = (profiles ?? []).map((item) => item.id).filter((item) => chosen.includes(item));
    const result = await exportProfiles(fetcher, ids, path, typed);
    setBusy(false);
    if (!result.ok) {
      setProblem(result.problem);
      return;
    }
    setDone(result.value);
    setTyped('');
  }

  let body;
  if (profiles === null) {
    body = <p className={connectionStyles.hint}>{t('profileExport.loading')}</p>;
  } else if (done !== null) {
    body = (
      <div className={connectionStyles.form}>
        <p role="status" className={connectionStyles.status} data-testid="profile-export-done">
          {t('profileExport.done', {
            count: done.profiles,
            path: visible(done.path),
            interpolation: { escapeValue: false },
          })}
          {done.auditPending ? ` ${t('profileExport.auditPending')}` : ''}
        </p>
        <div className={connectionStyles.actions}>
          <Button
            onClick={() => {
              setDone(null);
              setPath('');
            }}
          >
            {t('profileExport.another')}
          </Button>
        </div>
      </div>
    );
  } else {
    const listIssues = fieldIssues(problem, 'profiles');
    const listError = `${id}-profiles-error`;
    body = (
      <form
        className={connectionStyles.form}
        onSubmit={(event) => void submit(event)}
        noValidate
        data-testid="profile-export-form"
      >
        {profiles.length === 0 ? (
          <p className={connectionStyles.hint} data-testid="profile-export-none">
            {t('profileExport.none')}
          </p>
        ) : (
          <fieldset
            className={connectionStyles.fieldset}
            aria-describedby={listIssues.length > 0 ? listError : undefined}
          >
            <legend className={connectionStyles.label}>{t('profileExport.profiles')}</legend>
            <div className={connectionStyles.choicesColumn}>
              {profiles.map((profile) => (
                <label key={profile.id} className={connectionStyles.check}>
                  <input
                    type="checkbox"
                    checked={chosen.includes(profile.id)}
                    onChange={(event) => toggle(profile.id, event.target.checked)}
                    data-testid={`profile-export-pick-${profile.id}`}
                  />
                  {t('profileExport.profileLine', {
                    name: visible(profile.name),
                    source: t(`profileExport.sources.${profile.source}`),
                    environment: profile.environment.toUpperCase(),
                    password: t(`profileExport.password.${passwordNote(profile)}`),
                    interpolation: { escapeValue: false },
                  })}
                </label>
              ))}
            </div>
            {listIssues.length > 0 ? (
              <ul id={listError} className={connectionStyles.issues}>
                {listIssues.map((issue) => (
                  <li key={issue.code}>{issueText(t, issue)}</li>
                ))}
              </ul>
            ) : null}
          </fieldset>
        )}
        <Field
          label={t('profileExport.path')}
          hint={t('profileExport.pathHint', { suffix: EXPORT_SUFFIX })}
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
              data-testid="profile-export-path"
            />
          )}
        </Field>
        <p className={connectionStyles.hint}>{t('profileExport.rules')}</p>
        {name !== '' ? (
          <Field
            label={t('profileExport.type', {
              name: visible(name),
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
                data-testid="profile-export-typed"
              />
            )}
          </Field>
        ) : null}
        {problem !== null ? (
          <p role="alert" className={connectionStyles.error} data-testid="profile-export-problem">
            {problemText(t, problem)}
          </p>
        ) : null}
        <div className={connectionStyles.actions}>
          <Button
            type="submit"
            variant="primary"
            disabled={busy || chosen.length === 0 || name === '' || typed !== name}
            data-testid="profile-export-submit"
          >
            {t('profileExport.submit')}
          </Button>
        </div>
      </form>
    );
  }
  return (
    <section className={styles.card} aria-labelledby={`${id}-title`} data-testid="profile-export">
      <h2 id={`${id}-title`} className={styles.cardTitle}>
        {t('profileExport.title')}
      </h2>
      <p className={styles.lead}>{t('profileExport.lead')}</p>
      {body}
    </section>
  );
}
