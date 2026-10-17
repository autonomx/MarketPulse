import { useCallback, useEffect, useState } from 'react';
import { omnixApiClient, type ProviderFacadePayload } from '../../api/client';
import { unwrap } from '../../api/http';
import { api } from './api/gateway';

export type SettingsStatusSnapshot = {
  gateway: string;
  llm: string;
  tts: string;
  stt: string;
  image: string;
  hermes: string;
  activeJobs: number;
  loadedModels: number;
};

const initialStatus: SettingsStatusSnapshot = { gateway: 'Checking', llm: 'Checking', tts: 'Checking', stt: 'Checking', image: 'Checking', hermes: 'Checking', activeJobs: 0, loadedModels: 0 };

function familyStatus(payload: ProviderFacadePayload | undefined, family: string): string {
  const provider = payload?.providers.find((entry) => entry.family === family || entry.capabilities.some((capability) => capability === family));
  return provider ? provider.status : 'Unavailable';
}

export function useSettingsStatus() {
  const [status, setStatus] = useState(initialStatus);
  const [refreshing, setRefreshing] = useState(false);
  const [lastError, setLastError] = useState('');

  const refresh = useCallback(async () => {
    setRefreshing(true);
    const [runtime, providers, residency] = await Promise.allSettled([
      unwrap(api.GET('/api/runtime/status')) as Promise<Record<string, unknown>>,
      omnixApiClient.listProviders(),
      omnixApiClient.getModelResidency(),
    ]);
    const providerPayload = providers.status === 'fulfilled' ? providers.value : undefined;
    const residencyRows = residency.status === 'fulfilled' ? residency.value.records ?? [] : [];
    const runtimeValue = runtime.status === 'fulfilled' ? runtime.value : {};
    const activeJobs = Number(runtimeValue.active_jobs ?? runtimeValue.activeJobs ?? 0);
    setStatus({
      gateway: runtime.status === 'fulfilled' ? String(runtimeValue.status ?? (runtimeValue.ok ? 'Ready' : 'Degraded')) : 'Offline',
      llm: familyStatus(providerPayload, 'llm'),
      tts: familyStatus(providerPayload, 'tts'),
      stt: familyStatus(providerPayload, 'stt'),
      image: familyStatus(providerPayload, 'image'),
      hermes: String((runtimeValue.compatibility as Record<string, unknown> | undefined)?.hermes ?? 'Disabled'),
      activeJobs: Number.isFinite(activeJobs) ? activeJobs : 0,
      loadedModels: Array.isArray(residencyRows) ? residencyRows.length : 0,
    });
    const failure = [runtime, providers, residency].find((result) => result.status === 'rejected');
    setLastError(failure?.status === 'rejected' ? String(failure.reason instanceof Error ? failure.reason.message : failure.reason) : '');
    setRefreshing(false);
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  return { status, refreshing, lastError, refresh };
}
