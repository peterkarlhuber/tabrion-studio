/**
 * STU-CR-6 part 1: the profile export is hidden while the Schema Designer flag is off; it lists the read profiles
 * with what the file says about each password, sends only their ids, the path and the typed name (never a secret, a
 * profile or a mode), keeps the Export button off until the typed name matches, never focuses it on its own, shows
 * the server's field issues and problems, and the written file. axe finds no serious or critical violation.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';

import { ProfileExportSection } from './ProfileExport';

const PROFILES = [
  {
    id: 'user:parts',
    source: 'user',
    name: 'parts',
    engine: 'sqlite',
    environment: 'dev',
    path: '/home/u/parts.db',
    password: null,
    secret: { source: null, has_secret: null },
  },
  {
    id: 'user:orders',
    source: 'user',
    name: 'orders',
    engine: 'postgresql',
    environment: 'test',
    host: 'db.example.org',
    database: 'orders',
    password: { source: 'env', target: 'ORDERS_PASSWORD' },
    secret: { source: 'env', has_secret: false },
  },
  {
    id: 'project:ledger',
    source: 'project',
    name: 'ledger',
    engine: 'postgresql',
    environment: 'prod',
    host: 'localhost',
    database: 'ledger',
    password: { source: 'keyring', target: '' },
    secret: { source: 'keyring', has_secret: null },
  },
];

const PATH = '/home/u/exports/app.tabrion-profile.toml';

interface Call {
  url: string;
  body: Record<string, unknown> | null;
}

function setup(
  answers: { areas?: Record<string, boolean>; exported?: { status: number; body: unknown } } = {},
) {
  const calls: Call[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
    calls.push({ url, body });
    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: {
          'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json',
        },
      });
    if (url === '/api/write/areas')
      return json({
        areas: answers.areas ?? {
          data_editor: false,
          schema_designer: true,
          database_manager: false,
        },
      });
    if (url === '/api/profiles')
      return json({
        profiles: PROFILES,
        project_state: 'trusted',
        user_file: { digest: 'd'.repeat(64), hand_written: false },
        issues: {},
      });
    if (url === '/api/write/schema-designer/profile-export') {
      const answer = answers.exported ?? {
        status: 201,
        body: {
          path: PATH,
          file_name: 'app.tabrion-profile.toml',
          profiles: 2,
          mode: '0600',
          audit_pending: false,
        },
      };
      return json(answer.body, answer.status);
    }
    return new Response(null, { status: 404 });
  };
  const view = render(<ProfileExportSection fetcher={fetcher} />);
  return { calls, view };
}

async function serious(container: HTMLElement): Promise<string[]> {
  const result = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => v.id);
}

async function fill(user: ReturnType<typeof userEvent.setup>, typed = 'app.tabrion-profile.toml') {
  await user.click(await screen.findByTestId('profile-export-pick-user:orders'));
  await user.click(screen.getByTestId('profile-export-pick-user:parts'));
  await user.type(screen.getByTestId('profile-export-path'), PATH);
  await user.type(screen.getByTestId('profile-export-typed'), typed);
}

describe('ProfileExportSection (STU-CR-6 part 1)', () => {
  it.each([
    { data_editor: false, schema_designer: false, database_manager: false },
    { data_editor: true, schema_designer: false, database_manager: true },
  ])('renders nothing while the Schema Designer flag is off (%o)', async (areas) => {
    const { calls } = setup({ areas });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain('/api/write/areas'));
    expect(screen.queryByTestId('profile-export')).toBeNull();
    expect(calls.some((c) => c.url.includes('profile-export'))).toBe(false);
  });

  it('lists the read profiles with what the file says about each password', async () => {
    const { view } = setup();
    const form = await screen.findByTestId('profile-export-form');
    const group = within(form).getByRole('group', { name: 'Read profiles' });
    expect(
      within(group)
        .getAllByRole('checkbox')
        .map((box) => box.closest('label')?.textContent),
    ).toEqual([
      'parts (your profile, DEV; no password)',
      'orders (your profile, TEST; password from its environment variable)',
      'ledger (project profile, PROD; password asked for when used)',
    ]);
    expect(await serious(view.container)).toEqual([]);
  });

  it('sends only the ids, the path and the typed name, and shows the written file', async () => {
    const user = userEvent.setup();
    const { calls, view } = setup();
    await fill(user);
    const submit = screen.getByTestId('profile-export-submit');
    expect(submit).toBeEnabled();
    expect(submit).not.toHaveFocus();
    await user.click(submit);
    const done = await screen.findByTestId('profile-export-done');
    expect(done.textContent).toBe(
      `Exported 2 profiles to ${PATH}, readable and writable only by you.`,
    );
    const sent = calls.filter((c) => c.url === '/api/write/schema-designer/profile-export');
    // In the order the page lists them, whatever order they were ticked in.
    expect(sent.map((c) => c.body)).toEqual([
      { profiles: ['user:parts', 'user:orders'], path: PATH, typed: 'app.tabrion-profile.toml' },
    ]);
    expect(await serious(view.container)).toEqual([]);
  });

  it('keeps Export off until a profile is chosen and the typed name matches', async () => {
    const user = userEvent.setup();
    setup();
    await screen.findByTestId('profile-export-form');
    const submit = screen.getByTestId('profile-export-submit');
    expect(submit).toBeDisabled();
    expect(screen.queryByTestId('profile-export-typed')).toBeNull();
    await fill(user, 'app.tabrion-profile.tom');
    expect(submit).toBeDisabled();
    await user.type(screen.getByTestId('profile-export-typed'), 'l');
    expect(submit).toBeEnabled();
    await user.click(screen.getByTestId('profile-export-pick-user:parts'));
    await user.click(screen.getByTestId('profile-export-pick-user:orders'));
    expect(submit).toBeDisabled();
  });

  it('shows a field issue at its field', async () => {
    const user = userEvent.setup();
    const { view } = setup({
      exported: {
        status: 400,
        body: {
          code: 'studio.profile_export_invalid',
          status: 400,
          issues: [{ pointer: 'export.path', code: 'export.suffix' }],
        },
      },
    });
    await fill(user);
    await user.click(screen.getByTestId('profile-export-submit'));
    const path = screen.getByTestId('profile-export-path');
    await waitFor(() => expect(path).toHaveAttribute('aria-invalid', 'true'));
    expect(screen.getByText('The file name must end in .tabrion-profile.toml.')).toBeVisible();
    expect(screen.getByTestId('profile-export-problem').textContent).toContain(
      'Nothing was written.',
    );
    expect(await serious(view.container)).toEqual([]);
  });

  it('shows a profile list issue at the list', async () => {
    const user = userEvent.setup();
    setup({
      exported: {
        status: 400,
        body: {
          code: 'studio.profile_export_multi_host',
          status: 400,
          issues: [{ pointer: 'export.profiles', code: 'export.host_list' }],
        },
      },
    });
    await fill(user);
    await user.click(screen.getByTestId('profile-export-submit'));
    const group = await screen.findByRole('group', {
      description: 'A chosen profile names several hosts.',
    });
    expect(group).toBeVisible();
    expect(screen.getByTestId('profile-export-problem').textContent).toContain('several hosts');
  });

  it.each([
    ['studio.profile_export_exists', 'never overwrites a file'],
    ['studio.profile_export_folder', 'a folder of your own'],
    ['studio.profile_export_reserved', 'its own configuration'],
    ['studio.profile_export_platform', 'On Windows'],
  ])('shows the problem %s', async (code, text) => {
    const user = userEvent.setup();
    setup({ exported: { status: 400, body: { code, status: 400 } } });
    await fill(user);
    await user.click(screen.getByTestId('profile-export-submit'));
    expect((await screen.findByTestId('profile-export-problem')).textContent).toContain(text);
    expect(screen.queryByTestId('profile-export-done')).toBeNull();
  });
});
