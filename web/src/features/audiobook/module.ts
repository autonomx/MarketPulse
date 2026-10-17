import { defineModule } from '../../app/moduleManifest';

/** The workspace module this feature provides (WP-9.7). */
export const audiobookModule = defineModule({
  id: 'audiobook',
  label: 'Audiobook',
  summary: 'Source-faithful narration, voice casting, chapter rendering, and book exports.',
  route: '/audiobook',
  icon: 'AB',
  backendModules: ['audiobook'],
  apiPrefixes: ['/api/audiobook', '/api/jobs', '/api/providers', '/api/voice', '/api/tts'],
  modeLabel: 'Audiobook',
  loadWorkspace: () => import('./AudiobookWorkspace').then((module) => module.AudiobookWorkspace),
});
