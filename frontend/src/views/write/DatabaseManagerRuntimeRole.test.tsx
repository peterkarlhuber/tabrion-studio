/**
 * STU-CR-6 part 2: the runtime-role wizard is shown only while both the Schema Designer and the Database Manager flag
 * are on; it looks first, shows refusals and warnings, sends the password of a new role once with the run, and needs
 * the role's name typed. axe finds no serious or critical violation (jsdom: no colour contrast).
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';

import { parseRuntimeRolePreview, parseRuntimeRoleResult } from '../../api/databaseManager';
import { RuntimeRolePart } from './DatabaseManagerRuntimeRole';

const PREVIEW = {
  action: 'runtime',
  request: { role: 'app_rt', schema: 'app', protected_tables: ['alembic_version'], create: true },
  environment: 'dev',
  database: 'parts',
  target: null,
  k2: null,
  schema: {
    owner_reached: true,
    foreign_owned: 0,
    security_definers: 0,
    tables: 2,
    protected_found: ['alembic_version'],
    protected_missing: [],
  },
  refusals: [],
  warnings: ['rerun_after_migration'],
  typed: 'app_rt',
  state: 'a'.repeat(64),
};

const DONE = {
  action: 'runtime',
  role: 'app_rt',
  schema: 'app',
  created: true,
  connect: true,
  can_create: false,
  tables: 2,
  after: null,
  audit_pending: false,
};

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}

function setup(
  areas: Record<string, boolean>,
  preview: unknown = PREVIEW,
  apply: { status: number; body: unknown } = { status: 200, body: DONE },
) {
  const calls: Call[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url === '/api/write/areas') return new Response(JSON.stringify({ areas }));
    if (url.endsWith('/runtime-role/preview')) return new Response(JSON.stringify(preview));
    if (url.endsWith('/runtime-role/apply'))
      return new Response(JSON.stringify(apply.body), {
        status: apply.status,
        headers: { 'Content-Type': 'application/problem+json' },
      });
    return new Response(null, { status: 404 });
  };
  const view = render(<RuntimeRolePart fetcher={fetcher} connectionId="c1" onRefused={() => {}} />);
  return { calls, view };
}

const BOTH = { data_editor: false, schema_designer: true, database_manager: true };

async function serious(container: HTMLElement): Promise<string[]> {
  const result = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => v.id);
}

describe('the runtime-role wizard (STU-CR-6 part 2)', () => {
  it.each([
    [
      'database manager only',
      { data_editor: false, schema_designer: false, database_manager: true },
    ],
    [
      'schema designer only',
      { data_editor: false, schema_designer: true, database_manager: false },
    ],
  ])('is not shown with the %s flag', async (_name, areas) => {
    const { calls } = setup(areas);
    await waitFor(() => expect(calls.some((call) => call.url === '/api/write/areas')).toBe(true));
    expect(screen.queryByTestId('database-manager-runtime-role')).toBeNull();
  });

  it('looks first, then creates the role with the typed name and sends the password once', async () => {
    const { calls, view } = setup(BOTH);
    await userEvent.type(await screen.findByTestId('database-manager-runtime-role-name'), 'app_rt');
    await userEvent.clear(screen.getByTestId('database-manager-runtime-schema'));
    await userEvent.type(screen.getByTestId('database-manager-runtime-schema'), 'app');
    await userEvent.click(screen.getByTestId('database-manager-runtime-preview'));
    const confirm = await screen.findByTestId('database-manager-runtime-confirm');
    expect(confirm.textContent).toMatch(/2 tables/);
    expect(screen.getByTestId('database-manager-runtime-warnings').textContent).toMatch(
      /again after a migration/,
    );
    expect(await serious(view.container)).toEqual([]);
    const previewCall = calls.find((call) => call.url.endsWith('/runtime-role/preview'));
    expect(previewCall?.body).toEqual({
      connection_id: 'c1',
      role: 'app_rt',
      schema: 'app',
      protected_tables: ['alembic_version'],
      create: true,
    });
    const apply = screen.getByTestId('database-manager-runtime-apply');
    expect(apply).toBeDisabled();
    await userEvent.type(screen.getByTestId('database-manager-runtime-password'), 'pw-canary');
    await userEvent.type(screen.getByTestId('database-manager-runtime-typed'), 'app_rt');
    await userEvent.click(apply);
    expect((await screen.findByTestId('database-manager-runtime-done')).textContent).toMatch(
      /app_rt was created and granted the rows of app \(2 tables\)/,
    );
    const applyCall = calls.find((call) => call.url.endsWith('/runtime-role/apply'));
    expect(applyCall?.body).toMatchObject({
      typed: 'app_rt',
      state: 'a'.repeat(64),
      password: 'pw-canary',
    });
    expect(JSON.stringify(calls.filter((call) => call !== applyCall))).not.toContain('pw-canary');
  });

  it('shows the refusals and offers no apply', async () => {
    setup(BOTH, { ...PREVIEW, refusals: ['own_role', 'not_owner'] });
    await userEvent.type(await screen.findByTestId('database-manager-runtime-role-name'), 'app_rt');
    await userEvent.click(screen.getByTestId('database-manager-runtime-preview'));
    const refusals = await screen.findByTestId('database-manager-runtime-refusals');
    expect(refusals.textContent).toMatch(/own roles/);
    expect(refusals.textContent).toMatch(/owner of the schema/);
    expect(screen.queryByTestId('database-manager-runtime-apply')).toBeNull();
  });

  it('asks no password to grant an existing role again', async () => {
    const { calls } = setup(BOTH, {
      ...PREVIEW,
      request: { ...PREVIEW.request, create: false },
    });
    await userEvent.click(await screen.findByTestId('database-manager-runtime-existing'));
    await userEvent.type(screen.getByTestId('database-manager-runtime-role-name'), 'app_rt');
    await userEvent.click(screen.getByTestId('database-manager-runtime-preview'));
    await screen.findByTestId('database-manager-runtime-confirm');
    expect(screen.queryByTestId('database-manager-runtime-password')).toBeNull();
    expect(calls.find((call) => call.url.endsWith('/runtime-role/preview'))?.body).toMatchObject({
      create: false,
    });
  });

  it('parses the answers defensively', () => {
    expect(parseRuntimeRolePreview(null)).toBeNull();
    expect(parseRuntimeRolePreview({ ...PREVIEW, action: 'create' })).toBeNull();
    expect(
      parseRuntimeRolePreview({ ...PREVIEW, schema: 'x', refusals: 'own_role' }),
    ).toMatchObject({
      schema: null,
      refusals: [],
    });
    expect(parseRuntimeRoleResult({ ...DONE, tables: -1, connect: 'yes' })).toMatchObject({
      tables: 0,
      connect: false,
    });
    expect(parseRuntimeRoleResult({ action: 'runtime' })).toBeNull();
  });
});
