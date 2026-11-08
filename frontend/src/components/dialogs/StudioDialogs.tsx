/**
 * The confirmation dialogs of STU-051 (08 section 6: D-01, D-02, D-06, D-08). Each one opens with the focus on the
 * option that changes nothing, Escape means that option too, and a destructive action is never the default. The
 * owners that open them arrive with their work packages: D-01 with project trust (STU-006), D-02 with connecting
 * (STU-008), D-06 with the SQL editor (STU-012) and D-08 with running queries (STU-011). D-03..D-05 belong to the Data
 * Editor (STU-026, locked behind G-S2) and D-07 to Studio 1.0; they are not built.
 */
import type { TFunction } from 'i18next';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { TrustDiff, TrustFile, TrustReview } from '../../api/project';
import { MAX_ENTRIES, capped, visible } from '../../lib/visible';
import { Dialog } from '../ui/Dialog';
import styles from './StudioDialogs.module.css';

/** One untrusted value: escaped (U-1b), cut with a visible marker and the full value on request (U-1c). */
function Entry({ value, note }: { value: string; note?: string }) {
  const { t } = useTranslation();
  const [full, setFull] = useState(false);
  const [shown, cut] = capped(value);
  return (
    <li className={styles.entry}>
      <code>{full || !cut ? visible(value) : shown}</code>
      {cut && !full ? <span aria-hidden="true">…</span> : null}
      {note ? <span className={styles.note}> {note}</span> : null}
      {cut ? (
        <button
          type="button"
          className={styles.more}
          aria-expanded={full}
          onClick={() => setFull((v) => !v)}
        >
          {full ? t('dialogs.trustProject.showLess') : t('dialogs.trustProject.showFull')}
        </button>
      ) : null}
    </li>
  );
}

/** A list of untrusted values: one item per entry (U-1a), at most MAX_ENTRIES before "and N more" (U-1c). */
function EntryList({
  label,
  items,
  testId,
}: {
  label: string;
  items: { value: string; note?: string }[];
  testId: string;
}) {
  const { t } = useTranslation();
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, MAX_ENTRIES);
  const hidden = items.length - shown.length;
  return (
    <section className={styles.section} data-testid={testId} aria-label={label}>
      <h3 className={styles.heading}>
        {t('dialogs.trustProject.heading', { label, count: items.length })}
      </h3>
      {items.length === 0 ? (
        <p className={styles.none}>{t('dialogs.trustProject.none')}</p>
      ) : (
        <ul className={styles.list}>
          {shown.map((item, index) => (
            <Entry key={index} value={item.value} note={item.note} />
          ))}
        </ul>
      )}
      {hidden > 0 || all ? (
        <button type="button" className={styles.more} onClick={() => setAll((v) => !v)}>
          {all
            ? t('dialogs.trustProject.showLess')
            : t('dialogs.trustProject.andMore', { count: hidden })}
        </button>
      ) : null}
    </section>
  );
}

function fileNote(t: TFunction, file: TrustFile): string {
  const kind = t(
    file.kind === 'secret'
      ? 'dialogs.trustProject.secretFile'
      : 'dialogs.trustProject.databaseFile',
  );
  return file.outside ? `(${kind}, ${t('dialogs.trustProject.outside')})` : `(${kind})`;
}

/** The difference to the trusted version of the file (T-08, U-1d): only the groups that changed. */
function DiffSection({ diff }: { diff: TrustDiff }) {
  const { t } = useTranslation();
  const groups: { id: string; label: string; items: { value: string; note?: string }[] }[] = [
    {
      id: 'connections-added',
      label: t('dialogs.trustProject.connectionsAdded'),
      items: diff.connections.added.map((value) => ({ value })),
    },
    {
      id: 'connections-removed',
      label: t('dialogs.trustProject.connectionsRemoved'),
      items: diff.connections.removed.map((value) => ({ value })),
    },
    {
      id: 'connections-changed',
      label: t('dialogs.trustProject.connectionsChanged'),
      items: diff.connections.changed.map((value) => ({ value })),
    },
    {
      // C-1 of stu-007-review.md: the saved password stays with the old host; Studio asks for it again.
      id: 'connections-retargeted',
      label: t('dialogs.trustProject.connectionsRetargeted'),
      items: diff.connections.retargeted.map((value) => ({ value })),
    },
    {
      id: 'hosts-added',
      label: t('dialogs.trustProject.hostsAdded'),
      items: diff.hosts.added.map((value) => ({ value })),
    },
    {
      id: 'hosts-removed',
      label: t('dialogs.trustProject.hostsRemoved'),
      items: diff.hosts.removed.map((value) => ({ value })),
    },
    {
      id: 'files-added',
      label: t('dialogs.trustProject.filesAdded'),
      items: diff.files.added.map((f) => ({ value: f.path, note: fileNote(t, f) })),
    },
    {
      id: 'files-removed',
      label: t('dialogs.trustProject.filesRemoved'),
      items: diff.files.removed.map((f) => ({ value: f.path, note: fileNote(t, f) })),
    },
    {
      id: 'env-added',
      label: t('dialogs.trustProject.envAdded'),
      items: diff.env.added.map((value) => ({ value })),
    },
    {
      id: 'env-removed',
      label: t('dialogs.trustProject.envRemoved'),
      items: diff.env.removed.map((value) => ({ value })),
    },
    {
      id: 'keys-added',
      label: t('dialogs.trustProject.keysAdded'),
      items: diff.unknown_keys.added.map((value) => ({ value })),
    },
    {
      id: 'keys-removed',
      label: t('dialogs.trustProject.keysRemoved'),
      items: diff.unknown_keys.removed.map((value) => ({ value })),
    },
  ];
  const changed = groups.filter((group) => group.items.length > 0);
  return (
    <section
      className={styles.diff}
      data-testid="trust-diff"
      aria-label={t('dialogs.trustProject.changes')}
    >
      <h3 className={styles.heading}>{t('dialogs.trustProject.changes')}</h3>
      {changed.length === 0 ? (
        <p className={styles.none}>{t('dialogs.trustProject.noVisibleChange')}</p>
      ) : (
        changed.map((group) => (
          <EntryList
            key={group.id}
            testId={`trust-diff-${group.id}`}
            label={group.label}
            items={group.items}
          />
        ))
      )}
    </section>
  );
}

