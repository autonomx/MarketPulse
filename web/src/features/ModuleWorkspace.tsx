import { Suspense, type ComponentType, type LazyExoticComponent } from 'react';
import { moduleManifest, type OmnixModuleDefinition, type OmnixModuleId } from '../app/modules';
import { lazyWithRetry, RouteErrorBoundary } from '../app/RouteErrorBoundary';
import { WorkspacePanel } from '../design/primitives';

type Workspace = LazyExoticComponent<ComponentType<{ module: OmnixModuleDefinition }>>;
const workspaces = new Map<OmnixModuleId, Workspace>();

/** The module's workspace, loaded from its manifest on first use (WP-9.7). */
function workspaceFor(moduleId: OmnixModuleId): Workspace {
  let workspace = workspaces.get(moduleId);
  if (!workspace) {
    const manifest = moduleManifest(moduleId);
    workspace = lazyWithRetry(() => manifest.loadWorkspace().then((component) => ({ default: component })));
    workspaces.set(moduleId, workspace);
  }
  return workspace;
}

export function ModuleWorkspace({ module }: { module: OmnixModuleDefinition }) {
  const Workspace = workspaceFor(module.id);
  return (
    <RouteErrorBoundary resetKey={module.id}>
      <Suspense fallback={<WorkspacePanel label={module.label}><p className="workspace-summary">Loading {module.label} workspace…</p></WorkspacePanel>}>
        <Workspace module={module} />
      </Suspense>
    </RouteErrorBoundary>
  );
}
