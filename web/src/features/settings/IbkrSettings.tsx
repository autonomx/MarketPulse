import { useEffect, useState, type ReactNode } from 'react';
import { PRODUCT_NAME } from '../../shared/productIdentity';
import { IbkrWebApiSetup } from './IbkrWebApiSetup';
import { SettingsField, SettingsSection } from './SettingsPrimitives';
import { tradingMarketDataApi, type IbkrConnection, type IbkrSettings, type IbkrSettingsStatus } from './tradingMarketDataApi';
import './IbkrSettings.css';

type Tone = 'ready' | 'warning' | 'error' | 'idle';

function ibkrSourceLabel(source: IbkrSettingsStatus['settings_source']): string {
  if (source === 'omnix_settings') return `${PRODUCT_NAME} settings`;
  if (source === 'environment') return 'Legacy environment variables';
  if (source === 'runtime_arguments') return 'Runtime arguments';
  return `${PRODUCT_NAME} defaults`;
}

function ibkrConnectionLabel(status: IbkrSettingsStatus['connection_status'], connection: IbkrConnection): string {
  const webApi = connection === 'web_api';
  if (status === 'connected') return webApi ? 'Connected to the IBKR Web API' : 'Connected to IB Gateway';
  if (status === 'client_unavailable') return 'Official ibapi package missing';
  if (status === 'credentials_missing') return 'Web API setup not finished';
  if (status === 'disabled') return 'Disabled';
  return webApi ? 'Web API not connected' : 'Gateway not connected';
}

function ibkrTone(status: IbkrSettingsStatus['connection_status'] | undefined): Tone {
  if (status === 'connected') return 'ready';
  if (status === 'client_unavailable') return 'error';
  if (status === 'disabled' || status === undefined) return 'idle';
  return 'warning';
}

const DEFAULT_IBKR_FORM: IbkrSettings = {
  enabled: false,
  monitor_enabled: true,
  host: '127.0.0.1',
  port: 4002,
  client_id: 71,
  live_authority_enabled: false,
  recovery_authority_enabled: false,
  connection: 'gateway',
};

const CONNECTION_CHOICES: ReadonlyArray<{ value: IbkrConnection; label: string; help: string }> = [
  { value: 'gateway', label: 'IB Gateway', help: 'IBKR’s desktop app runs on this computer and holds the login.' },
  { value: 'web_api', label: 'IBKR Web API', help: 'No app to run. Needs an IBKR Pro account and a one-time key setup.' },
];

/** A setting turned on or off: what it does beside the switch, not under it. */
function IbkrToggle({ label, ariaLabel, help, checked, disabled, onChange }: {
  label: string;
  ariaLabel: string;
  help: ReactNode;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="ibkr-toggle">
      <span className="ibkr-toggle-text">
        <strong>{label}</strong>
        <small>{help}</small>
      </span>
      <input
        aria-label={ariaLabel}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.checked)}
      />
    </label>
  );
}

/** IB Gateway or the Web API: two cards, the chosen one outlined. */
function IbkrConnectionChoice({ value, disabled, onChange }: {
  value: IbkrConnection;
  disabled: boolean;
  onChange: (connection: IbkrConnection) => void;
}) {
  return (
    <div className="ibkr-connection-choice" role="radiogroup" aria-label="How to reach IBKR">
      {CONNECTION_CHOICES.map((choice) => (
        <label key={choice.value} className={`ibkr-choice${value === choice.value ? ' is-selected' : ''}`}>
          <input
            type="radio"
            name="ibkr-connection"
            value={choice.value}
            checked={value === choice.value}
            disabled={disabled}
            onChange={() => onChange(choice.value)}
          />
          <span className="ibkr-toggle-text">
            <strong>{choice.label}</strong>
            <small>{choice.help}</small>
          </span>
        </label>
      ))}
    </div>
  );
}

