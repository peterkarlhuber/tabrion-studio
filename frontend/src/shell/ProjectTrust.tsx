/**
 * Asks for project trust once the page has a session (STU-006, D-01, threat T-08).
 *
 * `GET /api/project` says whether `.tabrion/studio.toml` is new (`untrusted`) or changed since it was trusted
 * (`changed`); then D-01 shows the review. "Trust" sends back the digest the dialog showed; the server trusts the file
 * only if it is still that file (otherwise `studio.trust_stale`, and the dialog shows the new content). "Don't load"
 * keeps the project profiles unused until the next load of the page.
 *
 * Review I-2 of stu-006-review.md: after a refusal with `studio.trust_stale` the dialog says that it now shows the new
 * content, and an `invalid` file gets a notice with the issues (key paths and codes, never values) instead of
 * silently missing project profiles.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchProject, trustProject, type ProjectInfo } from '../api/project';
import { TrustProjectDialog } from '../components/dialogs/StudioDialogs';
import { MAX_ENTRIES, visible } from '../lib/visible';
import styles from './ProjectTrust.module.css';

/** The project file cannot be used: say so, with its issues (pointers come from the file, so they are escaped). */
function InvalidNotice({ project, onDismiss }: { project: ProjectInfo; onDismiss: () => void }) {
  const { t } = useTranslation();
  const errors = project.issues.filter((issue) => issue.severity !== 'warning');
  const shown = errors.slice(0, MAX_ENTRIES);
  return (
    <aside
      className={styles.notice}
      role="status"
      aria-labelledby="project-notice-title"
      data-testid="project-invalid"
    >
      <h2 id="project-notice-title" className={styles.title}>
        {t('projectNotice.title')}
      </h2>
      <p className={styles.text}>{t('projectNotice.body', { file: project.profilesFile })}</p>
      <ul className={styles.list}>
        {shown.map((issue, index) => (
          <li key={index}>
            <code>{visible(String(issue.pointer))}</code> <code>{visible(String(issue.code))}</code>
          </li>
        ))}
      </ul>
      {errors.length > shown.length ? (
        <p className={styles.text}>
          {t('dialogs.trustProject.andMore', { count: errors.length - shown.length })}
        </p>
      ) : null}
      <button type="button" className={styles.dismiss} onClick={onDismiss}>
        {t('projectNotice.dismiss')}
      </button>
    </aside>
  );
}

export function ProjectTrust({ fetcher }: { fetcher: typeof fetch }) {
  const [project, setProject] = useState<ProjectInfo | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [stale, setStale] = useState(false);

  const load = useCallback(() => {
    let active = true;
    void fetchProject(fetcher).then((info) => {
      if (active) setProject(info);
    });
    return () => {
      active = false;
    };
  }, [fetcher]);

  useEffect(load, [load]);

  if (project === null || dismissed) return null;
  if (project.state === 'invalid') {
    return <InvalidNotice project={project} onDismiss={() => setDismissed(true)} />;
  }
  if (project.review === null || project.digest === null) return null;
  if (project.state !== 'untrusted' && project.state !== 'changed') return null;
  const digest = project.digest;
  return (
    <TrustProjectDialog
      open
      file={project.profilesFile}
      review={project.review}
      diff={project.state === 'changed' ? project.diff : null}
      stale={stale}
      onReject={() => setDismissed(true)}
      onTrust={() => {
        void trustProject(fetcher, digest).then((problem) => {
          // Success or a refusal (for example a file that changed meanwhile): read the state again either way.
          setStale(problem?.code === 'studio.trust_stale');
          load();
        });
      }}
    />
  );
}
