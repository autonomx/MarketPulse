import type { ProviderFacadePayload } from '../../api/client';
import { SettingsSection } from './SettingsPrimitives';

function statusTone(status: string) {
  if (status === 'available' || status === 'configured') return 'ready';
  if (status === 'degraded') return 'warning';
  return 'idle';
}

function metadataText(metadata: Record<string, unknown> | undefined, key: string, fallback: string) {
  const value = metadata?.[key];
  return typeof value === 'string' && value.trim() ? value : fallback;
}

export function ProviderRegistrySection({ payload, loading, actionStatus, onRefresh }: {
  payload?: ProviderFacadePayload;
  loading: boolean;
  actionStatus: string;
  onRefresh: () => void;
}) {
  const providers = payload?.providers ?? [];
  return (
    <SettingsSection
      title="Provider connections"
      description="Capabilities and configuration are reported by the gateway provider registry."
      actions={<button type="button" className="settings-secondary-button" onClick={onRefresh} disabled={loading}>Test connections</button>}
    >
      {actionStatus ? <p className="settings-inline-status" role="status">{actionStatus}</p> : null}
      {providers.length ? (
        <div className="provider-settings-list">
          {providers.map((provider) => (
            <article className="provider-settings-card" key={provider.id}>
              <header>
                <div><strong>{provider.label}</strong><small>{provider.family} · {provider.source}</small></div>
                <span className={`provider-state tone-${statusTone(provider.status)}`}>{provider.status}</span>
              </header>
              <dl>
                <div><dt>Endpoint</dt><dd>{metadataText(provider.metadata, 'base_url', metadataText(provider.metadata, 'endpoint', 'Managed by runtime'))}</dd></div>
                <div><dt>Capabilities</dt><dd>{provider.capabilities.join(', ') || 'None reported'}</dd></div>
                <div><dt>Last error</dt><dd>{metadataText(provider.metadata, 'last_error', 'None')}</dd></div>
              </dl>
              <details>
                <summary>Configure</summary>
                <p>Configuration fields are managed by the runtime. Sensitive values are never returned to this page.</p>
              </details>
            </article>
          ))}
        </div>
      ) : <div className="settings-planned-state"><strong>{loading ? 'Loading provider registry…' : 'No providers discovered'}</strong><p>Refresh the provider registry after local or remote services are configured.</p></div>}
    </SettingsSection>
  );
}