/** Interactive Brokers: how Omnix reaches IBKR (Gateway or Web API) and how much authority its market data has. */
export function IbkrSettingsSection() {
  const [status, setStatus] = useState<IbkrSettingsStatus>();
  const [form, setForm] = useState<IbkrSettings>(DEFAULT_IBKR_FORM);
  const [busy, setBusy] = useState(false);
  // Save results and errors; the connection itself is shown in the status summary.
  const [message, setMessage] = useState('');

  useEffect(() => {
    let active = true;
    tradingMarketDataApi.ibkrSettings().then((next) => {
      if (!active) return;
      setStatus(next);
      setForm(next.settings);
    }).catch((error: unknown) => {
      if (active) setMessage(error instanceof Error ? error.message : 'IBKR connection status is unavailable.');
    });
    return () => { active = false; };
  }, []);

  const update = (patch: Partial<IbkrSettings>) => setForm((current) => ({ ...current, ...patch }));

  const save = async () => {
    setBusy(true);
    try {
      const next = await tradingMarketDataApi.saveIbkrSettings(form);
      setStatus(next);
      setForm(next.settings);
      setMessage(`Saved: ${ibkrConnectionLabel(next.connection_status, next.settings.connection)}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'IBKR settings update failed.');
    } finally {
      setBusy(false);
    }
  };

  const tone = ibkrTone(status?.connection_status);
  const savedConnection = status?.settings.connection ?? 'gateway';
  return (
    <SettingsSection
      title="Interactive Brokers (IBKR)"
      description={`Optional. Live stock charts and chart checks from IBKR market data, through IB Gateway on this computer or IBKR's Web API. ${PRODUCT_NAME} never stores your IBKR username or password.`}
      scope="global"
      className="ibkr-settings"
    >
      <div className="ibkr-status" role="group" aria-label="IBKR status">
        <div className="ibkr-status-head">
          <span className={`settings-status-value tone-${tone}`}>
            <i aria-hidden="true" />
            {status ? ibkrConnectionLabel(status.connection_status, savedConnection) : 'Checking IBKR...'}
          </span>
          <span className="ibkr-status-source">Settings from {status ? ibkrSourceLabel(status.settings_source) : '…'}</span>
        </div>
        <dl className="ibkr-status-facts">
          {savedConnection === 'web_api'
            ? <div><dt>Web API keys</dt><dd>{status?.web_api.ready ? 'Set up' : status?.web_api.keys_generated ? 'Tokens missing' : 'Not generated'}</dd></div>
            : <div><dt>Python API</dt><dd>{status?.official_ibapi_available ? 'Installed' : 'Not installed'}</dd></div>}
          <div><dt>Live data</dt><dd>{status?.settings.live_authority_enabled ? 'Authoritative' : 'Observation only'}</dd></div>
          <div><dt>History repair</dt><dd>{status?.settings.recovery_authority_enabled ? 'Allowed' : 'Off'}</dd></div>
          <div><dt>Orders</dt><dd>Never placed</dd></div>
        </dl>
        {status?.last_error ? <p className="ibkr-status-error">Last error: {status.last_error}</p> : null}
      </div>

      <fieldset className="ibkr-group">
        <legend>Connection</legend>
        <IbkrToggle
          label="Enable IBKR"
          ariaLabel="Enable IBKR"
          help={`${PRODUCT_NAME} connects to IBKR only when this is enabled.`}
          checked={form.enabled}
          disabled={busy}
          onChange={(enabled) => update({ enabled })}
        />
        <IbkrConnectionChoice value={form.connection} disabled={busy} onChange={(connection) => update({ connection })} />
        {form.connection === 'gateway' ? <div className="ibkr-connection-fields">
          <SettingsField label="Gateway host">
            <input aria-label="Gateway host" value={form.host} disabled={busy} onChange={(event) => update({ host: event.currentTarget.value })} />
          </SettingsField>
          <SettingsField label="Socket port" help="Paper 4002, live 4001.">
            <input
              aria-label="Gateway socket port"
              type="number"
              min="1"
              max="65535"
              value={form.port}
              disabled={busy}
              onChange={(event) => update({ port: Number(event.currentTarget.value) })}
            />
          </SettingsField>
          <SettingsField label="Client ID" help="Unique to this connection.">
            <input
              aria-label="IBKR client ID"
              type="number"
              min="0"
              max="32767"
              value={form.client_id}
              disabled={busy}
              onChange={(event) => update({ client_id: Number(event.currentTarget.value) })}
            />
          </SettingsField>
        </div> : null}
      </fieldset>

      {form.connection === 'web_api' ? (
        // The walkthrough refreshes the status; unsaved edits in the form above stay as they are.
        <IbkrWebApiSetup status={status} onStatus={setStatus} />
      ) : null}

      <fieldset className="ibkr-group">
        <legend>Market data</legend>
        <IbkrToggle
          label="Start market-data monitor"
          ariaLabel="Start IBKR market-data monitor"
          help="Keeps the zero-authority observation stream reconciled for active strategy demand."
          checked={form.monitor_enabled}
          disabled={busy}
          onChange={(monitor_enabled) => update({ monitor_enabled })}
        />
        <IbkrToggle
          label="Allow live-data authority"
          ariaLabel="Allow IBKR live-data authority"
          help="Requires fresh, complete, live-entitled quotes. This never grants order execution authority."
          checked={form.live_authority_enabled}
          disabled={busy}
          onChange={(live_authority_enabled) => update({ live_authority_enabled })}
        />
        <IbkrToggle
          label="Allow historical recovery authority"
          ariaLabel="Allow IBKR historical recovery authority"
          help="Allows IBKR exact-range history into canonical gap repair after your soak review."
          checked={form.recovery_authority_enabled}
          disabled={busy}
          onChange={(recovery_authority_enabled) => update({ recovery_authority_enabled })}
        />
      </fieldset>

      <div className="ibkr-actions">
        <p className="settings-inline-status" role="status">{message}</p>
        <button type="button" className="settings-primary-button" disabled={busy} onClick={() => void save()}>
          {busy ? 'Saving...' : 'Save IBKR settings'}
        </button>
      </div>
      <p className="ibkr-note">
        Live-data and recovery authority stay off by default. {PRODUCT_NAME} never stores your IBKR username or password; Web API keys
        and tokens stay in {status?.web_api.storage ?? 'the protected credential store'}.
      </p>
    </SettingsSection>
  );
}
