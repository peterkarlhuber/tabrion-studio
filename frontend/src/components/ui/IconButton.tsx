// Adapted from axentra-studio src/components/ui/IconButton.tsx at caa52b5 (MIT, © Peter Karlhuber): --tb- tokens;
// the tooltip is CSS only (Radix Tooltip is not used yet), the label is the accessible name.
import type { ComponentProps, ReactNode } from 'react';

import clsx from '../../lib/clsx';
import styles from './IconButton.module.css';

export interface IconButtonProps extends Omit<ComponentProps<'button'>, 'children'> {
  /** Accessible name, also shown as tooltip. Required: icon-only buttons need a label. */
  label: string;
  icon: ReactNode;
}

export function IconButton({ label, icon, className, type = 'button', ...rest }: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      data-tooltip={label}
      className={clsx(styles.iconButton, className)}
      {...rest}
    >
      <span aria-hidden="true">{icon}</span>
    </button>
  );
}
