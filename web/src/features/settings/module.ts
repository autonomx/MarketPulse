import { defineModule } from '../../app/moduleManifest';

/** The workspace module this feature provides (WP-9.7). */
export const settingsModule = defineModule({
  id: 'settings',
  label: 'Settings',
  summary: 'Global app, provider, model, local service, and feature settings.',
  route: '/settings',
  icon: '⚙',
  backendModules: [],
  usesOperations: [
    'GET /api/assistant/research/credentials',
    'POST /api/assistant/research/credentials',
    'GET /api/assistant/research/status',
    'GET /api/hermes/status',
    'GET /api/trading/market-data/providers/coinmarketcap/credentials',
    'PUT /api/trading/market-data/providers/coinmarketcap/credentials',
    'GET /api/trading/market-data/providers/fred/credentials',
    'PUT /api/trading/market-data/providers/fred/credentials',
    'GET /api/trading/market-data/providers/ibkr/settings',
    'PUT /api/trading/market-data/providers/ibkr/settings',
    'GET /api/trading/execution/providers/alpaca-iex/credentials',
    'PUT /api/trading/execution/providers/alpaca-iex/credentials',
  ],
  apiPrefixes: [
    '/api/settings', '/api/providers', '/api/models', '/api/runtime', '/api/assistant', '/api/hermes',
    '/api/diagnostics', '/api/workers', '/api/trading/market-data', '/api/trading/execution/providers/alpaca-iex/credentials',
  ],
  platform: true,
  loadWorkspace: () => import('./SettingsWorkspace').then((module) => module.SettingsWorkspace),
});
