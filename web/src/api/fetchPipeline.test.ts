import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  activeFetchMiddlewares,
  fetchBelow,
  baseFetch,
  registerFetchMiddleware,
  resetFetchPipelineForTests,
  pipelineFetch,
} from './fetchPipeline';

afterEach(() => {
  resetFetchPipelineForTests();
  vi.unstubAllGlobals();
});

function recordingBase(calls: string[]) {
  const base = vi.fn(async (input: RequestInfo | URL) => {
    calls.push(`base ${String(input)}`);
    return new Response('ok');
  });
  vi.stubGlobal('fetch', base);
  return base;
}

describe('fetch pipeline', () => {
  it('runs feature middleware newest first, then transport, then the base fetch', async () => {
    const calls: string[] = [];
    recordingBase(calls);
    registerFetchMiddleware('firewall', (input, init, next) => { calls.push('firewall'); return next(input, init); }, { layer: 'transport' });
    registerFetchMiddleware('older', (input, init, next) => { calls.push('older'); return next(input, init); });
    registerFetchMiddleware('newer', (input, init, next) => { calls.push('newer'); return next(input, init); });

    await pipelineFetch('/api/x');

    expect(calls).toEqual(['newer', 'older', 'firewall', 'base /api/x']);
    expect(activeFetchMiddlewares()).toEqual(['newer', 'older', 'firewall']);
  });

  it('removes middleware in any order without dropping the others', async () => {
    const calls: string[] = [];
    recordingBase(calls);
    const removeFirst = registerFetchMiddleware('first', (input, init, next) => { calls.push('first'); return next(input, init); });
    registerFetchMiddleware('second', (input, init, next) => { calls.push('second'); return next(input, init); });

    removeFirst();
    removeFirst();
    await pipelineFetch('/api/x');

    expect(calls).toEqual(['second', 'base /api/x']);
  });

  it('lets middleware rewrite the request for the layers below it', async () => {
    const calls: string[] = [];
    recordingBase(calls);
    registerFetchMiddleware('rewrite', (_input, init, next) => next('/api/rewritten', init));

    await pipelineFetch('/api/original');

    expect(calls).toEqual(['base /api/rewritten']);
  });

  it('ends at the current fetch, even one replaced after middleware was registered', async () => {
    const first: string[] = [];
    recordingBase(first);
    registerFetchMiddleware('a', (input, init, next) => next(input, init));
    const second: string[] = [];
    recordingBase(second);

    await pipelineFetch('/api/x');
    await baseFetch('/api/direct');

    expect(first).toEqual([]);
    expect(second).toEqual(['base /api/x', 'base /api/direct']);
  });

  it('leaves window.fetch to the browser', async () => {
    const calls: string[] = [];
    const base = recordingBase(calls);
    registerFetchMiddleware('a', (input, init, next) => { calls.push('a'); return next(input, init); });

    expect(window.fetch).toBe(base);
    await window.fetch('/assets/model.json');

    expect(calls).toEqual(['base /assets/model.json']);
    resetFetchPipelineForTests();
    expect(activeFetchMiddlewares()).toEqual([]);
  });
});

describe('fetchBelow', () => {
  it('skips the named middleware and newer ones but keeps older ones and transport', async () => {
    const calls: string[] = [];
    recordingBase(calls);
    registerFetchMiddleware('firewall', (input, init, next) => { calls.push('firewall'); return next(input, init); }, { layer: 'transport' });
    registerFetchMiddleware('older', (input, init, next) => { calls.push('older'); return next(input, init); });
    registerFetchMiddleware('self', (input, init, next) => { calls.push('self'); return next(input, init); });
    registerFetchMiddleware('newer', (input, init, next) => { calls.push('newer'); return next(input, init); });

    await fetchBelow('self')('/api/own');

    expect(calls).toEqual(['older', 'firewall', 'base /api/own']);
  });
});
