import { useState } from 'react';
import { downloadBlob } from '../../shared/download';
import { PRODUCT_NAME } from '../../shared/productIdentity';
import { SettingsField } from './SettingsPrimitives';
import { tradingMarketDataApi, type IbkrSettingsStatus, type IbkrWebApiPublicKeys } from './tradingMarketDataApi';

/** The files IBKR's OAuth page asks for, under the names its upload fields expect. */
const KEY_FILES: ReadonlyArray<{ key: keyof IbkrWebApiPublicKeys; filename: string; label: string; field: string }> = [
  { key: 'signature_public_key', filename: 'public_signature.pem', label: 'Signing key', field: 'Public Signing Key' },
  { key: 'encryption_public_key', filename: 'public_encryption.pem', label: 'Encryption key', field: 'Public Encryption Key' },
  { key: 'dh_parameters', filename: 'dhparam.pem', label: 'DH parameters', field: 'Diffie-Hellman Parameters' },
];

const API_ACCESS_EMAIL = 'apiintegration@interactivebrokers.com';

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

/**
 * The IBKR Web API connection, set up step by step: Omnix generates the keys, the user registers
 * them on IBKR's OAuth page and pastes back the tokens it issues. The private keys never leave the server.
 */
export function IbkrWebApiSetup({ status, onStatus }: {
  status: IbkrSettingsStatus | undefined;
  onStatus: (next: IbkrSettingsStatus) => void;
}) {
  const webApi = status?.web_api;
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [consumerKey, setConsumerKey] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [accessTokenSecret, setAccessTokenSecret] = useState('');
  const editable = webApi?.editable ?? false;
  // Connecting uses the saved settings, not unsaved changes above.
  const savedForWebApi = Boolean(status?.settings.enabled && status.settings.connection === 'web_api');

  const run = async (task: () => Promise<void>, fallback: string) => {
    setBusy(true);
    try {
      await task();
    } catch (error) {
      setMessage(errorText(error, fallback));
    } finally {
      setBusy(false);
    }
  };

  const downloadKeys = (files: IbkrWebApiPublicKeys) => {
    for (const file of KEY_FILES) {
      downloadBlob(new Blob([files[file.key]], { type: 'application/x-pem-file' }), file.filename);
    }
  };

  const generate = () => run(async () => {
    const replace = Boolean(webApi?.keys_generated);
    if (replace && !window.confirm('Replace the keys registered with IBKR? You will need to upload the new files and generate a new access token.')) return;
    const files = await tradingMarketDataApi.generateIbkrWebApiKeys(replace);
    downloadKeys(files);
    onStatus(await tradingMarketDataApi.ibkrSettings());
    setMessage('Keys generated. The three files were downloaded for step 3.');
  }, 'IBKR keys could not be generated.');

  const downloadFile = (filename: string, key: keyof IbkrWebApiPublicKeys) => run(async () => {
    const files = await tradingMarketDataApi.ibkrWebApiKeys();
    downloadBlob(new Blob([files[key]], { type: 'application/x-pem-file' }), filename);
  }, 'The key file could not be downloaded.');

  const saveTokens = () => run(async () => {
    const next = await tradingMarketDataApi.saveIbkrWebApiCredentials({
      ...(consumerKey.trim() ? { consumer_key: consumerKey } : {}),
      ...(accessToken.trim() ? { access_token: accessToken } : {}),
      ...(accessTokenSecret.trim() ? { access_token_secret: accessTokenSecret } : {}),
    });
    onStatus(next);
    setAccessToken('');
    setAccessTokenSecret('');
    setMessage(next.web_api.ready ? 'Saved. Connect in step 5.' : 'Saved. Some IBKR values are still missing.');
  }, 'IBKR tokens could not be saved.');

  const connect = () => run(async () => {
    const next = await tradingMarketDataApi.connectIbkr();
    onStatus(next);
    setMessage(next.connected ? `Connected: ${PRODUCT_NAME} is receiving IBKR data through the Web API.` : `Not connected yet${next.last_error ? `: ${next.last_error}` : '.'}`);
  }, 'IBKR could not be reached.');

  return (
    <fieldset className="ibkr-group ibkr-web-api" aria-label="IBKR Web API setup">
      <legend>Web API setup</legend>
      <ol className="ibkr-steps">
        <li>
          <strong>Check your account.</strong>
          <p>
            The Web API needs an IBKR Pro account. IBKR may also need to turn on OAuth access for you: if the page in step 3
            does not let you register a consumer key, email <a href={`mailto:${API_ACCESS_EMAIL}`}>{API_ACCESS_EMAIL}</a> and
            ask for first-party OAuth access for your account.
          </p>
        </li>
        <li>
          <strong>Generate keys.</strong>
          <p>{PRODUCT_NAME} creates a signing key, an encryption key and Diffie-Hellman parameters, and keeps the private halves in {webApi?.storage ?? 'its protected store'}.</p>
          <div className="ibkr-step-actions">
            <button type="button" className={webApi?.keys_generated ? 'settings-secondary-button' : 'settings-primary-button'} disabled={busy || !editable} onClick={() => void generate()}>
              {webApi?.keys_generated ? 'Replace keys' : 'Generate keys'}
            </button>
            {webApi?.keys_generated ? KEY_FILES.map((file) => (
              <button key={file.key} type="button" className="settings-secondary-button" disabled={busy} onClick={() => void downloadFile(file.filename, file.key)}>
                Download {file.filename}
              </button>
            )) : null}
          </div>
        </li>
        <li>
          <strong>Register them with IBKR.</strong>
          <p>
            Open <a href={webApi?.portal_url} target="_blank" rel="noreferrer">IBKR&apos;s OAuth page</a> and log in. Choose a consumer
            key of 9 letters and click Save Key, then upload:
          </p>
          <ul>
            {KEY_FILES.map((file) => <li key={file.key}><code>{file.filename}</code> as {file.field}</li>)}
          </ul>
          <p>Click Generate Token and copy the Access Token and Access Token Secret (IBKR shows them once), then switch on Enable OAuth Access.</p>
        </li>
        <li>
          <strong>Paste what IBKR gave you.</strong>
          <div className="ibkr-web-api-fields">
            <SettingsField label="Consumer key" help={webApi?.consumer_key ? `Saved: ${webApi.consumer_key}` : '9 letters, as chosen on IBKR’s page.'}>
              <input aria-label="IBKR consumer key" value={consumerKey} maxLength={9} autoComplete="off" disabled={busy || !editable} onChange={(event) => setConsumerKey(event.currentTarget.value.toUpperCase())} />
            </SettingsField>
            <SettingsField label="Access token" help={webApi?.access_token_masked ? `Saved: ${webApi.access_token_masked}` : undefined}>
              <input aria-label="IBKR access token" type="password" value={accessToken} autoComplete="off" disabled={busy || !editable} onChange={(event) => setAccessToken(event.currentTarget.value)} />
            </SettingsField>
            <SettingsField label="Access token secret" help={webApi?.access_token_secret_saved ? 'Saved (hidden).' : undefined}>
              <input aria-label="IBKR access token secret" type="password" value={accessTokenSecret} autoComplete="off" disabled={busy || !editable} onChange={(event) => setAccessTokenSecret(event.currentTarget.value)} />
            </SettingsField>
          </div>
          <div className="ibkr-step-actions">
            <button type="button" className="settings-primary-button" disabled={busy || !editable || !(consumerKey.trim() || accessToken.trim() || accessTokenSecret.trim())} onClick={() => void saveTokens()}>
              Save IBKR tokens
            </button>
          </div>
        </li>
        <li>
          <strong>Connect.</strong>
          <p>
            A new consumer key starts working after IBKR&apos;s nightly reset (midnight in New York); some users have waited until the
            weekend restart. If you are also logged in to TWS or the IBKR mobile app with the same username, IBKR keeps live data
            on that session: {PRODUCT_NAME} never logs it out, so use a second IBKR username for the API.
          </p>
          <div className="ibkr-step-actions">
            <button type="button" className="settings-primary-button" disabled={busy || !webApi?.ready || !savedForWebApi} onClick={() => void connect()}>
              Connect to IBKR
            </button>
            {!savedForWebApi ? <span className="ibkr-step-hint">Enable IBKR with the Web API connection and save first.</span> : null}
          </div>
        </li>
      </ol>
      {!editable && webApi ? <p className="ibkr-status-error">The protected credential store is unavailable, so IBKR keys cannot be saved here.</p> : null}
      <p className="settings-inline-status" role="status">{message}</p>
    </fieldset>
  );
}
