import { defineModule } from '../../app/moduleManifest';

/** The workspace module this feature provides (WP-9.7). */
export const tradingModule = defineModule({
  id: 'trading',
  label: 'Trading',
  summary: 'Multi-chart crypto and stock research, drawings, indicators, alerts, replay, backtests, and paper simulation.',
  route: '/trading',
  icon: '⌁',
  backendModules: ['trading'],
  apiPrefixes: ['/api/trading'],
  modeLabel: 'Trading',
  loadWorkspace: () => import('./TradingWorkspace').then((module) => module.TradingWorkspace),
});
