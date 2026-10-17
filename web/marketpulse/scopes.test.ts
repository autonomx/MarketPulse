import { afterEach, describe, expect, it } from 'vitest';
import { configureViewModules, isApiAllowedForView, moduleIdFromPathname } from '../src/app/viewApiScope';
import { moduleManifests } from '../src/app/modules';
import { marketPulseScopes } from './scopes';

afterEach(() => configureViewModules(moduleManifests));

describe('MarketPulse firewall scopes', () => {
  it('knows trading, settings and the first-run page, and nothing else', () => {
    configureViewModules(marketPulseScopes);

    expect(moduleIdFromPathname('/trading')).toBe('trading');
    expect(moduleIdFromPathname('/settings')).toBe('settings');
    expect(moduleIdFromPathname('/welcome')).toBe('welcome');
    expect(moduleIdFromPathname('/chatbot')).toBeNull();
    expect(moduleIdFromPathname('/rpg')).toBeNull();
  });

  it('lets each page reach only its API families', () => {
    configureViewModules(marketPulseScopes);

    expect(isApiAllowedForView('/api/trading/bars', 'trading')).toBe(true);
    expect(isApiAllowedForView('/api/providers', 'trading')).toBe(false);
    expect(isApiAllowedForView('/api/providers', 'settings')).toBe(true);
    expect(isApiAllowedForView('/api/trading/market-data/providers/fred/credentials', 'settings' as never)).toBe(true);
    expect(isApiAllowedForView('/api/trading/prospective-gap/github-credentials', 'settings' as never)).toBe(true);
    expect(isApiAllowedForView('/api/trading/bars', 'settings' as never)).toBe(false);
    expect(isApiAllowedForView('/api/rpg/turns', 'settings' as never)).toBe(false);
    expect(isApiAllowedForView('/api/auth/password', 'welcome' as never)).toBe(true);
    expect(isApiAllowedForView('/api/providers', 'welcome' as never)).toBe(false);
  });
});
