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
}));

vi.mock('./tradingMarketDataApi', () => ({ tradingMarketDataApi: mockedApi }));

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
  },
  settings_source: 'defaults' as const,
  connection_status: 'disabled' as const,
  official_ibapi_available: true,
  connected: false,
  last_error: null,
  diagnostics: {},
};

describe('TradingMarketDataSettings', () => {
  beforeEach(() => {
    mockedApi.coinmarketcapCredentials.mockReset();
    mockedApi.saveCoinMarketCapCredentials.mockReset();
    mockedApi.ibkrSettings.mockReset();
    mockedApi.saveIbkrSettings.mockReset();
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
