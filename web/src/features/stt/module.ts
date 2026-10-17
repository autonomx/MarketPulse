import { defineModule } from '../../app/moduleManifest';

/** The workspace module this feature provides (WP-9.7). */
export const sttModule = defineModule({
  id: 'stt',
  label: 'STT',
  summary: 'Speech-to-text transcription, alignment, transcript assets, and diagnostics.',
  route: '/stt',
  icon: '⌁',
  backendModules: [],
  apiPrefixes: [
    '/api/assets', '/api/jobs', '/api/providers', '/api/settings', '/api/voice', '/api/tts', '/api/agent',
    '/api/prompts',
  ],
  loadWorkspace: () => import('./SttWorkspace').then((module) => module.SttWorkspace),
});
