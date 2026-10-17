import { defineModule } from '../../app/moduleManifest';

/** The workspace module this feature provides (WP-9.7). */
export const imageGenerationModule = defineModule({
  id: 'image-generation',
  label: 'Image Generation',
  summary: 'Portraits, scenes, covers, image assets, and visual provider status.',
  route: '/image-generation',
  icon: '▧',
  backendModules: ['image'],
  apiPrefixes: [
    '/api/image-generation', '/api/assets', '/api/jobs', '/api/providers', '/api/settings', '/api/workers',
    '/api/agent', '/api/prompts',
  ],
  modeLabel: 'Image Generation',
  loadWorkspace: () => import('./ImageGenerationWorkspace').then((module) => module.ImageGenerationWorkspace),
});
