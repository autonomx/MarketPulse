/** The FRED API key (TVP-10.5) for the trading economic calendar: write-only, kept in the OS-protected store. */
import { useEffect, useState } from 'react';
import { SettingsField, SettingsSection } from './SettingsPrimitives';
import { tradingMarketDataApi, type ProviderKeyStatus } from './tradingMarketDataApi';

export function FredKeySettings() {
  const [status, setStatus] = useState<ProviderKeyStatus>();
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('Checking the FRED key...');
  useEffect(() => {
    tradingMarketDataApi.fredCredentials().then((next) => {
      setStatus(next);
      setMessage(next.configured ? 'The economic calendar is on.' : 'Add a free FRED API key to show the economic calendar.');
    }, (error: unknown) => setMessage(error instanceof Error ? error.message : 'The FRED key status is unavailable.'));
  }, []);
  const update = async (input: { api_key?: string; clear_api_key?: boolean }, done: string) => {
    setBusy(true);
    try {
      const next = await tradingMarketDataApi.saveFredCredentials(input);
      setStatus(next);
      setApiKey('');
      setMessage(done);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'The FRED key could not be saved.');
    } finally {
      setBusy(false);
    }
  };
  const editable = status?.api_key_editable ?? true;
  return (
    <SettingsSection title="FRED (economic calendar)" description="US data releases and their values from the Federal Reserve Bank of St. Louis. A key is free at fred.stlouisfed.org." scope="global">
      <div className="settings-form-grid">
        <SettingsField label="API key" help="Environment override: OMNIX_FRED_API_KEY or FRED_API_KEY.">
          <input
            aria-label="FRED API key"
            type="password"
            autoComplete="new-password"
            value={apiKey}
            disabled={busy || !editable}
            placeholder={status?.configured ? status.api_key_masked : 'Enter API key'}
            onChange={(event) => setApiKey(event.currentTarget.value)}
          />
          <button type="button" className="settings-primary-button" aria-label="Save FRED key" disabled={busy || !editable || !apiKey.trim()} onClick={() => void update({ api_key: apiKey.trim() }, 'FRED key saved in the OS-protected store.')}>
            {busy ? 'Saving...' : 'Save key'}
          </button>
          {status?.configured && status.api_key_source === 'os_protected_store' ? (
            <button type="button" className="settings-secondary-button" aria-label="Clear stored FRED key" disabled={busy} onClick={() => void update({ clear_api_key: true }, 'Stored FRED key cleared.')}>Clear stored key</button>
          ) : null}
        </SettingsField>
        <p className="settings-inline-status" role="status">{message}</p>
      </div>
    </SettingsSection>
  );
}
