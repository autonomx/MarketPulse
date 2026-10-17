import { useCallback, useEffect, useState } from 'react';
import { omnixApiClient, type ProviderFacadePayload } from '../../api/client';
import { ProviderDefaultsSection } from './ProviderDefaultsSection';
import { ProviderRegistrySection } from './ProviderRegistrySection';
import { ProviderRoutingSection } from './ProviderRoutingSection';
import './AiProvidersSettings.css';

export function AiProvidersSettings() {
  const [payload, setPayload] = useState<ProviderFacadePayload>();
  const [loading, setLoading] = useState(true);
  const [actionStatus, setActionStatus] = useState('');

  const loadProviders = useCallback(async () => {
    setLoading(true);
    try {
      setPayload(await omnixApiClient.listProviders());
      setActionStatus('Provider registry refreshed.');
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : 'Provider registry is unavailable.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadProviders(); }, [loadProviders]);

  return (
    <div className="settings-category-panel" aria-labelledby="settings-category-title">
      <div className="settings-category-title-row">
        <p className="eyebrow">Settings category</p>
        <h2 id="settings-category-title">AI Providers</h2>
        <p>Manage provider defaults, connection status, and model routing.</p>
      </div>
      <ProviderDefaultsSection payload={payload} />
      <ProviderRegistrySection payload={payload} loading={loading} actionStatus={actionStatus} onRefresh={loadProviders} />
      <ProviderRoutingSection payload={payload} />
    </div>
  );
}
