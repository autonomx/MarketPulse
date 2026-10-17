import type { OmnixModuleDefinition } from '../../../app/modules';
import { useChatWorkspaceState } from './useChatWorkspaceState';

/** Chat's state, data and commands, wired from its hooks (WP-9.5). */
export function useChatWorkspace(module: OmnixModuleDefinition) {
  const chat = useChatWorkspaceState();
  return { module, ...chat };
}

export type ChatWorkspaceModel = ReturnType<typeof useChatWorkspace>;
