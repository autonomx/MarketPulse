import { defineModule } from '../../app/moduleManifest';

/** The workspace module this feature provides (WP-9.7). */
export const voiceModule = defineModule({
  id: 'voice',
  label: 'Voice Studio',
  summary: 'Text-to-speech generation, previews, playback, and voice provider diagnostics.',
  route: '/voice',
  icon: '◍',
  backendModules: ['voice'],
  apiPrefixes: [
    '/api/voice', '/api/voice-cloning', '/api/voice-library', '/api/assets', '/api/jobs', '/api/providers',
    '/api/settings', '/api/tts', '/api/agent', '/api/prompts',
  ],
  modeLabel: 'Voice Studio',
  loadWorkspace: () => import('./VoiceWorkspace').then((module) => module.VoiceWorkspace),
  activateRuntime: async (_context, store) => {
    store.add((await import('./voiceLibraryAssetFallback')).installVoiceLibraryAssetFallback());
    store.add((await import('./voiceLibraryFetchDiagnostics')).installVoiceLibraryFetchDiagnostics());
  },
});
