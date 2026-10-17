import type { ProviderFacadePayload } from '../../api/client';
import type { components } from './api/generated';

export interface WorkerHealthRecord {
  id: string;
  ok: boolean;
  status: string;
  capabilities: string[];
  mocked?: boolean;
  error?: string | null;
}

export type WorkerHealthPayload = components['schemas']['WorkerHealthPayload'];

export type ImageReadinessStatus = 'loading' | 'ready' | 'degraded' | 'blocked';

export interface ImageReadiness {
  status: ImageReadinessStatus;
  canGenerate: boolean;
  title: string;
  message: string;
  providerCount: number;
  workerMode: 'inline' | 'worker' | 'mock' | 'unavailable';
}

export function readyImageProviders(payload: ProviderFacadePayload | undefined) {
  return payload?.providers.filter((provider) => (
    provider.family === 'image'
    && provider.capabilities.includes('image')
    && (provider.status === 'available' || provider.status === 'configured')
  )) ?? [];
}

export function resolveImageReadiness(options: {
  providers?: ProviderFacadePayload;
  workers?: WorkerHealthPayload;
  loading?: boolean;
  providerError?: boolean;
  workerError?: boolean;
}): ImageReadiness {
  const { providers, workers, loading = false, providerError = false, workerError = false } = options;
  if (loading) {
    return readiness('loading', false, 'Checking image runtime', 'Loading provider and runtime status.', 0, 'inline');
  }
  if (providerError) {
    return readiness('blocked', false, 'Image providers unavailable', 'Provider status could not be loaded. Refresh or open Settings.', 0, 'unavailable');
  }

  const availableProviders = readyImageProviders(providers);
  if (!availableProviders.length) {
    return readiness('blocked', false, 'No ready image provider', 'Configure or repair an image provider before generating.', 0, 'unavailable');
  }

  const imageWorker = workers?.workers.find((worker) => (
    worker.id === 'image' || worker.capabilities.includes('image')
  ));
  if (imageWorker && !imageWorker.ok) {
    return readiness(
      'blocked',
      false,
      'Image runtime unreachable',
      imageWorker.error || 'The configured image worker is not reachable. Check Diagnostics.',
      availableProviders.length,
      'unavailable',
    );
  }
  if (imageWorker?.mocked) {
    return readiness('degraded', true, 'Mock image runtime', 'Generation is available through the mock image worker.', availableProviders.length, 'mock');
  }
  if (imageWorker) {
    return readiness('ready', true, 'Image runtime ready', `${availableProviders.length} image provider${availableProviders.length === 1 ? '' : 's'} available.`, availableProviders.length, 'worker');
  }
  if (workerError) {
    return readiness('degraded', true, 'Inline image runtime', 'Worker status could not be loaded; local inline generation remains available.', availableProviders.length, 'inline');
  }
  return readiness('ready', true, 'Inline image runtime ready', `${availableProviders.length} image provider${availableProviders.length === 1 ? '' : 's'} available.`, availableProviders.length, 'inline');
}

function readiness(
  status: ImageReadinessStatus,
  canGenerate: boolean,
  title: string,
  message: string,
  providerCount: number,
  workerMode: ImageReadiness['workerMode'],
): ImageReadiness {
  return { status, canGenerate, title, message, providerCount, workerMode };
}
