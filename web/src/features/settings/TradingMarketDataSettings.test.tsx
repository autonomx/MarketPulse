import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TradingMarketDataSettings } from './TradingMarketDataSettings';

const mockedApi = vi.hoisted(() => ({
  coinmarketcapCredentials: vi.fn(),
  saveCoinMarketCapCredentials: vi.fn(),
  ibkrSettings: vi.fn(),
  saveIbkrSettings: vi.fn(),
  // The FRED section (TVP-10.5) loads its own status.
  fredCredentials: vi.fn(async () => ({ provider: 'fred', configured: false, api_key_masked: '', api_key_source: 'missing', api_key_editable: true, storage: 'Windows DPAPI user store' })),
  saveFredCredentials: vi.fn(),
  // So does the Alpaca section.
  alpacaCredentials: vi.fn(async () => ({ provider: 'alpaca_iex', configured: false, api_key_id_masked: '', api_key_source: 'missing', secret_key_source: 'missing', api_key_editable: true, secret_key_editable: true, storage: 'Windows DPAPI user store' })),
  saveAlpacaCredentials: vi.fn(),
  connectIbkr: vi.fn(),
  ibkrWebApiKeys: vi.fn(),
  generateIbkrWebApiKeys: vi.fn(),
  saveIbkrWebApiCredentials: vi.fn(),
}));

const downloads = vi.hoisted(() => ({ downloadBlob: vi.fn() }));

vi.mock('./tradingMarketDataApi', () => ({ tradingMarketDataApi: mockedApi }));
vi.mock('../../shared/download', () => downloads);

const configured = {
  provider: 'coinmarketcap' as const,
  configured: true,
  api_key_masked: '***5678',
  api_key_source: 'os_protected_store' as const,
  api_key_editable: true,
  storage: 'Windows DPAPI user store',
};

const ibkrDisconnected = {
  provider: 'ibkr' as const,
  settings: {
    enabled: false,
    monitor_enabled: true,
    host: '127.0.0.1',
    port: 4002,
    client_id: 71,
    live_authority_enabled: false,
    recovery_authority_enabled: false,
    connection: 'gateway' as const,
  },
  settings_source: 'defaults' as const,
  connection_status: 'disabled' as const,
  official_ibapi_available: true,
  connected: false,
  last_error: null,
  web_api: {
    keys_generated: false,
    consumer_key: '',
    access_token_masked: '',
    access_token_secret_saved: false,
    ready: false,
    editable: true,
    storage: 'Windows DPAPI user store',
    portal_url: 'https://ndcdyn.interactivebrokers.com/sso/Login?action=OAUTH&RL=1&ip2loc=US',
  },
  diagnostics: {},
};

