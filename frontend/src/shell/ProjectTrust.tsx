/**
 * Asks for project trust once the page has a session (STU-006, D-01, threat T-08).
 *
 * `GET /api/project` says whether `.tabrion/studio.toml` is new (`untrusted`) or changed since it was trusted
 * (`changed`); then D-01 shows the review. "Trust" sends back the digest the dialog showed; the server trusts the file
 * only if it is still that file (otherwise `studio.trust_stale`, and the dialog shows the new content). "Don't load"
 * keeps the project profiles unused until the next load of the page.
 */
import { useCallback, useEffect, useState } from 'react';

import { fetchProject, trustProject, type ProjectInfo } from '../api/project';
import { TrustProjectDialog } from '../components/dialogs/StudioDialogs';

export function ProjectTrust({ fetcher }: { fetcher: typeof fetch }) {
  const [project, setProject] = useState<ProjectInfo | null>(null);
  const [dismissed, setDismissed] = useState(false);

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

  if (project === null || project.review === null || project.digest === null || dismissed)
    return null;
  if (project.state !== 'untrusted' && project.state !== 'changed') return null;
  const digest = project.digest;
  return (
    <TrustProjectDialog
      open
      file={project.profilesFile}
      review={project.review}
      diff={project.state === 'changed' ? project.diff : null}
      onReject={() => setDismissed(true)}
      onTrust={() => {
        void trustProject(fetcher, digest).then(() => {
          // Success or a refusal (for example a file that changed meanwhile): read the state again either way.
          load();
        });
      }}
    />
  );
}
