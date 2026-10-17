import { defineModule } from '../../app/moduleManifest';

/** The workspace module this feature provides (WP-9.7). */
export const storytellerModule = defineModule({
  id: 'storyteller',
  label: 'Storyteller',
  summary: 'Long-form story generation, outlines, branches, and exports.',
  route: '/storyteller',
  icon: '✍',
  backendModules: ['story'],
  apiPrefixes: [
    '/api/assets', '/api/jobs', '/api/providers', '/api/settings', '/api/tts', '/api/voice', '/api/agent',
    '/api/prompts',
  ],
  modeLabel: 'Storyteller',
  loadWorkspace: () => import('./StorytellerWorkspace').then((module) => module.StorytellerWorkspace),
});
