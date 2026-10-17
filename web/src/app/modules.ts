import type { OmnixModuleManifest } from './moduleManifest';
import { moduleManifests } from './modulesManifest';

export { moduleManifests };

export type OmnixModuleId = (typeof moduleManifests)[number]['id'];
export type OmnixModuleRoute = (typeof moduleManifests)[number]['route'];

/** Where `/` leads, and the first mode in the top bar. */
export const defaultModuleId: OmnixModuleId = 'chatbot';

export interface OmnixModuleDefinition {
  id: OmnixModuleId;
  label: string;
  summary: string;
  route: OmnixModuleRoute;
}

/** What navigation shows and workspaces receive for each module. */
export function moduleDefinitions<TManifest extends OmnixModuleManifest>(
  manifests: readonly TManifest[],
): Array<Pick<TManifest, 'id' | 'label' | 'summary' | 'route'>> {
  return manifests.map((manifest) => ({ id: manifest.id, label: manifest.label, summary: manifest.summary, route: manifest.route }));
}

export const omnixModules: OmnixModuleDefinition[] = moduleDefinitions(moduleManifests);

const manifestById = new Map<string, OmnixModuleManifest>(moduleManifests.map((manifest) => [manifest.id, manifest]));

export function moduleManifest(moduleId: OmnixModuleId): OmnixModuleManifest {
  const manifest = manifestById.get(moduleId);
  if (!manifest) throw new Error(`Unknown module: ${moduleId}`);
  return manifest;
}

export function isPlatformModule(moduleId: OmnixModuleId): boolean {
  return Boolean(moduleManifest(moduleId).platform);
}
