import { defineModule } from '../../app/moduleManifest';

/** The workspace module this feature provides (WP-9.7). */
export const rpgModule = defineModule({
  id: 'rpg',
  label: 'RPG',
  summary: 'Deterministic AI role-playing engine, turn contracts, journal, party, combat, and reports.',
  route: '/rpg',
  icon: '✦',
  backendModules: ['hermes', 'rpg'],
  apiPrefixes: [
    '/api/rpg', '/api/assets', '/api/jobs', '/api/reports', '/api/replay', '/api/hermes', '/api/agent',
    '/api/prompts',
  ],
  modeLabel: 'RPG',
  loadWorkspace: () => import('./RpgWorkspace').then((module) => module.RpgWorkspace),
  activateRuntime: async (_context, store) => {
    store.add((await import('./rpgTurnUiStore')).installRpgTurnUiFetchInterceptor());
  },
});
