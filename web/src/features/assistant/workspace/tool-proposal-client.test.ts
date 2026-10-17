import { afterEach, describe, expect, it, vi } from 'vitest';
import { executeToolProposal } from './tool-proposal-client';

const request = { tool_id: 'gmail', action_id: 'gmail.send_email', input: { body: 'Hello' } };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

afterEach(() => vi.unstubAllGlobals());

describe('durable tool proposal client', () => {
  it('uses three requests and binds execution to the server proposal id', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ proposal_id: 'server/id', approval_required: true }))
      .mockResolvedValueOnce(response({ decision: 'approved' }))
      .mockResolvedValueOnce(response({ execution_result: { error: null } }));
    vi.stubGlobal('fetch', fetchMock);
    await executeToolProposal(request, true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      '/api/assistant/tools/proposals', '/api/assistant/tools/proposals/server%2Fid/approve',
      '/api/assistant/tools/proposals/server%2Fid/execute',
    ]);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(request);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({});
    expect(fetchMock.mock.calls[2][1].body).toBeUndefined();
  });

  it('does not approve or execute without an explicit confirmation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ proposal_id: 'server-id', approval_required: true }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(executeToolProposal(request)).rejects.toThrow('explicit approval');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('uses server policy to omit approval for automatic actions', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ proposal_id: 'server-id', approval_required: false }))
      .mockResolvedValueOnce(response({ execution_result: { error: null } }));
    vi.stubGlobal('fetch', fetchMock);
    await executeToolProposal(request);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe('/api/assistant/tools/proposals/server-id/execute');
  });

  it('stops when approval is rejected', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ proposal_id: 'server-id', approval_required: true }))
      .mockResolvedValueOnce(response({ detail: 'denied' }, 403));
    vi.stubGlobal('fetch', fetchMock);
    await expect(executeToolProposal(request, true)).rejects.toThrow('403');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('stops when proposing is rejected', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ detail: 'disabled' }, 403));
    vi.stubGlobal('fetch', fetchMock);
    await expect(executeToolProposal(request, true)).rejects.toThrow('403');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
