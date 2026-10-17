import { defineModule } from '../../app/moduleManifest';

/** The platform workspace modules (WP-9.7). */
export const providersModule = defineModule({
  id: 'providers',
  label: 'Providers',
  summary: 'Shared provider registry, model discovery, health, latency, and capabilities.',
  route: '/providers',
  icon: '◇',
  backendModules: [],
  apiPrefixes: ['/api/providers', '/api/models', '/api/jobs', '/api/settings', '/api/health', '/api/diagnostics'],
  sidebar: false,
  platform: true,
  loadWorkspace: () => import('./PlatformModuleWorkspace').then((module) => module.PlatformModuleWorkspace),
});

export const modelsModule = defineModule({
  id: 'models',
  label: 'Models',
  summary: 'Installed and remote models, capability mapping, defaults, and resource hints.',
  route: '/models',
  icon: '✧',
  backendModules: [],
  apiPrefixes: ['/api/models', '/api/providers', '/api/jobs', '/api/settings', '/api/health', '/api/diagnostics'],
  sidebar: false,
  platform: true,
  loadWorkspace: () => import('./PlatformModuleWorkspace').then((module) => module.PlatformModuleWorkspace),
});

export const jobsModule = defineModule({
  id: 'jobs',
  label: 'Jobs / Runs',
  summary: 'Shared long-running job queue, run history, progress, and logs.',
  route: '/jobs',
  icon: '↻',
  backendModules: [],
  apiPrefixes: ['/api/jobs', '/api/assets', '/api/reports', '/api/diagnostics'],
  sidebar: false,
  platform: true,
  loadWorkspace: () => import('./PlatformModuleWorkspace').then((module) => module.PlatformModuleWorkspace),
});

export const assetsModule = defineModule({
  id: 'assets',
  label: 'Assets',
  summary: 'Generated audio, images, transcripts, reports, checkpoints, and exports.',
  route: '/assets',
  icon: '▤',
  backendModules: [],
  apiPrefixes: ['/api/assets', '/api/jobs', '/api/reports'],
  platform: true,
  loadWorkspace: () => import('./PlatformModuleWorkspace').then((module) => module.PlatformModuleWorkspace),
});

export const reportsModule = defineModule({
  id: 'reports',
  label: 'Reports',
  summary: 'Run reports, RPG autoplay evidence, diagnostics exports, and generated documents.',
  route: '/reports',
  icon: '☷',
  backendModules: [],
  apiPrefixes: ['/api/reports', '/api/assets', '/api/jobs', '/api/replay'],
  platform: true,
  loadWorkspace: () => import('./PlatformModuleWorkspace').then((module) => module.PlatformModuleWorkspace),
});

export const diagnosticsModule = defineModule({
  id: 'diagnostics',
  label: 'Diagnostics',
  summary: 'Health checks, logs, event stream status, and troubleshooting surfaces.',
  route: '/diagnostics',
  icon: '⌕',
  backendModules: [],
  apiPrefixes: ['/api/diagnostics', '/api/health', '/api/runtime', '/api/providers', '/api/models', '/api/jobs'],
  platform: true,
  loadWorkspace: () => import('./PlatformModuleWorkspace').then((module) => module.PlatformModuleWorkspace),
});
