import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE = 'Windows DPAPI user store';
const missing = {
  provider: 'alpaca_iex', configured: false, api_key_id_masked: '', api_key_source: 'missing', secret_key_source: 'missing',
  api_key_editable: true, secret_key_editable: true, storage: STORAGE,
};
const stored = { ...missing, configured: true, api_key_id_masked: '***WXYZ', api_key_source: 'os_protected_store', secret_key_source: 'os_protected_store' };

const tradingMarketDataApi = vi.hoisted(() => ({
  alpacaCredentials: vi.fn(),
  saveAlpacaCredentials: vi.fn(),
}));
vi.mock('./tradingMarketDataApi', () => ({ tradingMarketDataApi }));

const { AlpacaKeySettings } = await import('./AlpacaKeySettings');

describe('Alpaca key settings', () => {
  beforeEach(() => {
    tradingMarketDataApi.alpacaCredentials.mockReset();
    tradingMarketDataApi.saveAlpacaCredentials.mockReset();
  });

  it('saves both keys without ever showing them back', async () => {
    tradingMarketDataApi.alpacaCredentials.mockResolvedValue(missing);
    tradingMarketDataApi.saveAlpacaCredentials.mockResolvedValue(stored);
    render(<AlpacaKeySettings />);

    expect(await screen.findByText(/Add Alpaca API keys/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Alpaca keys' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Clear stored Alpaca keys' })).toBeNull();

    fireEvent.change(screen.getByLabelText('Alpaca API key ID'), { target: { value: ' PKTESTKEYWXYZ ' } });
    fireEvent.change(screen.getByLabelText('Alpaca secret key'), { target: { value: 'fake-secret' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Save Alpaca keys' })));

    expect(tradingMarketDataApi.saveAlpacaCredentials).toHaveBeenCalledWith({ api_key_id: 'PKTESTKEYWXYZ', secret_key: 'fake-secret' });
    expect(screen.getByLabelText('Alpaca API key ID')).toHaveValue('');
    expect(screen.getByLabelText('Alpaca secret key')).toHaveValue('');
    expect(screen.getByLabelText('Alpaca API key ID')).toHaveAttribute('placeholder', '***WXYZ');
    expect(screen.getByRole('status')).toHaveTextContent('Alpaca keys saved.');
    expect(screen.getByText(/Currently stored in the Windows DPAPI user store\. Environment override: OMNIX_ALPACA_API_KEY_ID/)).toBeInTheDocument();
  });

  it('updates only the field that was filled in', async () => {
    tradingMarketDataApi.alpacaCredentials.mockResolvedValue(stored);
    tradingMarketDataApi.saveAlpacaCredentials.mockResolvedValue(stored);
    render(<AlpacaKeySettings />);
    expect(await screen.findByText('Alpaca is connected (key ***WXYZ).')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Alpaca secret key'), { target: { value: 'rotated-secret' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Save Alpaca keys' })));

    expect(tradingMarketDataApi.saveAlpacaCredentials).toHaveBeenCalledWith({ secret_key: 'rotated-secret' });
  });

  it('clears stored keys, and says when the environment still supplies them', async () => {
    tradingMarketDataApi.alpacaCredentials.mockResolvedValue(stored);
    tradingMarketDataApi.saveAlpacaCredentials.mockResolvedValue({ ...stored, api_key_source: 'environment', secret_key_source: 'environment', api_key_editable: false, secret_key_editable: false });
    render(<AlpacaKeySettings />);
    await screen.findByText('Alpaca is connected (key ***WXYZ).');

    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Clear stored Alpaca keys' })));

    expect(tradingMarketDataApi.saveAlpacaCredentials).toHaveBeenCalledWith({ clear_api_key_id: true, clear_secret_key: true });
    expect(screen.getByRole('status')).toHaveTextContent('Stored keys cleared; keys from the environment are still in use.');
    expect(screen.getByLabelText('Alpaca API key ID')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Clear stored Alpaca keys' })).toBeNull();
  });

  it('asks for the missing half of a key pair', async () => {
    tradingMarketDataApi.alpacaCredentials.mockResolvedValue({ ...missing, api_key_source: 'os_protected_store', api_key_id_masked: '***WXYZ' });
    render(<AlpacaKeySettings />);

    expect(await screen.findByText('Both the API key ID and the secret key are needed.')).toBeInTheDocument();
  });
});
