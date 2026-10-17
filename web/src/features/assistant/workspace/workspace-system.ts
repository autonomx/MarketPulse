import type { WorkspaceChatSession, Project, Workspace } from './domain';

export type WorkspaceProjectTree = {
  workspace: Workspace;
  projects: Project[];
  conversations: WorkspaceChatSession[];
};

export type ProjectWorkspaceSummary = {
  workspaceId: string;
  projectCount: number;
  conversationCount: number;
};

export function createWorkspaceProjectTree(input: WorkspaceProjectTree): WorkspaceProjectTree {
  return {
    workspace: { ...input.workspace },
    projects: input.projects.filter((project) => project.workspaceId === input.workspace.id).map((project) => ({ ...project })),
    conversations: input.conversations
      .filter((session) => session.workspaceId === input.workspace.id)
      .map((session) => ({ ...session })),
  };
}

export function summarizeWorkspaceProjectTree(tree: WorkspaceProjectTree): ProjectWorkspaceSummary {
  return {
    workspaceId: tree.workspace.id,
    projectCount: tree.projects.length,
    conversationCount: tree.conversations.length,
  };
}

export function getProjectConversationIds(tree: WorkspaceProjectTree, projectId: string): string[] {
  return tree.conversations
    .filter((session) => session.projectId === projectId)
    .map((session) => session.id);
}
