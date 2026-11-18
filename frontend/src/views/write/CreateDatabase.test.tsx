/**
 * STU-CR-2, STU-CR-3 (G-S4: the admin area): the "New database" wizard is hidden while the Database Manager flag is
 * off (also with the Schema Designer or Data Editor flag on), confirms by the typed name, shows the server's refusals by field and code, unlocks the admin profile
 * (never a write profile) before a PostgreSQL database, shows the guided steps without CREATEDB, and saves a connection profile through the normal
 * profile route. axe finds no serious or critical violation (jsdom: no colour contrast; the E2E test has it).
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';

import { parseCreatedDatabase, parseCreatedFile, fileName } from '../../api/schemaDesigner';
import i18n from '../../i18n';
import { CreateDatabaseSection } from './CreateDatabase';

const PG = {
  id: 'user:shop-dev',
  source: 'user',
  name: 'shop-dev',
  engine: 'postgresql',
  environment: 'dev',
  host: 'localhost',
  port: 5432,
  database: 'postgres',
  user: 'reader',
  sslmode: 'prefer',
  password: { source: 'env', target: 'SHOP_PW' },
  secret: { source: 'env', has_secret: true },
};
const PG_PROD = { ...PG, id: 'user:shop-prod', name: 'shop-prod', environment: 'prod' };

interface Call {
  method: string;
  url: string;
  body: Record<string, unknown> | null;
}

interface Answers {
  areas?: Record<string, boolean>;
  sqlite?: { status: number; body: unknown };
  postgres?: { status: number; body: unknown };
  unlocked?: boolean;
  opened?: { connection_id: string; profile_id: string; environment: string }[];
}

const ON = { data_editor: false, schema_designer: false, database_manager: true };

function setup(answers: Answers = {}) {
  const calls: Call[] = [];
  let unlocked = answers.unlocked ?? false;
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
    calls.push({ method, url, body });
    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: {
          'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json',
        },
      });
    if (url === '/api/write/areas') return json({ areas: answers.areas ?? ON });
    if (url === '/api/profiles' && method === 'GET')
      return json({
        profiles: [PG, PG_PROD],
        project_state: 'none',
        user_file: { digest: 'd'.repeat(64), hand_written: false },
        issues: {},
      });
    if (url === '/api/profiles' && method === 'POST') return json({ digest: 'e'.repeat(64) }, 201);
    if (url === '/api/connections')
      return json({
        connections: answers.opened ?? [
          { connection_id: 'c_pg', profile_id: 'user:shop-dev', environment: 'dev' },
          { connection_id: 'c_prod', profile_id: 'user:shop-prod', environment: 'prod' },
        ],
      });
    if (url === '/api/write/unlocks' && method === 'GET')
      return json({
        unlocks: unlocked
          ? [{ connection_id: 'c_pg', write_profile_id: 'admin:shop-a', environment: 'dev' }]
          : [],
      });
    if (url === '/api/write/unlocks' && method === 'POST') {
      unlocked = true;
      return json({ connection_id: 'c_pg' });
    }
    if (url === '/api/write/profiles')
      return json({
        write_profiles: [
          {
            id: 'write:shop-w',
            name: 'shop-w',
            kind: 'write',
            engine: 'postgresql',
            read_profile_id: 'user:shop-dev',
            secret: { confirmed: true, foreign: false },
          },
          {
            id: 'admin:shop-a',
            name: 'shop-a',
            kind: 'admin',
            engine: 'postgresql',
            read_profile_id: 'user:shop-dev',
            secret: { confirmed: true, foreign: false },
          },
        ],
      });
    if (url === '/api/write/database-manager/create/sqlite') {
      const answer = answers.sqlite ?? {
        status: 201,
        body: {
          engine: 'sqlite',
          path: body?.path,
          file_name: 'inventory.db',
          mode: '0600',
          audit_pending: false,
        },
      };
      return json(answer.body, answer.status);
    }
    if (url === '/api/write/database-manager/create/postgresql') {
      const answer = answers.postgres ?? {
        status: 201,
        body: { engine: 'postgresql', database: body?.name, owner: null, audit_pending: false },
      };
      return json(answer.body, answer.status);
    }
    return new Response(null, { status: 404 });
  };
  const saved = vi.fn();
  const view = render(<CreateDatabaseSection fetcher={fetcher} onSaved={saved} />);
  return { calls, saved, view };
}

async function serious(container: HTMLElement): Promise<string[]> {
  const result = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => v.id);
}

describe('the answers of the creation routes', () => {
  it('parses a created file and database defensively', () => {
    expect(
      parseCreatedFile({ engine: 'sqlite', path: '/d/a.db', file_name: 'a.db', mode: '0600' }),
    ).toEqual({
      engine: 'sqlite',
      path: '/d/a.db',
      fileName: 'a.db',
      mode: '0600',
      auditPending: false,
    });
    expect(parseCreatedFile({ engine: 'postgresql', path: '/d/a.db' })).toBeNull();
    expect(parseCreatedDatabase({ engine: 'postgresql', database: 'shop' })?.database).toBe('shop');
    expect(parseCreatedDatabase('x')).toBeNull();
    expect(fileName('/data/a.db')).toBe('a.db');
    expect(fileName('C:\\data\\b.sqlite')).toBe('b.sqlite');
  });
});

describe('CreateDatabaseSection (STU-CR-2, STU-CR-3)', () => {
  it.each([
    { data_editor: false, schema_designer: false, database_manager: false },
    { data_editor: false, schema_designer: true, database_manager: false },
    { data_editor: true, schema_designer: true, database_manager: false },
  ])('renders nothing while the Database Manager flag is off (%o)', async (areas) => {
    const { calls } = setup({ areas });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain('/api/write/areas'));
    expect(screen.queryByTestId('create-database')).toBeNull();
    expect(calls.some((c) => c.url.includes('/create/'))).toBe(false);
  });

  it('creates a SQLite file only after its name is typed, then saves a profile', async () => {
    const user = userEvent.setup();
    const { calls, saved } = setup();
    await screen.findByTestId('create-database');
    const create = screen.getByRole('button', { name: 'Create file' });
    expect(create).toBeDisabled();
    await user.type(screen.getByLabelText('File path'), '/data/inventory.db');
    expect(create).toBeDisabled();
    await user.type(screen.getByLabelText('Type inventory.db to create the file'), 'inventory.d');
    expect(create).toBeDisabled();
    await user.type(screen.getByLabelText('Type inventory.db to create the file'), 'b');
    expect(create).toBeEnabled();
    expect(document.activeElement).not.toBe(create);
    await user.click(create);
    expect(await screen.findByTestId('create-database-done')).toHaveTextContent(
      'Created /data/inventory.db',
    );
    const post = calls.find((c) => c.url === '/api/write/database-manager/create/sqlite');
    expect(post?.body).toEqual({ path: '/data/inventory.db', typed: 'inventory.db' });
    expect(screen.getByLabelText('Profile name')).toHaveValue('inventory');
    await user.selectOptions(screen.getByLabelText('Environment'), 'test');
    await user.click(screen.getByRole('button', { name: 'Save profile' }));
    expect(await screen.findByTestId('create-database-saved')).toBeInTheDocument();
    const profile = calls.find((c) => c.url === '/api/profiles' && c.method === 'POST');
    expect(profile?.body).toMatchObject({
      profile: {
        name: 'inventory',
        engine: 'sqlite',
        path: '/data/inventory.db',
        environment: 'test',
      },
      digest: 'd'.repeat(64),
    });
    expect(saved).toHaveBeenCalledTimes(1);
  });

  it('shows a refused path at its field and a refused folder as the problem', async () => {
    const user = userEvent.setup();
    setup({
      sqlite: {
        status: 400,
        body: {
          code: 'studio.schema_create_invalid',
          issues: [{ pointer: 'create.path', code: 'create.suffix' }],
        },
      },
    });
    await screen.findByTestId('create-database');
    await user.type(screen.getByLabelText('File path'), '/data/notes.db');
    await user.type(screen.getByLabelText('Type notes.db to create the file'), 'notes.db');
    await user.click(screen.getByRole('button', { name: 'Create file' }));
    await waitFor(() =>
      expect(screen.getByLabelText('File path')).toHaveAttribute('aria-invalid', 'true'),
    );
    expect(
      screen.getByText('The file name must end in .db, .sqlite or .sqlite3.'),
    ).toBeInTheDocument();
  });

  it.each([
    ['studio.schema_folder_unsafe', 'Studio does not create the file in this folder'],
    ['studio.schema_file_exists', 'Studio never overwrites a file'],
    ['studio.schema_folder_reserved', "the project's .tabrion folder"],
    ['studio.schema_create_platform', 'On Windows Studio does not create SQLite files yet'],
  ])('shows %s with its own text, never the server text', async (code, text) => {
    const user = userEvent.setup();
    setup({ sqlite: { status: 400, body: { code, detail: 'SERVER-TEXT-CANARY' } } });
    await screen.findByTestId('create-database');
    await user.type(screen.getByLabelText('File path'), '/data/a.db');
    await user.type(screen.getByLabelText('Type a.db to create the file'), 'a.db');
    await user.click(screen.getByRole('button', { name: 'Create file' }));
    const problem = await screen.findByTestId('create-database-problem');
    expect(problem).toHaveTextContent(text);
    expect(document.body.textContent).not.toContain('SERVER-TEXT-CANARY');
  });

  it('offers only non-PROD PostgreSQL connections and unlocks the admin profile before creating', async () => {
    const user = userEvent.setup();
    const { calls } = setup();
    await screen.findByTestId('create-database');
    await user.click(screen.getByLabelText('PostgreSQL database'));
    expect(await screen.findByTestId('data-editor-unlock')).toBeInTheDocument();
    expect(screen.getByText('On the server of shop-dev.')).toBeInTheDocument();
    expect(screen.queryByText(/shop-prod/)).toBeNull();
    expect(screen.queryByText(/shop-w\b/)).toBeNull(); // the write profile is never offered
    await user.type(screen.getByLabelText('Type shop-a to unlock'), 'shop-a');
    await user.click(screen.getByRole('button', { name: 'Unlock' }));
    await screen.findByTestId('create-postgres');
    await user.type(screen.getByLabelText('Database name'), 'shop_test');
    await user.selectOptions(screen.getByLabelText('Template'), 'template0');
    const create = screen.getByRole('button', { name: 'Create database' });
    expect(create).toBeDisabled();
    await user.type(screen.getByLabelText('Type shop_test to create the database'), 'shop_test');
    await user.click(create);
    expect(await screen.findByTestId('create-database-done')).toHaveTextContent('shop_test');
    const post = calls.find((c) => c.url === '/api/write/database-manager/create/postgresql');
    expect(post?.body).toEqual({
      connection_id: 'c_pg',
      name: 'shop_test',
      template: 'template0',
      encoding: 'UTF8',
      typed: 'shop_test',
    });
    expect(post?.body).not.toHaveProperty('environment');
    // The new profile copies the server, role, TLS and secret reference, not the name or the database.
    await user.click(screen.getByRole('button', { name: 'Save profile' }));
    await screen.findByTestId('create-database-saved');
    const profile = calls.find((c) => c.url === '/api/profiles' && c.method === 'POST');
    expect(profile?.body).toMatchObject({
      profile: {
        name: 'shop-dev-shop_test',
        engine: 'postgresql',
        host: 'localhost',
        database: 'shop_test',
        user: 'reader',
        password: 'env:SHOP_PW',
        environment: 'dev',
      },
    });
  });

  it('says to connect first when no PostgreSQL connection of a DEV or TEST profile is open', async () => {
    const user = userEvent.setup();
    setup({
      opened: [{ connection_id: 'c_prod', profile_id: 'user:shop-prod', environment: 'prod' }],
    });
    await screen.findByTestId('create-database');
    await user.click(screen.getByLabelText('PostgreSQL database'));
    expect(await screen.findByTestId('create-postgres-none')).toBeInTheDocument();
  });

  it('shows the guided steps when the role may not create databases', async () => {
    const user = userEvent.setup();
    setup({
      unlocked: true,
      postgres: { status: 400, body: { code: 'studio.schema_createdb_required' } },
    });
    await screen.findByTestId('create-database');
    await user.click(screen.getByLabelText('PostgreSQL database'));
    await user.type(await screen.findByLabelText('Database name'), 'shop');
    await user.type(screen.getByLabelText('Type shop to create the database'), 'shop');
    await user.click(screen.getByRole('button', { name: 'Create database' }));
    expect(await screen.findByTestId('create-postgres-guided')).toHaveTextContent('Ask the server');
    expect(screen.getByTestId('create-database-problem')).toHaveTextContent('CREATEDB');
  });

  it('shows a refused field of the PostgreSQL form at that field', async () => {
    const user = userEvent.setup();
    setup({
      unlocked: true,
      postgres: {
        status: 400,
        body: {
          code: 'studio.schema_create_invalid',
          issues: [{ pointer: 'create.locale', code: 'create.format' }],
        },
      },
    });
    await screen.findByTestId('create-database');
    await user.click(screen.getByLabelText('PostgreSQL database'));
    await user.type(await screen.findByLabelText('Database name'), 'shop');
    await user.type(screen.getByLabelText('Locale (optional)'), 'x y');
    await user.type(screen.getByLabelText('Type shop to create the database'), 'shop');
    await user.click(screen.getByRole('button', { name: 'Create database' }));
    await waitFor(() =>
      expect(screen.getByLabelText('Locale (optional)')).toHaveAttribute('aria-invalid', 'true'),
    );
    expect(screen.getByText('Not in the expected form.')).toBeInTheDocument();
  });

  it('goes back to the unlock when the server says the unlock ended', async () => {
    const user = userEvent.setup();
    setup({ unlocked: true, postgres: { status: 405, body: { code: 'studio.write_locked' } } });
    await screen.findByTestId('create-database');
    await user.click(screen.getByLabelText('PostgreSQL database'));
    await user.type(await screen.findByLabelText('Database name'), 'shop');
    await user.type(screen.getByLabelText('Type shop to create the database'), 'shop');
    await user.click(screen.getByRole('button', { name: 'Create database' }));
    expect(await screen.findByTestId('data-editor-unlock')).toBeInTheDocument();
    expect(screen.getByTestId('create-database-problem')).toHaveTextContent(
      'The write mode is locked. Unlock it again.',
    );
  });

  it('shows why the unlock ended when the admin role became over-privileged', async () => {
    const user = userEvent.setup();
    setup({
      unlocked: true,
      postgres: { status: 405, body: { code: 'studio.admin_over_privileged' } },
    });
    await screen.findByTestId('create-database');
    await user.click(screen.getByLabelText('PostgreSQL database'));
    await user.type(await screen.findByLabelText('Database name'), 'shop');
    await user.type(screen.getByLabelText('Type shop to create the database'), 'shop');
    await user.click(screen.getByRole('button', { name: 'Create database' }));
    expect(await screen.findByTestId('data-editor-unlock')).toBeInTheDocument();
    expect(screen.getByTestId('create-database-problem')).toHaveTextContent(
      'The admin role has or reaches SUPERUSER',
    );
  });

  it('has German texts', async () => {
    await i18n.changeLanguage('de');
    try {
      setup();
      expect(await screen.findByText('Neue Datenbank (in Entwicklung)')).toBeInTheDocument();
      expect(screen.getByLabelText('Dateipfad')).toBeInTheDocument();
    } finally {
      await i18n.changeLanguage('en');
    }
  });

  it('has no serious or critical axe violation in either form', async () => {
    const user = userEvent.setup();
    const { view } = setup({ unlocked: true });
    await screen.findByTestId('create-database');
    await user.type(screen.getByLabelText('File path'), '/data/a.db');
    expect(await serious(view.container)).toEqual([]);
    await user.click(screen.getByLabelText('PostgreSQL database'));
    await screen.findByTestId('create-postgres');
    expect(await serious(view.container)).toEqual([]);
  });
});
