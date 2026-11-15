/** STU-XW-1: the write-profile section is hidden with every area off, and shows the N-6 note when one is on. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { openKinds, parseWriteAreas } from '../../api/write';
import { PROFILES } from '../../harness/profiles';
import { WriteProfilesSection } from './WriteProfiles';

const LOCAL = {
  id: 'user:parts-local',
  source: 'user',
  name: 'parts-local',
  engine: 'sqlite',
  environment: 'dev',
  database: '/data/parts.db',
  password: null,
  secret: { source: null, has_secret: null },
};

const SOCKET = {
  id: 'user:parts-socket',
  source: 'user',
  name: 'parts-socket',
  engine: 'postgresql',
  environment: 'dev',
  host: '/var/run/postgresql',
  port: 5432,
  database: 'parts',
  user: 'reader',
  password: null,
  secret: { source: null, has_secret: null },
};

const TCP = { ...SOCKET, id: 'user:parts-tcp', name: 'parts-tcp', host: 'localhost' };

function setup(areas: Record<string, unknown>) {
  const posts: unknown[] = [];
  const urls: string[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    urls.push(url);
    if (url === '/api/write/areas') return new Response(JSON.stringify({ areas }));
    if (url.startsWith('/api/write/audit'))
      return new Response(
        JSON.stringify({
          available: true,
          problem: null,
          pending: 0,
          verification: { ok: true, records: 0, problem: null, at: null },
          records: [],
        }),
      );
    if (url === '/api/profiles')
      return new Response(
        JSON.stringify({ ...PROFILES, profiles: [...PROFILES.profiles, LOCAL, SOCKET, TCP] }),
      );
    if (url === '/api/write/profiles' && init?.method === 'POST') {
      posts.push(JSON.parse(String(init.body)));
      return new Response(JSON.stringify({ id: 'write:w', digest: 'c'.repeat(64) }), {
        status: 201,
      });
    }
    return new Response(null, { status: 404 });
  };
  render(<WriteProfilesSection fetcher={fetcher} />);
  return { posts, urls };
}

describe('WriteProfilesSection (STU-XW-1)', () => {
  it('renders nothing while every write area is off', async () => {
    const { urls } = setup({ data_editor: false, schema_designer: false, database_manager: false });
    await waitFor(() => expect(urls).toContain('/api/write/areas'));
    expect(screen.queryByTestId('write-profiles')).toBeNull();
    expect(urls).not.toContain('/api/profiles');
  });

  it('reads anything but a literal true as off', () => {
    const areas = parseWriteAreas({
      areas: { data_editor: 'true', schema_designer: 1, database_manager: 'on' },
    });
    expect(openKinds(areas)).toEqual([]);
    expect(openKinds(parseWriteAreas(null))).toEqual([]);
    expect(openKinds(parseWriteAreas({ areas: { database_manager: true } }))).toEqual(['admin']);
  });

  it('shows the production note and sends the profile only after it was confirmed', async () => {
    const user = userEvent.setup();
    const { posts } = setup({ data_editor: true, schema_designer: false, database_manager: false });
    const section = await screen.findByTestId('write-profiles');
    expect(screen.getByRole('note')).toHaveTextContent(/production/i);
    // Only the user's own non-PROD profiles can be coupled.
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'parts-local' })).toBeInTheDocument(),
    );
    expect(screen.queryByRole('option', { name: 'measure-central' })).toBeNull();
    expect(screen.queryByRole('option', { name: 'parts-dev' })).toBeNull();
    expect(screen.queryByRole('option', { name: /admin/i })).toBeNull();
    const create = screen.getByRole('button', { name: /create/i });
    expect(create).toBeDisabled();
    await user.type(screen.getAllByRole('textbox')[0] as HTMLElement, 'parts-write');
    await user.selectOptions(screen.getAllByRole('combobox')[1] as HTMLElement, 'parts-local');
    await user.click(create);
    expect(posts).toEqual([]);
    await user.click(screen.getByRole('checkbox'));
    expect(create).toBeEnabled();
    await user.click(create);
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      profile: { name: 'parts-write', kind: 'write', read_profile: 'parts-local' },
      digest: 'b'.repeat(64),
      confirm_no_prod_credentials: true,
    });
    expect(section).toBeInTheDocument();
  });

  it('asks for the server OS user only for a Unix socket and sends it (N-2 of PR #116)', async () => {
    const user = userEvent.setup();
    const { posts } = setup({ data_editor: true, schema_designer: false, database_manager: false });
    await screen.findByTestId('write-profiles');
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'parts-socket' })).toBeInTheDocument(),
    );
    const readSelect = screen.getAllByRole('combobox')[1] as HTMLElement;
    await user.selectOptions(readSelect, 'parts-tcp');
    expect(screen.queryByLabelText(/operating system user/i)).toBeNull();
    await user.selectOptions(readSelect, 'parts-socket');
    await user.type(screen.getAllByRole('textbox')[0] as HTMLElement, 'socket-write');
    await user.type(screen.getByLabelText(/database role/i), 'writer');
    await user.type(screen.getByLabelText(/operating system user/i), 'postgres');
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /create/i }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({
      profile: {
        name: 'socket-write',
        kind: 'write',
        read_profile: 'parts-socket',
        user: 'writer',
        password: 'keyring:',
        requirepeer: 'postgres',
      },
    });
  });
});
