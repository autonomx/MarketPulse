import { defineModule } from '../../app/moduleManifest';

/** The workspace module this feature provides (WP-9.7). */
export const podcastModule = defineModule({
  id: 'podcast',
  label: 'Podcast',
  summary: 'Script planning, multi-speaker synthesis, mixing, and podcast exports.',
  route: '/podcast',
  icon: '◉',
  backendModules: [],
  apiPrefixes: [
    '/api/assets', '/api/jobs', '/api/providers', '/api/settings', '/api/tts', '/api/voice', '/api/agent',
    '/api/prompts',
  ],
  modeLabel: 'Podcast',
  loadWorkspace: () => import('./PodcastWorkspace').then((module) => module.PodcastWorkspace),
});
