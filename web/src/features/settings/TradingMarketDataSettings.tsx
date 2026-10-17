import { useEffect, useState } from 'react';
import { FredKeySettings } from './FredKeySettings';
import { IbkrSettingsSection } from './IbkrSettings';
import { SettingsField, SettingsSection } from './SettingsPrimitives';
import { tradingMarketDataApi, type CoinMarketCapCredentialStatus } from './tradingMarketDataApi';

function sourceLabel(source: CoinMarketCapCredentialStatus['api_key_source']): string {
  if (source === 'environment') return 'Environment variable';
  if (source === 'os_protected_store') return 'Windows user-scoped protected store';
  return 'Not configured';
}

export function TradingMarketDataSettings() {
  const [status, setStatus] = useState<CoinMarketCapCredentialStatus>();
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('Checking market-data credentials...');

  const load = async () => {
    try {
      const next = await tradingMarketDataApi.coinmarketcapCredentials();
      setStatus(next);
      setMessage(next.configured
        ? 'CoinMarketCap market-cap data is configured.'
        : 'Add a CoinMarketCap API key to enable CRYPTOCAP symbols and dominance charts.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Market-data credential status is unavailable.');
    }
  };

  useEffect(() => { void load(); }, []);

  const save = async () => {
    const value = apiKey.trim();
    if (!value) {
      setMessage('Enter a CoinMarketCap API key. The key is never returned to the browser after saving.');
      return;
    }
    setBusy(true);
    try {
      const next = await tradingMarketDataApi.saveCoinMarketCapCredentials({ api_key: value });
      setStatus(next);
      setApiKey('');
      setMessage('CoinMarketCap API key saved in the OS-protected store.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'CoinMarketCap credential update failed.');
    } finally {
      setBusy(false);
    }
  };

  const clearStored = async () => {
    if (!status || !status.api_key_editable || status.api_key_source !== 'os_protected_store') {
      setMessage('Environment-owned values must be changed outside Omnix.');
      return;
    }
    if (!window.confirm('Clear the CoinMarketCap key stored by Omnix?')) return;
    setBusy(true);
    try {
      const next = await tradingMarketDataApi.saveCoinMarketCapCredentials({ clear_api_key: true });
      setStatus(next);
      setApiKey('');
      setMessage(next.configured
        ? 'Stored key cleared; an environment-owned key is still active.'
        : 'Stored CoinMarketCap key cleared.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'CoinMarketCap credential update failed.');
    } finally {
      setBusy(false);
    }
  };

  const editable = status?.api_key_editable ?? true;
  return (
    <div className="settings-category-panel" aria-labelledby="settings-category-title">
      <div className="settings-category-title-row">
        <p className="eyebrow">Settings category</p>
        <h2 id="settings-category-title">Trading &amp; Market Data</h2>
        <p>Configure market-data providers used by charts, arithmetic symbols, watchlists, and replay.</p>
      </div>

      <SettingsSection
        title="CoinMarketCap"
        description="Historical crypto market-cap data for CRYPTOCAP symbols such as TOTAL3 and USDT.D."
        scope="global"
      >
        <div className="settings-form-grid">
          <SettingsField
            label="API key"
            help={`${status ? sourceLabel(status.api_key_source) : 'Checking credential source...'}. Environment override: COINMARKETCAP_API_KEY or CMC_PRO_API_KEY.`}
          >
            <input
              aria-label="CoinMarketCap API key"
              type="password"
              autoComplete="new-password"
              value={apiKey}
              disabled={busy || !editable}
              placeholder={status?.configured ? status.api_key_masked : 'Enter API key'}
              onChange={(event) => setApiKey(event.currentTarget.value)}
            />
            <button type="button" className="settings-primary-button" disabled={busy || !editable || !apiKey.trim()} onClick={() => void save()}>
              {busy ? 'Saving...' : 'Save key'}
            </button>
            {status?.configured && status.api_key_source === 'os_protected_store' ? (
              <button type="button" className="settings-secondary-button" disabled={busy} onClick={() => void clearStored()}>Clear stored key</button>
            ) : null}
          </SettingsField>
          <div className="settings-status-card">
            <h3>Coverage</h3>
            <p>CRYPTOCAP: TOTAL, TOTAL2, TOTAL3, BTC, ETH, USDT, and dominance symbols ending in <code>.D</code>.</p>
            <p className="settings-inline-status" role="status">{message}</p>
          </div>
        </div>
        <p className="settings-inline-status">Secret values are never stored in chart or strategy configuration. UI-entered keys are protected with the current Windows user's DPAPI key.</p>
      </SettingsSection>

      <FredKeySettings />

      <IbkrSettingsSection />
    </div>
  );
}
