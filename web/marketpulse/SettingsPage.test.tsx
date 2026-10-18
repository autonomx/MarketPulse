import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

// The sections are Omnix's and MarketPulse's own components with their own tests; here they are stand-ins.
vi.mock('../src/features/settings/AccountSection', () => ({ AccountSection: () => <p>account section</p> }));
vi.mock('../src/features/settings/SignedInSessionsSection', () => ({ SignedInSessionsSection: () => <p>sessions section</p> }));
vi.mock('../src/features/settings/TradingMarketDataSettings', () => ({ TradingMarketDataSettings: () => <p>market data section</p> }));
vi.mock('../src/features/settings/AiProvidersSettings', () => ({ AiProvidersSettings: () => <p>ai providers section</p> }));
vi.mock('./AppearanceSettings', () => ({ AppearanceSettings: () => <p>appearance section</p> }));
vi.mock('./ResearchHandoffSettings', () => ({ ResearchHandoffSettings: () => <p>research section</p> }));
vi.mock('../src/features/settings/SettingsProfileProvider', () => ({ SettingsProfileProvider: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('../src/features/settings/SettingsProfileContext', () => ({
  useSettingsProfileContext: () => ({ state: { dirtyPaths: [], status: 'idle', message: null }, dispatch: vi.fn() }),
}));

const { SettingsPage } = await import('./SettingsPage');

function renderAt(url: string) {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const settingsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: 'settings',
    validateSearch: (search: Record<string, unknown>): { category?: string } => (typeof search.category === 'string' ? { category: search.category } : {}),
    component: SettingsPage,
  });
  const tradingRoute = createRoute({ getParentRoute: () => rootRoute, path: 'trading', component: () => <p>charts</p> });
  const router = createRouter({ routeTree: rootRoute.addChildren([settingsRoute, tradingRoute]), history: createMemoryHistory({ initialEntries: [url] }) });
  render(<RouterProvider router={router} />);
  return router;
}

afterEach(() => cleanup());

describe('MarketPulse settings page', () => {
  it('opens on Appearance and switches category from the sidebar', async () => {
    const router = renderAt('/settings');

    expect(await screen.findByRole('heading', { level: 2, name: 'Appearance' })).toBeInTheDocument();
    expect(screen.getByText('appearance section')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Appearance' })).toHaveAttribute('aria-current', 'page');

    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Market data & brokers' })));

    expect(await screen.findByRole('heading', { level: 2, name: 'Market data & brokers' })).toBeInTheDocument();
    expect(screen.getByText('market data section')).toBeInTheDocument();
    expect(screen.queryByText('appearance section')).toBeNull();
    expect(router.state.location.search).toEqual({ category: 'trading-market-data' });
  });

  it('opens the category a link names, and falls back to the first for an unknown one', async () => {
    renderAt('/settings?category=research');
    expect(await screen.findByText('research section')).toBeInTheDocument();
    cleanup();

    renderAt('/settings?category=nope');
    expect(await screen.findByText('appearance section')).toBeInTheDocument();
  });

  it('shows the account and its sessions together, links back to the charts, and hides the save bar with nothing to save', async () => {
    renderAt('/settings?category=account');

    expect(await screen.findByText('account section')).toBeInTheDocument();
    expect(screen.getByText('sessions section')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to charts' })).toHaveAttribute('href', '/trading');
    expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull();
  });
});
