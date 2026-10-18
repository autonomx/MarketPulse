/** The Alpaca API keys (market data and AUTO PAPER quotes): write-only, kept in the OS-protected store. */
import { useEffect, useState } from 'react';
import { SettingsField, SettingsSection } from './SettingsPrimitives';
import { tradingMarketDataApi, type AlpacaCredentialStatus, type AlpacaCredentialUpdate } from './tradingMarketDataApi';

type Source = AlpacaCredentialStatus['api_key_source'];

function sourceLabel(source: Source, storage: string): string {
  if (source === 'environment') return 'from the environment';
  if (source === 'os_protected_store') return `stored in the ${storage}`;
  return 'not set';
}

function statusMessage(status: AlpacaCredentialStatus): string {
  if (status.configured) return `Alpaca is connected (key ${status.api_key_id_masked}).`;
  if (status.api_key_source !== 'missing' || status.secret_key_source !== 'missing') return 'Both the API key ID and the secret key are needed.';
  return 'Add Alpaca API keys to turn on the features below. A free account at alpaca.markets includes market data.';
}

export function AlpacaKeySettings() {
  const [status, setStatus] = useState<AlpacaCredentialStatus>();
  const [keyId, setKeyId] = useState('');
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('Checking the Alpaca keys...');

  useEffect(() => {
    tradingMarketDataApi.alpacaCredentials().then((next) => {
      setStatus(next);
      setMessage(statusMessage(next));
    }, (error: unknown) => setMessage(error instanceof Error ? error.message : 'The Alpaca key status is unavailable.'));
  }, []);

  const update = async (input: AlpacaCredentialUpdate, done: (next: AlpacaCredentialStatus) => string) => {
    setBusy(true);
    try {
      const next = await tradingMarketDataApi.saveAlpacaCredentials(input);
      setStatus(next);
      setKeyId('');
      setSecret('');
      setMessage(done(next));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'The Alpaca keys could not be saved.');
    } finally {
      setBusy(false);
    }
  };

  const save = () => {
    const input: AlpacaCredentialUpdate = {};
    if (keyId.trim()) input.api_key_id = keyId.trim();
    if (secret.trim()) input.secret_key = secret.trim();
    void update(input, (next) => (next.configured ? 'Alpaca keys saved. Secret values are never shown again.' : statusMessage(next)));
  };

  const storage = status?.storage ?? 'OS-protected store';
  const keyEditable = status?.api_key_editable ?? true;
  const secretEditable = status?.secret_key_editable ?? true;
  const stored = status?.api_key_source === 'os_protected_store' || status?.secret_key_source === 'os_protected_store';
  return (
    <SettingsSection
      title="Alpaca"
      description="US stock market data: real-time quotes for AUTO PAPER, option chains, dividends and splits, market breadth, and rebuilt gapper history in backtests."
      scope="global"
    >
      <div className="settings-form-grid">
        <SettingsField
          label="API key ID"
          help={`${status ? `Currently ${sourceLabel(status.api_key_source, storage)}` : 'Checking...'}. Environment override: OMNIX_ALPACA_API_KEY_ID or APCA_API_KEY_ID.`}
        >
          <input
            aria-label="Alpaca API key ID"
            type="password"
            autoComplete="off"
            value={keyId}
            disabled={busy || !keyEditable}
            placeholder={status?.api_key_id_masked || (keyEditable ? 'Enter API key ID' : 'Set in the environment')}
            onChange={(event) => setKeyId(event.currentTarget.value)}
          />
        </SettingsField>
        <SettingsField
          label="Secret key"
          help={`${status ? `Currently ${sourceLabel(status.secret_key_source, storage)}` : 'Checking...'}. Environment override: OMNIX_ALPACA_API_SECRET_KEY or APCA_API_SECRET_KEY.`}
        >
          <input
            aria-label="Alpaca secret key"
            type="password"
            autoComplete="new-password"
            value={secret}
            disabled={busy || !secretEditable}
            placeholder={status?.secret_key_source === 'missing' || !status ? 'Enter secret key' : '••••••••'}
            onChange={(event) => setSecret(event.currentTarget.value)}
          />
        </SettingsField>
        <div className="settings-inline-actions">
          <button type="button" className="settings-primary-button" aria-label="Save Alpaca keys" disabled={busy || (!keyId.trim() && !secret.trim())} onClick={save}>
            {busy ? 'Saving...' : 'Save keys'}
          </button>
          {stored ? (
            <button
              type="button"
              className="settings-secondary-button"
              aria-label="Clear stored Alpaca keys"
              disabled={busy}
              onClick={() => void update({ clear_api_key_id: true, clear_secret_key: true }, (next) => (next.configured ? 'Stored keys cleared; keys from the environment are still in use.' : 'Stored Alpaca keys cleared.'))}
            >
              Clear stored keys
            </button>
          ) : null}
        </div>
        <p className="settings-inline-status" role="status">{message}</p>
      </div>
      <p className="settings-inline-status">Leave a field blank to keep its current value. The same keys appear in the Strategies panel for AUTO PAPER.</p>
    </SettingsSection>
  );
}
