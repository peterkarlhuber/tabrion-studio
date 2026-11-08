/** A labelled form field with its hint and its issues, wired with `aria-describedby` and `aria-invalid`. */
import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type { ProblemIssue } from '../../api/problem';
import { issueText } from './messages';
import styles from './Connections.module.css';

export function Field({
  label,
  hint,
  issues = [],
  children,
}: {
  label: string;
  hint?: ReactNode;
  issues?: ProblemIssue[];
  children: (props: {
    id: string;
    'aria-describedby'?: string;
    'aria-invalid'?: boolean;
  }) => ReactNode;
}) {
  const { t } = useTranslation();
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const described = [hint ? hintId : null, issues.length > 0 ? errorId : null]
    .filter(Boolean)
    .join(' ');
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      {children({
        id,
        'aria-describedby': described || undefined,
        'aria-invalid': issues.length > 0 ? true : undefined,
      })}
      {hint ? (
        <p id={hintId} className={styles.hint}>
          {hint}
        </p>
      ) : null}
      {issues.length > 0 ? (
        <p id={errorId} className={styles.error}>
          {issues.map((issue) => issueText(t, issue)).join(' ')}
        </p>
      ) : null}
    </div>
  );
}
