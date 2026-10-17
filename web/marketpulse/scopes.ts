import type { ViewModuleScope } from '../src/app/viewApiScope';
import { tradingModule } from '../src/features/trading/module';

/**
 * The API families MarketPulse's settings page may call. Omnix's settings module lists more (and would bundle its
 * whole workspace), so MarketPulse names its own: account and sign-in are always allowed, plus these.
 */
export const marketPulseSettingsScope: ViewModuleScope = {
  id: 'settings',
  route: '/settings',
  apiPrefixes: [
    '/api/settings', '/api/providers', '/api/models', '/api/runtime', '/api/hermes',
    '/api/trading/market-data', '/api/trading/prospective-gap/github-credentials',
  ],
};

/** The first-run page only talks to the sign-in API, which every page may call. */
export const marketPulseWelcomeScope: ViewModuleScope = { id: 'welcome', route: '/welcome', apiPrefixes: [] };

/** Every scope the MarketPulse firewall knows: trading, settings and the first-run page. */
export const marketPulseScopes: readonly ViewModuleScope[] = [tradingModule, marketPulseSettingsScope, marketPulseWelcomeScope];
