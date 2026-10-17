import { useCallback, useEffect, useState } from 'react';
import { SettingsField } from './SettingsPrimitives';
import { unwrap } from '../../api/http';
import { api } from './api/gateway';

type CredentialProvider = 'brave' | 'tavily';

type ResearchCredentialProviderStatus = {
  provider: CredentialProvider;
  configured: boolean;
  source: 'environment' | 'legacy_environment' | 'os_protected_store' | 'missing';
  editable: boolean;
  key_suffix: string | null;
};

type ResearchCredentialStatus = {
  providers: ResearchCredentialProviderStatus[];
  legacy_environment_key: boolean;
};

const PROVIDERS: Array<{
  provider: CredentialProvider;
  label: string;
  environment: string;
}> = [
  {
    provider: 'brave',
    label: 'Brave Search API key',
    environment: 'OMNIX_BRAVE_SEARCH_API_KEY or BRAVE_SEARCH_API_KEY',
  },
  {
    provider: 'tavily',
    label: 'Tavily API key',
    environment: 'OMNIX_TAVILY_SEARCH_API_KEY or TAVILY_API_KEY',
  },
];

function sourceLabel(status: ResearchCredentialProviderStatus | undefined): string {
  if (!status) return 'Checking credential source…';
  if (status.source === 'environment') return 'Environment variable';
  if (status.source === 'legacy_environment') return 'Legacy shared OMNIX_WEB_SEARCH_API_KEY';
  if (status.source === 'os_protected_store') return 'Windows user-scoped protected store';
  return status.editable ? 'Not configured' : 'Not configured · use an environment variable on this platform';
}

export function ResearchCredentialSettings() {
  const [status, setStatus] = useState<ResearchCredentialStatus>();
  const [inputs, setInputs] = useState<Record<CredentialProvider, string>>({ brave: '', tavily: '' });
  const [busyProvider, setBusyProvider] = useState<CredentialProvider | null>(null);
  const [message, setMessage] = useState('Checking search credentials…');

  const load = useCallback(async () => {
    try {
      // The credential routes return untyped objects.
      const result = (await unwrap(api.GET('/api/assistant/research/credentials'))) as unknown as ResearchCredentialStatus;
      setStatus(result);
      setMessage('Search credential status loaded.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Search credential status is unavailable.');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const updateCredential = async (provider: CredentialProvider, apiKey: string) => {
    setBusyProvider(provider);
    try {
      const result = (await unwrap(api.POST('/api/assistant/research/credentials', { body: { provider, api_key: apiKey } }))) as unknown as ResearchCredentialStatus;
      setStatus(result);
      setInputs((current) => ({ ...current, [provider]: '' }));
      setMessage(apiKey ? `${provider === 'brave' ? 'Brave' : 'Tavily'} credential saved.` : `${provider === 'brave' ? 'Brave' : 'Tavily'} credential cleared.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Search credential update failed.');
    } finally {
      setBusyProvider(null);
    }
  };

  return (
    <div className="provider-config-grid">
      <div className="provider-config-group">
        <h4>Search API credentials</h4>
        <p>
          Brave and Tavily use independent credentials. UI-entered keys are stored only in the Windows user-scoped protected store;
          environment values remain authoritative and are never returned to the browser.
        </p>
        {status?.legacy_environment_key ? (
          <p className="settings-inline-status" role="status">
            Legacy OMNIX_WEB_SEARCH_API_KEY is active for compatibility. Migrate to provider-specific keys so Brave and Tavily can be configured independently.
          </p>
        ) : null}
        <div className="settings-form-grid">
          {PROVIDERS.map(({ provider, label, environment }) => {
            const providerStatus = status?.providers.find((item) => item.provider === provider);
            const editable = providerStatus?.editable ?? false;
            const configured = providerStatus?.configured ?? false;
            const suffix = providerStatus?.key_suffix;
            const input = inputs[provider];
            const busy = busyProvider === provider;
            return (
              <SettingsField
                key={provider}
                label={label}
                help={`${sourceLabel(providerStatus)}. Environment override: ${environment}.`}
              >
                <input
                  type="password"
                  autoComplete="off"
                  value={input}
                  disabled={!editable || busy}
                  placeholder={configured && suffix ? `Configured · ••••${suffix}` : 'Enter API key'}
                  onChange={(event) => {
                    const { value } = event.currentTarget;
                    setInputs((current) => ({ ...current, [provider]: value }));
                  }}
                />
                <button
                  type="button"
                  className="settings-secondary-button"
                  disabled={!editable || busy || !input.trim()}
                  onClick={() => { void updateCredential(provider, input.trim()); }}
                >
                  {busy ? 'Saving…' : 'Save key'}
                </button>
                {configured && providerStatus?.source === 'os_protected_store' ? (
                  <button
                    type="button"
                    className="settings-secondary-button"
                    disabled={!editable || busy}
                    onClick={() => { void updateCredential(provider, ''); }}
                  >
                    Clear stored key
                  </button>
                ) : null}
              </SettingsField>
            );
          })}
        </div>
        <p className="settings-inline-status" role="status">{message}</p>
      </div>
    </div>
  );
}
