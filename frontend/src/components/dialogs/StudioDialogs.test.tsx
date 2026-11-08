/**
 * STU-051: the focus rules of every dialog (08 section 6). For each of D-01, D-02, D-06 and D-08:
 * the dialog is modal and named by its title and text; the initial focus is the option that changes nothing (never a
 * destructive one); Tab and Shift+Tab stay inside; Escape and the close button cancel; the focus returns to the
 * opener. The same rules are checked in Chromium in e2e/dialogs.spec.ts.
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState, type ReactElement } from 'react';

import {
  QuitDialog,
  ProdConnectDialog,
  TrustProjectDialog,
  UnsavedScriptDialog,
} from './StudioDialogs';

interface Case {
  name: string;
  title: string;
  description: RegExp;
  safe: string;
  destructive: string | null;
  buttons: string[];
  render: (open: boolean, done: (choice: string) => void) => ReactElement;
}

const CASES: Case[] = [
  {
    name: 'D-01 trust project profiles',
    title: 'Trust the project profiles?',
    description:
      /\.tabrion\/studio\.toml defines 3 connections: parts-dev, parts-pg, measure-central/,
    safe: "Don't load",
    destructive: null,
    buttons: ['Close', "Don't load", 'Trust'],
    render: (open, done) => (
      <TrustProjectDialog
        open={open}
        file=".tabrion/studio.toml"
        connections={['parts-dev', 'parts-pg', 'measure-central']}
        hosts={['db.internal']}
        files={['./secrets/db']}
        onTrust={() => done('trust')}
        onReject={() => done('cancel')}
      />
    ),
  },
  {
    name: 'D-02 connect to PROD',
    title: 'Production database',
    description:
      /measure-central is marked as PROD\. Studio stays read-only; queries are limited to 15 s\./,
    safe: 'Cancel',
    destructive: null,
    buttons: ['Close', 'Cancel', 'Connect'],
    render: (open, done) => (
      <ProdConnectDialog
        open={open}
        name="measure-central"
        timeoutSeconds={15}
        onConnect={() => done('connect')}
        onCancel={() => done('cancel')}
      />
    ),
  },
  {
    name: 'D-06 unsaved script',
    title: 'Script not saved',
    description: /Query 2 has changes that are not saved\./,
    safe: 'Cancel',
    destructive: 'Discard',
    buttons: ['Close', 'Cancel', 'Discard', 'Save'],
    render: (open, done) => (
      <UnsavedScriptDialog
        open={open}
        name="Query 2"
        onSave={() => done('save')}
        onDiscard={() => done('discard')}
        onCancel={() => done('cancel')}
      />
    ),
  },
  {
    name: 'D-08 quit with running queries',
    title: '2 queries are running',
    description: /Quitting Studio cancels them\./,
    safe: 'Cancel',
    destructive: 'Quit',
    buttons: ['Close', 'Cancel', 'Quit'],
    render: (open, done) => (
      <QuitDialog
        open={open}
        running={2}
        onQuit={() => done('quit')}
        onCancel={() => done('cancel')}
      />
    ),
  },
];

function Opener({ dialog, onDone }: { dialog: Case; onDone: (choice: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <button type="button">Elsewhere</button>
      {dialog.render(open, (choice) => {
        setOpen(false);
        onDone(choice);
      })}
    </>
  );
}

async function openDialog(dialog: Case) {
  const user = userEvent.setup();
  const onDone = vi.fn();
  render(<Opener dialog={dialog} onDone={onDone} />);
  const opener = screen.getByRole('button', { name: 'Open' });
  await user.click(opener);
  const element = screen.getByRole('dialog', { name: dialog.title });
  return { user, onDone, opener, element };
}

describe.each(CASES)('$name', (dialog) => {
  it('is a modal dialog named by its title and text', async () => {
    const { element } = await openDialog(dialog);
    expect(element.tagName).toBe('DIALOG');
    expect(element).toHaveAttribute('aria-modal', 'true');
    expect(element).toHaveAccessibleName(dialog.title);
    expect(element).toHaveAccessibleDescription(dialog.description);
    expect(
      within(element)
        .getAllByRole('button')
        .map((b) => b.textContent || b.getAttribute('aria-label')),
    ).toEqual(dialog.buttons);
  });

  it('opens with the focus on the option that changes nothing', async () => {
    const { element } = await openDialog(dialog);
    const safe = within(element).getByRole('button', { name: dialog.safe });
    expect(safe).toHaveFocus();
    if (dialog.destructive !== null) {
      expect(within(element).getByRole('button', { name: dialog.destructive })).not.toHaveFocus();
    }
  });

  it('keeps Tab and Shift+Tab inside the dialog', async () => {
    const { user, element } = await openDialog(dialog);
    const buttons = within(element).getAllByRole('button');
    const first = buttons[0] as HTMLElement;
    const last = buttons[buttons.length - 1] as HTMLElement;
    for (let i = 0; i < buttons.length * 2; i += 1) {
      await user.tab();
      expect(element).toContainElement(document.activeElement as HTMLElement);
    }
    last.focus();
    await user.tab();
    expect(first).toHaveFocus();
    await user.tab({ shift: true });
    expect(last).toHaveFocus();
  });

  it('cancels on Escape and returns the focus to the opener', async () => {
    const { user, onDone, opener } = await openDialog(dialog);
    await user.keyboard('{Escape}');
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith('cancel');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(opener).toHaveFocus();
  });

  it('cancels with the close button', async () => {
    const { user, onDone, element, opener } = await openDialog(dialog);
    await user.click(within(element).getByRole('button', { name: 'Close' }));
    expect(onDone).toHaveBeenCalledWith('cancel');
    expect(opener).toHaveFocus();
  });

  it('runs the primary action only when it is chosen', async () => {
    const { user, onDone, element, opener } = await openDialog(dialog);
    const primary = dialog.buttons[dialog.buttons.length - 1] as string;
    await user.click(within(element).getByRole('button', { name: primary }));
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).not.toHaveBeenCalledWith('cancel');
    expect(opener).toHaveFocus();
  });
});

it('D-01 shows the hosts and file references, and a changed file', () => {
  render(
    <TrustProjectDialog
      open
      changed
      file=".tabrion/studio.toml"
      connections={['parts-dev']}
      hosts={[]}
      files={['./secrets/db']}
      onTrust={() => undefined}
      onReject={() => undefined}
    />,
  );
  const element = screen.getByRole('dialog', {
    name: 'The project profiles have changed. Trust them?',
  });
  expect(element).toHaveAccessibleDescription(/defines 1 connection: parts-dev/);
  expect(within(element).getByText('none')).toBeInTheDocument();
  expect(within(element).getByText('./secrets/db')).toBeInTheDocument();
});
