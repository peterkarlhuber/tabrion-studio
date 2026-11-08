/**
 * Modal dialog with the focus rules of 08 section 6 (STU-051):
 *
 * - a native modal `<dialog>` (`showModal()`): the rest of the page is inert, the dialog is announced with its title
 *   (`aria-labelledby`) and text (`aria-describedby`);
 * - the initial focus is on the option the caller names, which is always the one that changes nothing; a destructive
 *   primary action never has the default focus;
 * - Tab and Shift+Tab stay inside the dialog (focus trap, also against the browser's own controls);
 * - Escape and the close button mean "Cancel" (`onCancel`); a click outside does nothing;
 * - on close, the focus returns to the element that had it before (the opener).
 *
 * Layout and styles are adapted from axentra-studio src/components/ui/Dialog.tsx at caa52b5 (MIT, © Peter
 * Karlhuber). Planning document 08 names Radix Dialog; it is not used because its scroll lock injects a <style>
 * element, which the strict Content-Security-Policy of STU-003 (`style-src 'self'`) refuses. The native element
 * gives the modality, and this component adds the focus rules on top (tested in Dialog.test.tsx and
 * e2e/dialogs.spec.ts).
 */
import { X } from 'lucide-react';
import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { useTranslation } from 'react-i18next';

import { Button, type ButtonVariant } from './Button';
import { IconButton } from './IconButton';
import styles from './Dialog.module.css';

export interface DialogAction {
  id: string;
  label: string;
  variant?: ButtonVariant;
  onSelect: () => void;
}

export interface DialogProps {
  open: boolean;
  title: ReactNode;
  /** Required: every dialog explains itself (`aria-describedby`). */
  description: ReactNode;
  children?: ReactNode;
  /** The footer buttons, in visual order (the safe option first). */
  actions: DialogAction[];
  /** The action that gets the focus when the dialog opens: the one that changes nothing. */
  initialFocus: string;
  /** Escape and the close button. */
  onCancel: () => void;
  testId?: string;
}

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Dialog(props: DialogProps) {
  return props.open ? <OpenDialog {...props} /> : null;
}

function OpenDialog({
  title,
  description,
  children,
  actions,
  initialFocus,
  onCancel,
  testId,
}: DialogProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const cancel = useRef(onCancel);
  // Escape is handled on keydown; a browser that still fires its own `cancel` event for it must not cancel twice.
  const escapeHandled = useRef(false);
  useEffect(() => {
    cancel.current = onCancel;
  }, [onCancel]);

  useLayoutEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return undefined;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (typeof dialog.showModal === 'function') {
      dialog.showModal();
    } else {
      dialog.setAttribute('open', '');
    }
    dialog.querySelector<HTMLElement>(`[data-action="${initialFocus}"]`)?.focus();
    const onNativeCancel = (event: Event) => {
      // The browser's own Escape handling: keep the dialog open until the owner closes it.
      event.preventDefault();
      if (!escapeHandled.current) cancel.current();
    };
    dialog.addEventListener('cancel', onNativeCancel);
    return () => {
      dialog.removeEventListener('cancel', onNativeCancel);
      if (typeof dialog.close === 'function' && dialog.open) dialog.close();
      if (opener?.isConnected) opener.focus();
    };
    // The initial focus is taken once, when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDialogElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      escapeHandled.current = true;
      window.setTimeout(() => {
        escapeHandled.current = false;
      }, 0);
      onCancel();
      return;
    }
    if (event.key !== 'Tab' || ref.current === null) return;
    const focusable = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (first === undefined || last === undefined) return;
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !ref.current.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !ref.current.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    // The keyboard handler implements the dialog's own keys: Escape and the focus trap.
    <dialog
      ref={ref}
      className={styles.dialog}
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onKeyDown={onKeyDown}
      data-testid={testId}
    >
      <div className={styles.header}>
        <h2 id={titleId} className={styles.title}>
          {title}
        </h2>
        <IconButton
          label={t('dialog.close')}
          icon={<X />}
          onClick={onCancel}
          className={styles.close}
        />
      </div>
      <div className={styles.body}>
        <div id={descriptionId} className={styles.description}>
          {description}
        </div>
        {children}
      </div>
      <div className={styles.footer}>
        {actions.map((action) => (
          <Button
            key={action.id}
            data-action={action.id}
            variant={action.variant ?? 'secondary'}
            onClick={action.onSelect}
          >
            {action.label}
          </Button>
        ))}
      </div>
    </dialog>
  );
}
