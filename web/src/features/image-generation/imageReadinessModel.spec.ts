import { describe, expect, it } from 'vitest';
import type { ProviderFacadePayload } from '../../api/client';
import { readyImageProviders, resolveImageReadiness } from './imageReadinessModel';
import { fixture } from '../../test/fixture';

const providers = fixture<ProviderFacadePayload>({
  providers: [
    { id: 'image:flux_klein', label: 'FLUX', family: 'image', capabilities: ['image'], status: 'configured', source: 'settings' },
    { id: 'image:broken', label: 'Broken', family: 'image', capabilities: ['image'], status: 'degraded', source: 'settings' },
    { id: 'llm:local', label: 'Local', family: 'llm', capabilities: ['chat'], status: 'configured', source: 'settings' },
  ],
  models: [],
});

describe('image runtime readiness', () => {
  it('keeps only usable standalone image providers', () => {
    expect(readyImageProviders(providers).map((provider) => provider.id)).toEqual(['image:flux_klein']);
  });

  it('uses inline generation when no image worker is configured', () => {
    expect(resolveImageReadiness({ providers, workers: fixture({ ok: true, status: 'not_configured', workers: [] }) })).toMatchObject({
      status: 'ready',
      canGenerate: true,
      workerMode: 'inline',
      providerCount: 1,
    });
  });

  it('blocks generation when an image worker is unreachable', () => {
    expect(resolveImageReadiness({
      providers,
      workers: fixture({
        ok: false,
        status: 'degraded',
        workers: [{ id: 'image', ok: false, status: 'unreachable', capabilities: ['image'], error: 'connection refused' }],
      }),
    })).toMatchObject({ status: 'blocked', canGenerate: false, workerMode: 'unavailable' });
  });

  it('blocks generation when no provider is ready', () => {
    const unavailable = { providers: providers.providers.filter((provider) => provider.id === 'image:broken'), models: [] } as ProviderFacadePayload;
    expect(resolveImageReadiness({ providers: unavailable })).toMatchObject({ status: 'blocked', canGenerate: false, providerCount: 0 });
  });
});
