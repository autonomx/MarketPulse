import { defineModule } from '../../app/moduleManifest';

/** The workspace module this feature provides (WP-9.7). */
export const voiceCloningModule = defineModule({
  id: 'voice-cloning',
  label: 'Voice Cloning',
  summary: 'Voice profile creation, sample ingestion, previews, and profile metadata.',
  route: '/voice-cloning',
  icon: '◎',
  backendModules: [],
  apiPrefixes: [
    '/api/voice-cloning', '/api/voice-library', '/api/assets', '/api/jobs', '/api/providers', '/api/settings',
    '/api/tts', '/api/agent', '/api/prompts',
  ],
  sidebar: false,
  loadWorkspace: () => import('./VoiceCloningWorkspace').then((module) => module.VoiceCloningWorkspace),
});
