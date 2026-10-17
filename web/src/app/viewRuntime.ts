import { DisposableStore, type Disposable, type ModuleRuntimeContext } from './moduleRuntime';

export type { ModuleRuntimeContext } from './moduleRuntime';
import { moduleManifest, type OmnixModuleId } from './modules';

/**
 * Browser-side runtime of the active workspace (WP-9.1).
 *
 * Entering a workspace activates its runtime; leaving it disposes it. Every
 * initializer returns a cleanup that goes into the workspace's store, so
 * listeners, observers, timers, fetch middleware and client patches do not
 * outlive the route. Each module's manifest provides its runtime, loaded lazily, so Chat does not
 * boot trading, voice or audiobook runtimes (and vice versa).
 */

const activeStores = new Map<OmnixModuleId, DisposableStore>();

export interface ActiveViewRuntime extends Disposable {
  /** Settles when activation finished (or failed and was logged). */
  readonly ready: Promise<void>;
}

export function activateViewRuntime(moduleId: OmnixModuleId, context: ModuleRuntimeContext): ActiveViewRuntime {
  const store = new DisposableStore();
  activeStores.get(moduleId)?.dispose();
  activeStores.set(moduleId, store);
  const ready = loadViewRuntime(moduleId, context, store).catch((error: unknown) => {
    console.error(`[Omnix] ${moduleId} workspace runtime failed to initialize`, error);
  });
  return {
    ready,
    dispose: () => {
      store.dispose();
      if (activeStores.get(moduleId) === store) activeStores.delete(moduleId);
    },
  };
}

/** Workspaces whose runtime is active (tests and diagnostics). */
export function activeViewRuntimes(): OmnixModuleId[] {
  return [...activeStores.keys()];
}

async function loadViewRuntime(moduleId: OmnixModuleId, context: ModuleRuntimeContext, store: DisposableStore): Promise<void> {
  await moduleManifest(moduleId).activateRuntime?.(context, store);
}

