/** STU-XW-4: the I-109 j confirmation (D-15) shows the server's pair and confirms it only with the typed host. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { parseWriteProfiles } from '../../api/write';
import { SecretConfirmations } from './SecretConfirmations';

const HASH = 'a'.repeat(64);
const PREVIEW = {
  write_profile_id: 'write:pg-write',
  kind: 'write',
  confirmation_hash: HASH,
  confirm_text: 'db.example.org',
  confirmed: false,
  expires_in_s: 300,
  pair: {
    host: 'db.example.org',
    port: 5432,
    database: 'parts',
    sslmode: 'verify-full',
    role: 'app_writer',
    reference: { source: 'env', target: 'PARTS_WRITE_PW' },
    foreign: true,
  },
};

function setup(confirmAnswer: { status: number; body: unknown }, preview: unknown = PREVIEW) {
  const posts: { url: string; body: unknown }[] = [];
  let confirmed = false;
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    if (url === '/api/write/profiles')
      return new Response(
        JSON.stringify({
          write_profiles: [
            {
              id: 'write:pg-write',
              name: 'pg-write',
              kind: 'write',
              engine: 'postgresql',
              secret: { source: 'env', has_secret: true, confirmed, foreign: true },
            },
            {
              id: 'write:parts-write',
              name: 'parts-write',
              kind: 'write',
              engine: 'sqlite',
              secret: { source: null, has_secret: null, confirmed: null, foreign: null },
            },
          ],
        }),
      );
    if (init?.method === 'POST') {
      posts.push({ url, body: JSON.parse(String(init.body)) });
      if (url === '/api/write/secret-confirmations') return new Response(JSON.stringify(preview));
      if (url === '/api/write/secret-confirmations/confirm') {
        if (confirmAnswer.status === 200) confirmed = true;
        return new Response(JSON.stringify(confirmAnswer.body), {
          status: confirmAnswer.status,
          headers: { 'Content-Type': 'application/problem+json' },
        });
      }
    }
    return new Response(null, { status: 404 });
  };
  render(<SecretConfirmations fetcher={fetcher} />);
  return { posts };
}

describe('SecretConfirmations (STU-XW-4, D-15)', () => {
  it('lists only profiles with a secret and shows the pair to confirm', async () => {
    const { posts } = setup({ status: 200, body: { confirmed: true } });
    await screen.findByText(/pg-write/);
    expect(screen.queryByText(/parts-write/)).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Review' }));
    const dialog = await screen.findByTestId('secret-pair');
    expect(dialog.textContent).toContain('db.example.org:5432 / parts');
    expect(dialog.textContent).toContain('verify-full');
    expect(dialog.textContent).toContain('PARTS_WRITE_PW');
    expect(screen.getByRole('note').textContent).toMatch(/not on this computer/);
    expect(dialog.textContent).not.toMatch(/operating system user/);
    expect(posts[0]).toEqual({
      url: '/api/write/secret-confirmations',
      body: { write_profile_id: 'write:pg-write' },
    });
  });

  it('confirms only with the host typed exactly, and sends the hash, never the pair', async () => {
    const { posts } = setup({ status: 200, body: { confirmed: true } });
    await userEvent.click(await screen.findByRole('button', { name: 'Review' }));
    const confirm = await screen.findByRole('button', { name: 'Confirm' });
    expect(confirm).toBeDisabled();
    const input = screen.getByRole('textbox');
    await userEvent.type(input, 'db.example.or');
    expect(confirm).toBeDisabled();
    await userEvent.type(input, 'g');
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() => expect(screen.queryByTestId('secret-pair')).toBeNull());
    expect(posts[1]).toEqual({
      url: '/api/write/secret-confirmations/confirm',
      body: { confirmation_hash: HASH, typed: 'db.example.org' },
    });
    await waitFor(() =>
      expect(screen.getByRole('listitem').textContent).toMatch(/^pg-write: confirmed/),
    );
  });

  it("shows the server's OS user when the profile names one (N-2 of PR #116)", async () => {
    setup(
      { status: 200, body: { confirmed: true } },
      {
        ...PREVIEW,
        confirm_text: '/var/run/postgresql',
        pair: {
          ...PREVIEW.pair,
          host: '/var/run/postgresql',
          foreign: false,
          requirepeer: 'postgres',
        },
      },
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Review' }));
    const dialog = await screen.findByTestId('secret-pair');
    expect(dialog.textContent).toContain("Server's operating system userpostgres");
  });

  it('shows the server refusal as a problem', async () => {
    setup({
      status: 400,
      body: { code: 'studio.secret_confirmation_stale', title: 'Profile changed', status: 400 },
    });
    await userEvent.click(await screen.findByRole('button', { name: 'Review' }));
    await userEvent.type(screen.getByRole('textbox'), 'db.example.org');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/changed since the review/);
  });

  it('parses defensively', () => {
    expect(parseWriteProfiles(null)).toEqual([]);
    expect(parseWriteProfiles({ write_profiles: [{ id: 1 }, null, 'x'] })).toEqual([]);
    expect(
      parseWriteProfiles({
        write_profiles: [{ id: 'write:a', name: 'a', kind: 'evil', secret: { confirmed: 'yes' } }],
      }),
    ).toEqual([
      {
        id: 'write:a',
        name: 'a',
        kind: 'write',
        engine: '',
        confirmed: null,
        foreign: null,
        readProfileId: '',
      },
    ]);
  });
});
