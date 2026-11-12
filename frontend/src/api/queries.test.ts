import { describe, expect, it } from 'vitest';

import { followQuery, parseStatus, readEvents } from './queries';

function stream(text: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const part of text) controller.enqueue(encoder.encode(part));
      controller.close();
    },
  });
}

describe('parseStatus', () => {
  it('reads the state document and fails closed on an unknown state', () => {
    expect(
      parseStatus({ query_id: 'q', state: 'done', row_count: 2, truncated_by: 'rows' }),
    ).toMatchObject({
      queryId: 'q',
      state: 'done',
      rowCount: 2,
      truncatedBy: 'rows',
      problem: null,
    });
    expect(parseStatus({ query_id: 'q', state: 'weird' })?.state).toBe('failed');
    expect(parseStatus({ query_id: 'q', state: 'weird' })?.problem?.code).toBe('studio.unknown');
    expect(parseStatus({})).toBeNull();
  });
});

describe('readEvents', () => {
  it('reads events split across chunks and skips keep-alive comments', async () => {
    const events = [];
    for await (const event of readEvents(
      stream([
        ': keep-alive\n\nevent: st',
        'ate\r\ndata: {"a":1}\r\n\r\nevent: done\ndata: {"b"',
        ':2}\n\n',
      ]),
    )) {
      events.push(event);
    }
    expect(events).toEqual([
      { event: 'state', data: '{"a":1}' },
      { event: 'done', data: '{"b":2}' },
    ]);
  });
});

describe('followQuery', () => {
  it('follows the stream to the final state', async () => {
    const seen: string[] = [];
    const fetcher: typeof fetch = async () =>
      new Response(
        stream([
          'event: state\ndata: {"query_id":"q","state":"running"}\n\n',
          'event: done\ndata: {"query_id":"q","state":"done","row_count":1}\n\n',
        ]),
      );
    const result = await followQuery(
      fetcher,
      'q',
      (s) => seen.push(s.state),
      new AbortController().signal,
    );
    expect(result.ok && result.value.rowCount).toBe(1);
    expect(seen).toEqual(['running', 'done']);
  });

  it('asks for the state when the stream breaks', async () => {
    const urls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      urls.push(String(input));
      if (String(input).endsWith('/events')) throw new TypeError('network');
      return new Response(JSON.stringify({ query_id: 'q', state: 'canceled' }));
    };
    const result = await followQuery(fetcher, 'q', () => undefined, new AbortController().signal);
    expect(result.ok && result.value.state).toBe('canceled');
    expect(urls).toEqual(['/api/queries/q/events', '/api/queries/q']);
  });
});
