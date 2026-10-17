import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const tradingMarketDataApi = vi.hoisted(() => ({
  fredCredentials: vi.fn(async () => ({ provider: 'fred', configured: false, api_key_masked: '', api_key_source: 'missing', api_key_editable: true, storage: 'Windows DPAPI user store' })),
  saveFredCredentials: vi.fn(async () => ({ provider: 'fred', configured: true, api_key_masked: '***3456', api_key_source: 'os_protected_store', api_key_editable: true, storage: 'Windows DPAPI user store' })),
}));
vi.mock('./tradingMarketDataApi', () => ({ tradingMarketDataApi }));

const { FredKeySettings } = await import('./FredKeySettings');

describe('FRED key settings (TVP-10.5)', () => {
  it('saves a key without ever showing it back', async () => {
    render(<FredKeySettings />);
    expect(await screen.findByText('Add a free FRED API key to show the economic calendar.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('FRED API key'), { target: { value: 'fake-fred-key-1234' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Save FRED key' })));
    expect(tradingMarketDataApi.saveFredCredentials).toHaveBeenCalledWith({ api_key: 'fake-fred-key-1234' });
    expect(screen.getByLabelText('FRED API key')).toHaveValue('');
    expect(screen.getByLabelText('FRED API key')).toHaveAttribute('placeholder', '***3456');
    expect(screen.getByRole('button', { name: 'Clear stored FRED key' })).toBeInTheDocument();
  });
});
