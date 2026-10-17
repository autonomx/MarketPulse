import type { ComponentType } from 'react';
import type { DisposableStore, ModuleRuntimeContext } from './moduleRuntime';
import type { OmnixModuleDefinition } from './modules';

/**
 * A workspace module's manifest (WP-9.7). Adding a module is one manifest in
 * its feature (`features/<name>/module.ts`) plus one line in `app/modules.ts`:
 * navigation, routes, the view API scope, icons, the lazy workspace and its
 * runtime all derive from the manifests.
 */
export interface OmnixModuleManifest<TId extends string = string, TRoute extends `/${string}` = `/${string}`> {
  id: TId;
  label: string;
  summary: string;
  route: TRoute;
  /** The navigation monogram. */
  icon: string;
  /** API families the workspace may call; the view API firewall blocks the rest. */
  apiPrefixes: readonly string[];
  /**
   * Backend feature ids whose gateway operations this feature owns (PA-2.4):
   * their paths and schemas are generated into `features/<name>/api/generated.ts`.
   * A literal list, read by `scripts/generate-api-types.mjs`.
   */
  backendModules: readonly string[];
  /**
   * Operations (`'METHOD /path'`) of another feature's backend modules that this
   * feature also calls, such as settings showing research status. They are added
   * to this feature's generated paths; each one is listed, reviewed and kept small.
   */
  usesOperations?: readonly string[];
  /** Shown in the sidebar (default true); hidden modules stay reachable by route. */
  sidebar?: boolean;
  /** Shown as a mode in the top bar, under this label. */
  modeLabel?: string;
  /** Platform workspaces (providers, jobs, …) share the platform layout. */
  platform?: boolean;
  /** Loads the workspace component when its route first opens. */
  loadWorkspace: () => Promise<ComponentType<{ module: OmnixModuleDefinition }>>;
  /** Starts the workspace's browser runtime on entry; each cleanup goes into `store` and runs on exit. */
  activateRuntime?: (context: ModuleRuntimeContext, store: DisposableStore) => Promise<void>;
}

/** Declares a manifest, keeping its id and route as literal types. */
export function defineModule<const TId extends string, const TRoute extends `/${string}`>(
  manifest: OmnixModuleManifest<TId, TRoute>,
): OmnixModuleManifest<TId, TRoute> {
  return manifest;
}
