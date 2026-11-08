// Adapted from axentra-studio src/components/ui/Button.tsx at caa52b5 (MIT, © Peter Karlhuber): --tb- tokens, no
// router link variant, no loading state yet.
import type { ComponentProps, ReactNode } from 'react';

import clsx from '../../lib/clsx';
import styles from './Button.module.css';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'dangerOutline';

export interface ButtonProps extends ComponentProps<'button'> {
  variant?: ButtonVariant;
  size?: 'sm' | 'md';
  icon?: ReactNode;
}

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={clsx(styles.button, styles[variant], styles[size], className)}
      {...rest}
    >
      {icon ? (
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
      ) : null}
      {children ? <span className={styles.label}>{children}</span> : null}
    </button>
  );
}