/**
 * D-01: project profiles seen for the first time or changed. Default: "Don't load".
 *
 * Everything shown comes from an untrusted project file (AT4, T-08), so the dialog follows the binding requirement
 * U-1 of stu-005-051-review.md: every name, host, file and variable is its own list item with its own `<code>`
 * (U-1a), control and bidi characters are shown as escapes (U-1b), long values and long lists are cut with a marker
 * and the full value on request while the counts stay the real totals (U-1c), and a changed file shows the diff to
 * the trusted version (U-1d).
 */
export function TrustProjectDialog({
  open,
  file,
  review,
  diff = null,
  stale = false,
  onTrust,
  onReject,
}: {
  open: boolean;
  file: string;
  review: TrustReview;
  diff?: TrustDiff | null;
  /** The file changed after the dialog showed it (`studio.trust_stale`): this is the new content (review I-2). */
  stale?: boolean;
  onTrust: () => void;
  onReject: () => void;
}) {
  const { t } = useTranslation();
  const changed = diff !== null;
  return (
    <Dialog
      open={open}
      testId="dialog-trust-project"
      title={t(changed ? 'dialogs.trustProject.titleChanged' : 'dialogs.trustProject.title')}
      description={t('dialogs.trustProject.body', { file, count: review.connections.length })}
      initialFocus="reject"
      onCancel={onReject}
      actions={[
        { id: 'reject', label: t('dialogs.trustProject.reject'), onSelect: onReject },
        {
          id: 'trust',
          label: t('dialogs.trustProject.trust'),
          variant: 'primary',
          onSelect: onTrust,
        },
      ]}
    >
      {stale ? (
        <p role="alert" className={styles.stale} data-testid="trust-stale">
          {t('dialogs.trustProject.stale')}
        </p>
      ) : null}
      {diff !== null ? <DiffSection diff={diff} /> : null}
      <EntryList
        testId="trust-connections"
        label={t('dialogs.trustProject.connections')}
        items={review.connections.map((c) => ({
          value: c.name,
          note: `(${c.environment.toUpperCase()})`,
        }))}
      />
      <EntryList
        testId="trust-hosts"
        label={t('dialogs.trustProject.hosts')}
        items={review.hosts.map((value) => ({ value }))}
      />
      <EntryList
        testId="trust-files"
        label={t('dialogs.trustProject.files')}
        items={review.files.map((f) => ({ value: f.path, note: fileNote(t, f) }))}
      />
      <EntryList
        testId="trust-env"
        label={t('dialogs.trustProject.env')}
        items={review.env.map((value) => ({ value }))}
      />
      {review.unknown_keys.length > 0 ? (
        <EntryList
          testId="trust-unknown-keys"
          label={t('dialogs.trustProject.unknownKeys')}
          items={review.unknown_keys.map((value) => ({ value }))}
        />
      ) : null}
    </Dialog>
  );
}

/** D-02: opening a PROD connection for the first time in a session. Default: "Cancel". */
export function ProdConnectDialog({
  open,
  name,
  timeoutSeconds,
  onConnect,
  onCancel,
}: {
  open: boolean;
  name: string;
  timeoutSeconds: number;
  onConnect: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={open}
      testId="dialog-prod-connect"
      title={t('dialogs.prodConnect.title')}
      description={t('dialogs.prodConnect.body', { name, seconds: timeoutSeconds })}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[
        { id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel },
        {
          id: 'connect',
          label: t('dialogs.prodConnect.connect'),
          variant: 'primary',
          onSelect: onConnect,
        },
      ]}
    />
  );
}

/** D-06: closing a changed script. Default: "Cancel"; "Discard" is destructive. */
export function UnsavedScriptDialog({
  open,
  name,
  onSave,
  onDiscard,
  onCancel,
}: {
  open: boolean;
  name: string;
  onSave: () => void;
  onDiscard: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={open}
      testId="dialog-unsaved-script"
      title={t('dialogs.unsavedScript.title')}
      description={t('dialogs.unsavedScript.body', { name })}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[
        { id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel },
        {
          id: 'discard',
          label: t('dialogs.unsavedScript.discard'),
          variant: 'dangerOutline',
          onSelect: onDiscard,
        },
        {
          id: 'save',
          label: t('dialogs.unsavedScript.save'),
          variant: 'primary',
          onSelect: onSave,
        },
      ]}
    />
  );
}

/** D-08: quitting Studio while queries run. Default: "Cancel"; "Quit" cancels the queries. */
export function QuitDialog({
  open,
  running,
  onQuit,
  onCancel,
}: {
  open: boolean;
  running: number;
  onQuit: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={open}
      testId="dialog-quit"
      title={t('dialogs.quit.title', { count: running })}
      description={t('dialogs.quit.body')}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[
        { id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel },
        { id: 'quit', label: t('dialogs.quit.quit'), variant: 'danger', onSelect: onQuit },
      ]}
    />
  );
}