describe('TradingMarketDataSettings', () => {
  beforeEach(() => {
    mockedApi.coinmarketcapCredentials.mockReset();
    mockedApi.saveCoinMarketCapCredentials.mockReset();
    mockedApi.ibkrSettings.mockReset();
    mockedApi.saveIbkrSettings.mockReset();
    mockedApi.connectIbkr.mockReset();
    mockedApi.ibkrWebApiKeys.mockReset();
    mockedApi.generateIbkrWebApiKeys.mockReset();
    mockedApi.saveIbkrWebApiCredentials.mockReset();
    downloads.downloadBlob.mockReset();
  });

  it('loads masked status and saves a new key without rendering the secret', async () => {
    mockedApi.coinmarketcapCredentials.mockResolvedValue(configured);
    mockedApi.saveCoinMarketCapCredentials.mockResolvedValue(configured);
    mockedApi.ibkrSettings.mockResolvedValue(ibkrDisconnected);
    mockedApi.saveIbkrSettings.mockResolvedValue(ibkrDisconnected);
    render(<TradingMarketDataSettings />);

    expect(await screen.findByText('CoinMarketCap market-cap data is configured.')).toBeVisible();
    expect(screen.getByPlaceholderText('***5678')).toBeVisible();

    fireEvent.change(screen.getByLabelText('CoinMarketCap API key'), { target: { value: 'new-secret-key' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));

    expect(await screen.findByText('CoinMarketCap API key saved in the OS-protected store.')).toBeVisible();
    expect(mockedApi.saveCoinMarketCapCredentials).toHaveBeenCalledWith({ api_key: 'new-secret-key' });
    expect(screen.queryByDisplayValue('new-secret-key')).not.toBeInTheDocument();
  });

  it('captures the IBKR enable checkbox before saving settings', async () => {
    mockedApi.coinmarketcapCredentials.mockResolvedValue(configured);
    mockedApi.ibkrSettings.mockResolvedValue(ibkrDisconnected);
    mockedApi.saveIbkrSettings.mockResolvedValue({
      ...ibkrDisconnected,
      settings: { ...ibkrDisconnected.settings, enabled: true },
      connection_status: 'client_unavailable',
    });
    render(<TradingMarketDataSettings />);

    const enableIbkr = await screen.findByRole('checkbox', { name: 'Enable IBKR' });
    fireEvent.click(enableIbkr);
    fireEvent.click(screen.getByRole('button', { name: 'Save IBKR settings' }));

    expect(mockedApi.saveIbkrSettings).toHaveBeenCalledWith({
      ...ibkrDisconnected.settings,
      enabled: true,
    });
    await waitFor(() => {
      expect(screen.getAllByRole('status').some((status) => status.textContent?.includes('Official ibapi package missing'))).toBe(true);
    });
  });
});

describe('IBKR Web API setup', () => {
  const publicFiles = {
    signature_public_key: '-----BEGIN PUBLIC KEY-----\nsign\n-----END PUBLIC KEY-----\n',
    encryption_public_key: '-----BEGIN PUBLIC KEY-----\nenc\n-----END PUBLIC KEY-----\n',
    dh_parameters: '-----BEGIN DH PARAMETERS-----\ndh\n-----END DH PARAMETERS-----\n',
  };
  const webApiSaved = {
    ...ibkrDisconnected,
    settings: { ...ibkrDisconnected.settings, enabled: true, connection: 'web_api' as const },
    connection_status: 'credentials_missing' as const,
  };

  beforeEach(() => {
    mockedApi.coinmarketcapCredentials.mockResolvedValue(configured);
  });

  it('walks through generating keys and saving the tokens IBKR issues', async () => {
    mockedApi.ibkrSettings.mockResolvedValue(ibkrDisconnected);
    render(<TradingMarketDataSettings />);

    fireEvent.click(await screen.findByRole('radio', { name: /IBKR Web API/ }));
    // The Gateway socket fields belong to the other connection.
    expect(screen.queryByLabelText('Gateway host')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: "IBKR's OAuth page" })).toHaveAttribute('href', ibkrDisconnected.web_api.portal_url);
    expect(screen.getByRole('button', { name: 'Connect to IBKR' })).toBeDisabled();

    mockedApi.generateIbkrWebApiKeys.mockResolvedValue(publicFiles);
    mockedApi.ibkrSettings.mockResolvedValue({ ...ibkrDisconnected, web_api: { ...ibkrDisconnected.web_api, keys_generated: true } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate keys' }));

    await waitFor(() => expect(downloads.downloadBlob).toHaveBeenCalledTimes(3));
    expect(mockedApi.generateIbkrWebApiKeys).toHaveBeenCalledWith(false);
    expect(downloads.downloadBlob.mock.calls.map((call) => call[1])).toEqual(['public_signature.pem', 'public_encryption.pem', 'dhparam.pem']);
    expect(await screen.findByRole('button', { name: 'Download dhparam.pem' })).toBeVisible();

    mockedApi.saveIbkrWebApiCredentials.mockResolvedValue({
      ...ibkrDisconnected,
      web_api: { ...ibkrDisconnected.web_api, keys_generated: true, ready: true, consumer_key: 'OMNIXTEST', access_token_masked: '***ef56', access_token_secret_saved: true },
    });
    fireEvent.change(screen.getByLabelText('IBKR consumer key'), { target: { value: 'omnixtest' } });
    fireEvent.change(screen.getByLabelText('IBKR access token'), { target: { value: 'a1b2c3d4ef56' } });
    fireEvent.change(screen.getByLabelText('IBKR access token secret'), { target: { value: 'c2VjcmV0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save IBKR tokens' }));

    await waitFor(() => expect(mockedApi.saveIbkrWebApiCredentials).toHaveBeenCalledWith({
      consumer_key: 'OMNIXTEST',
      access_token: 'a1b2c3d4ef56',
      access_token_secret: 'c2VjcmV0',
    }));
    expect(await screen.findByText('Saved: OMNIXTEST')).toBeVisible();
    // Secrets are not kept on the page once saved, and are never shown while typed.
    expect(screen.queryByDisplayValue('c2VjcmV0')).not.toBeInTheDocument();
    expect(screen.getByLabelText('IBKR access token')).toHaveAttribute('type', 'password');
    expect(screen.getByLabelText('IBKR access token secret')).toHaveAttribute('type', 'password');
    // Connecting needs the Web API connection to be saved, not just chosen.
    expect(screen.getByRole('button', { name: 'Connect to IBKR' })).toBeDisabled();
  });

  it('connects once the Web API connection is saved and set up', async () => {
    const ready = { ...webApiSaved, connection_status: 'disconnected' as const, web_api: { ...webApiSaved.web_api, keys_generated: true, ready: true } };
    mockedApi.ibkrSettings.mockResolvedValue(ready);
    mockedApi.connectIbkr.mockResolvedValue({ ...ready, connection_status: 'connected', connected: true });
    render(<TradingMarketDataSettings />);

    fireEvent.click(await screen.findByRole('button', { name: 'Connect to IBKR' }));

    expect(await screen.findByText(/Connected: .* is receiving IBKR data through the Web API\./)).toBeVisible();
    expect(screen.getAllByText('Connected to the IBKR Web API').length).toBeGreaterThan(0);
  });
});
