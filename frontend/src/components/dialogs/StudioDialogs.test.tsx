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
import type { TrustDiff } from '../../api/project';
import { DIFF, HOSTILE_NAMES, HOSTILE_REVIEW, OVERLONG, REVIEW } from '../../harness/trust';
import { MAX_ENTRIES, MAX_ENTRY_LENGTH, capped, visible } from '../../lib/visible';

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
    description: /\.tabrion\/studio\.toml defines 3 connections\./,
    safe: "Don't load",
    destructive: null,
    buttons: ['Close', "Don't load", 'Trust'],
    render: (open, done) => (
      <TrustProjectDialog
        open={open}
        file=".tabrion/studio.toml"
        review={REVIEW}
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

describe('D-01 with an untrusted project file (binding requirement U-1)', () => {
  function renderHostile(diff: TrustDiff | null = null) {
    render(
      <TrustProjectDialog
        open
        file=".tabrion/studio.toml"
        review={HOSTILE_REVIEW}
        diff={diff}
        onTrust={() => undefined}
        onReject={() => undefined}
      />,
    );
    return screen.getByRole('dialog');
  }

  function codes(section: HTMLElement): string[] {
    return within(section)
      .getAllByRole('listitem')
      .map((item) => item.querySelector('code')?.textContent ?? '');
  }

  it('U-1a: one list item with its own <code> per entry, nothing joined', () => {
    const dialog = renderHostile();
    const connections = within(dialog).getByTestId('trust-connections');
    const items = within(connections).getAllByRole('listitem');
    expect(items).toHaveLength(MAX_ENTRIES);
    for (const item of items) expect(item.querySelectorAll('code')).toHaveLength(1);
    // The comma in a name stays inside its one entry; it does not make two.
    expect(codes(connections)[0]).toBe('parts-dev, safe (checked)');
    const hosts = within(dialog).getByTestId('trust-hosts');
    expect(codes(hosts)).toEqual(['db.internal:5432, localhost:5432', 'attacker\\u202Elacol:5432']);
    expect(codes(within(dialog).getByTestId('trust-files'))).toEqual([
      'data/parts.db\\u0009safe',
      '../../.ssh/id_rsa',
    ]);
    expect(
      within(within(dialog).getByTestId('trust-files')).getByText(/outside the project folder/),
    ).toBeVisible();
    expect(codes(within(dialog).getByTestId('trust-env'))).toEqual(['AWS_SECRET_ACCESS_KEY']);
  });

  it('U-1b: control and bidi characters are visible escapes, never interpreted', () => {
    const dialog = renderHostile();
    const names = codes(within(dialog).getByTestId('trust-connections'));
    expect(names[1]).toBe('evil\\u202Etxt.exe');
    expect(names[2]).toBe('line\\u000Abreak');
    for (const raw of [String.fromCodePoint(0x202e), '\n', '\t'])
      expect(dialog.textContent).not.toContain(raw);
    expect(visible('a\\u202Eb')).toBe('a\\\\u202Eb'); // a literal escape cannot pass for a real one
    for (const code of [
      0x00, 0x1b, 0x7f, 0x85, 0x9f, 0x61c, 0x200e, 0x200f, 0x2028, 0x2029, 0x202a, 0x2066, 0x2069,
    ]) {
      expect(visible(String.fromCodePoint(code))).toBe(
        `\\u${code.toString(16).toUpperCase().padStart(4, '0')}`,
      );
    }
    expect(visible('plain ä 日本 😀')).toBe('plain ä 日本 😀');
  });

  it('U-1c: long values are cut with a marker and shown in full on request; lists say "and N more"', async () => {
    const user = userEvent.setup();
    const dialog = renderHostile();
    const connections = within(dialog).getByTestId('trust-connections');
    // The counts are the real totals.
    expect(within(connections).getByRole('heading')).toHaveTextContent(
      `Connections (${HOSTILE_NAMES.length})`,
    );
    const long = within(connections).getAllByRole('listitem')[3] as HTMLElement;
    const shown = long.querySelector('code')?.textContent ?? '';
    expect(shown).toHaveLength(MAX_ENTRY_LENGTH);
    expect(long).toHaveTextContent('…');
    await user.click(within(long).getByRole('button', { name: 'Show full value' }));
    expect(long.querySelector('code')?.textContent).toBe(OVERLONG);
    const more = within(connections).getByRole('button', {
      name: `and ${HOSTILE_NAMES.length - MAX_ENTRIES} more`,
    });
    await user.click(more);
    expect(within(connections).getAllByRole('listitem')).toHaveLength(HOSTILE_NAMES.length);
    expect(capped('x'.repeat(10), 4)).toEqual(['xxxx', true]);
    expect(capped(`ab${String.fromCodePoint(0x202e)}`, 4)).toEqual(['ab', true]); // never half an escape
  });

  it('U-1d: a changed file shows the diff to the trusted version', () => {
    const dialog = renderHostile(DIFF);
    expect(dialog).toHaveAccessibleName('The project profiles have changed. Trust them?');
    const diff = within(dialog).getByTestId('trust-diff');
    expect(codes(within(diff).getByTestId('trust-diff-connections-added'))).toEqual(['parts-dev2']);
    expect(codes(within(diff).getByTestId('trust-diff-connections-removed'))).toEqual([
      'parts-dev',
    ]);
    expect(codes(within(diff).getByTestId('trust-diff-connections-changed'))).toEqual([
      'measure-central',
    ]);
    expect(codes(within(diff).getByTestId('trust-diff-hosts-added'))).toEqual([
      'attacker.example:5432',
    ]);
    expect(codes(within(diff).getByTestId('trust-diff-hosts-removed'))).toEqual([
      'db.internal:5432',
    ]);
    expect(codes(within(diff).getByTestId('trust-diff-env-added'))).toEqual([
      'AWS_SECRET_ACCESS_KEY',
    ]);
    expect(within(diff).queryByTestId('trust-diff-files-added')).toBeNull(); // only groups that changed
  });

  it('U-1d: a change outside the listed items is still announced', () => {
    const empty: TrustDiff = {
      connections: { added: [], removed: [], changed: [] },
      hosts: { added: [], removed: [] },
      files: { added: [], removed: [] },
      env: { added: [], removed: [] },
      unknown_keys: { added: [], removed: [] },
    };
    const dialog = renderHostile(empty);
    expect(within(dialog).getByTestId('trust-diff')).toHaveTextContent(
      /other content of the file did/,
    );
  });
});

it('I-2: unknown keys added and removed are both shown, and a stale review says so', () => {
  render(
    <TrustProjectDialog
      open
      file=".tabrion/studio.toml"
      review={REVIEW}
      diff={{ ...DIFF, unknown_keys: { added: ['profile[0].new'], removed: ['profile[0].old'] } }}
      stale
      onTrust={() => undefined}
      onReject={() => undefined}
    />,
  );
  const dialog = screen.getByRole('dialog');
  const codes = (id: string) =>
    within(within(dialog).getByTestId(id))
      .getAllByRole('listitem')
      .map((item) => item.querySelector('code')?.textContent);
  expect(codes('trust-diff-keys-added')).toEqual(['profile[0].new']);
  expect(codes('trust-diff-keys-removed')).toEqual(['profile[0].old']);
  expect(within(dialog).getByTestId('trust-diff-keys-removed')).toHaveTextContent(
    'Unknown keys removed (1)',
  );
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'The file changed after it was shown.',
  );
});

it('I-2: no stale notice by default', () => {
  render(
    <TrustProjectDialog
      open
      file=".tabrion/studio.toml"
      review={REVIEW}
      onTrust={() => undefined}
      onReject={() => undefined}
    />,
  );
  expect(screen.queryByTestId('trust-stale')).toBeNull();
});

it('D-01 without hosts says none', () => {
  render(
    <TrustProjectDialog
      open
      file=".tabrion/studio.toml"
      review={{ ...REVIEW, hosts: [], env: [] }}
      onTrust={() => undefined}
      onReject={() => undefined}
    />,
  );
  const element = screen.getByRole('dialog', { name: 'Trust the project profiles?' });
  expect(element).toHaveAccessibleDescription(/defines 3 connections/);
  expect(within(within(element).getByTestId('trust-hosts')).getByText('none')).toBeInTheDocument();
  expect(within(element).getByText('./secrets/db')).toBeInTheDocument();
});
