// Adapted from axentra-studio src/components/ui/EmptyState.tsx at caa52b5 (MIT, © Peter Karlhuber): --tb- tokens,
// no hexagon motif and no route focus.
import type { ReactNode } from 'react';

import styles from './EmptyState.module.css';

export function EmptyState({
  title,
  description,
  icon,
  action,
}: {
  title: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={styles.empty}>
      {icon ? (
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
      ) : null}
      <h2 className={styles.title}>{title}</h2>
      {description ? <div className={styles.description}>{description}</div> : null}
      {action ? <div className={styles.action}>{action}</div> : null}
    </div>
  );
}
