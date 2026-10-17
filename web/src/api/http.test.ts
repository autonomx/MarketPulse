import { describe, expect, it, vi } from 'vitest';
import { ApiError } from './errors';
import { createGatewayClient, unwrap } from './http';

function client(response: Response) {
  const fetchImpl = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => response);
  return { fetchImpl, api: createGatewayClient({ fetchImpl: fetchImpl as unknown as typeof fetch }) };
}

describe('typed gateway client', () => {
  it('issues the same (path, init) call a handwritten client would', async () => {
    const { api, fetchImpl } = client(Response.json({ jobs: [], next_cursor: null, has_more: false }));

    const page = await unwrap(api.GET('/api/jobs', { params: { query: { limit: 50, cursor: 'abc' } } }));

    expect(page.jobs).toEqual([]);
    expect(fetchImpl).toHaveBeenCalledWith('/api/jobs?limit=50&cursor=abc', { method: 'GET' });
  });

  it('sends JSON bodies with their content type and path parameters encoded', async () => {
    const { api, fetchImpl } = client(Response.json({ id: 'job:1' }));

    await unwrap(api.POST('/api/jobs/{job_id}/cancel', { params: { path: { job_id: 'job:1' } }, body: { reason: 'stop' } }));

    expect(fetchImpl).toHaveBeenCalledWith('/api/jobs/job%3A1/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'stop' }),
    });
  });

  it('raises ApiError with the gateway detail and request id', async () => {
    const { api } = client(Response.json({ detail: 'invalid_cursor' }, { status: 400, headers: { 'X-Request-ID': 'req-7' } }));

    const failure = unwrap(api.GET('/api/jobs', { params: { query: { cursor: 'bad' } } }));

    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({ status: 400, requestId: 'req-7', message: expect.stringContaining('invalid_cursor') });
  });

  it('resolves fetch when called, so the fetch pipeline installed later still applies', async () => {
    const later = vi.fn(async () => Response.json({ jobs: [], next_cursor: null, has_more: false }));
    vi.stubGlobal('fetch', later);
    try {
      await unwrap(createGatewayClient().GET('/api/jobs'));
      expect(later).toHaveBeenCalledTimes(1);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
