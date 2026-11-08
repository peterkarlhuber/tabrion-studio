/**
 * Global status displays (08 section 3, STU-051): environment badge, access mode, transaction and connection.
 * Colour is never the only carrier: every display has text (WCAG 1.4.1).
 */
import { Ban, Lock, Plug } from 'lucide-react';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';

import clsx from '../../lib/clsx';
import { ENV_LABEL, type ActiveConnection, type Environment } from '../../workspace';
import styles from './Status.module.css';

/** DEV (neutral grey), TEST (amber), PROD (red, filled). The visible text is the environment itself. */
export function EnvBadge({
  environment,
  size = 'md',
}: {
  environment: Environment;
  size?: 'sm' | 'md';
}) {
  const { t } = useTranslation();
  return (
    <span
      className={clsx(styles.env, styles[environment], size === 'sm' && styles.small)}
      data-env={environment}
      data-testid="env-badge"
    >
      <span className="tb-sr-only">{t('header.env.label')}: </span>
      {ENV_LABEL[environment]}
    </span>
  );
}

/**
 * The access mode. Studio has no write mode before G-S2 (STU-026, Data Editor), so the chip is not a control: it
 * reads "Read-only", and "Writing locked" on PROD connections.
 */
export function AccessModeChip({ environment }: { environment: Environment }) {
  const { t } = useTranslation();
  const locked = environment === 'prod';
  const hint = t(locked ? 'header.access.lockedHint' : 'header.access.readOnlyHint');
  const Icon = locked ? Ban : Lock;
  return (
    <span
      className={clsx(styles.chip, locked && styles.locked)}
      title={hint}
      data-testid="access-mode"
    >
      <Icon aria-hidden="true" className={styles.chipIcon} />
      {t(locked ? 'header.access.locked' : 'header.access.readOnly')}
      <span className="tb-sr-only">. {hint}</span>
    </span>
  );
}

/** Transaction state. Read-only connections never hold an open transaction between requests. */
export function TxChip() {
  const { t } = useTranslation();
  return (
    <span className={styles.chip} data-testid="tx-state">
      {t('header.tx.none')}
    </span>
  );
}

/**
 * The active connection with its state (dot plus text); opens the connection manager. The accessible name is the
 * visible text (WCAG 2.5.3), the purpose is its description.
 */
export function ConnectionSwitcher({
  connection,
  onOpen,
}: {
  connection: ActiveConnection | null;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const hintId = useId();
  const hint = (
    <span id={hintId} className="tb-sr-only">
      {t('header.connection.manage')}
    </span>
  );
  if (connection === null) {
    return (
      <>
        <button
          type="button"
          className={styles.switcher}
          onClick={onOpen}
          aria-describedby={hintId}
        >
          <Plug aria-hidden="true" className={styles.chipIcon} />
          <span>{t('header.connection.none')}</span>
        </button>
        {hint}
      </>
    );
  }
  const status =
    connection.status === 'connected' && connection.latencyMs !== undefined
      ? t('header.connection.status.connectedLatency', { ms: connection.latencyMs })
      : t(`header.connection.status.${connection.status}`);
  return (
    <>
      <button
        type="button"
        className={styles.switcher}
        onClick={onOpen}
        aria-describedby={hintId}
        data-testid="connection-switcher"
      >
        <span className={clsx(styles.dot, styles[connection.status])} aria-hidden="true" />
        <span className="tb-sr-only">{t('header.connection.label')}: </span>
        <span className={styles.name}>{connection.name}</span>
        <span className="tb-sr-only">, </span>
        <span className={styles.status}>{status}</span>
      </button>
      {hint}
    </>
  );
}
