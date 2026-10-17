import { afterEach, describe, expect, it, vi } from 'vitest';
import { checkHermesRpgSequence } from './hermesRpgSequenceClient';

describe('hermesRpgSequenceClient', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('posts sequence data to the sequence endpoint', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => Response.json({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const request = {
      sequence_id: 'seq-1',
      objective: 'Review room details',
      domain: 'rpg',
      state_owner: 'rpg_sim',
      items: [{ item_id: 'look', statement: 'look around' }],
    };
    const result = await checkHermesRpgSequence(request);

    expect(result).toEqual({ ok: true });
    const [input, init] = fetchMock.mock.calls[0];
    expect(new URL(String(input), 'http://localhost').pathname).toBe('/api/hermes/rpg/sequence/review');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual(request);
  });
});
