import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixApiClient, type CreateJobRequest } from '../../../api/client';
import { rpgSessionClient } from './rpgSessionClient';

const turnRequest = {
  module: 'rpg',
  type: 'rpg.turn',
  resource_class: 'gpu:llm',
  priority: 0,
  input_ref: { session_id: 'session-1' },
  input_payload: { command: 'look around' },
} as unknown as CreateJobRequest;

describe('rpgSessionClient.submitTurnJob', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function gateway(turn: Response | (() => Response)) {
    const calls: string[] = [];
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(String(input instanceof Request ? input.url : input), 'http://localhost');
      calls.push(`${init?.method ?? 'GET'} ${url.pathname}`);
      if (url.pathname === '/api/rpg/sessions/session-1/turn') return typeof turn === 'function' ? turn() : turn;
      if (url.pathname === '/api/jobs/job%3Afg' || url.pathname === '/api/jobs/job:fg') {
        return Response.json({ id: 'job:fg', module: 'rpg', type: 'rpg.turn.foreground_record', status: 'completed', output_refs: [] });
      }
      return Response.json({ detail: 'unexpected' }, { status: 500 });
    });
    vi.stubGlobal('fetch', fetchMock);
    return calls;
  }

  it('returns the job the gateway completed with the foreground turn', async () => {
    const calls = gateway(Response.json({ ok: true, contract_version: 'rpg_turn_response_v2', job_id: 'job:fg', response: 'You see a door.' }));
    const createJob = vi.spyOn(omnixApiClient, 'createJob');

    const job = await rpgSessionClient.submitTurnJob(turnRequest);

    expect(calls[0]).toBe('POST /api/rpg/sessions/session-1/turn');
    expect(createJob).not.toHaveBeenCalled();
    expect(job).toMatchObject({ id: 'job:fg', type: 'rpg.turn.foreground_record', status: 'completed' });
  });

  it('does not queue an applied turn again when the gateway returns no job', async () => {
    gateway(Response.json({ ok: true, contract_version: 'rpg_turn_response_v2', response: 'You see a door.' }));
    const createJob = vi.spyOn(omnixApiClient, 'createJob');

    await expect(rpgSessionClient.submitTurnJob(turnRequest)).rejects.toThrow('did not return its job');
    expect(createJob).not.toHaveBeenCalled();
  });

  it('queues an ordinary job when the foreground route is missing', async () => {
    gateway(() => Response.json({ detail: 'not found' }, { status: 404 }));
    const queued = { id: 'job-1' };
    const createJob = vi.spyOn(omnixApiClient, 'createJob').mockResolvedValue(queued as never);

    await expect(rpgSessionClient.submitTurnJob(turnRequest, { timeoutMs: 5 })).resolves.toBe(queued);
    expect(createJob).toHaveBeenCalledWith(turnRequest, { timeoutMs: 5 });
  });

  it('queues a turn without a session as an ordinary job', async () => {
    const calls = gateway(Response.json({}));
    const createJob = vi.spyOn(omnixApiClient, 'createJob').mockResolvedValue({ id: 'job-2' } as never);
    const request = { ...turnRequest, input_ref: null } as unknown as CreateJobRequest;

    await rpgSessionClient.submitTurnJob(request);

    expect(calls).toEqual([]);
    expect(createJob).toHaveBeenCalledWith(request, {});
  });
});

describe('OmnixApiClient.createJob', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('posts every job, including an RPG turn, to the jobs route', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => Response.json({ id: 'job-3' }));
    vi.stubGlobal('fetch', fetchMock);

    await omnixApiClient.createJob(turnRequest);

    const paths = fetchMock.mock.calls.map(([input]) => new URL(String(input instanceof Request ? input.url : input), 'http://localhost').pathname);
    expect(paths).toEqual(['/api/jobs']);
  });
});
