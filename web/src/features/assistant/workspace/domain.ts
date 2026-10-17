export const ASSISTANT_WORKSPACE_SESSION_MODES = ['text', 'voice', 'mixed'] as const;

export type ChatSessionMode = (typeof ASSISTANT_WORKSPACE_SESSION_MODES)[number];

export type Workspace = {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
};

export type Project = {
  id: string;
  workspaceId: string;
  name: string;
  description?: string;
  createdAt: string;
  updatedAt: string;
};

export type WorkspaceChatSession = {
  id: string;
  workspaceId: string;
  projectId?: string;
  title: string;
  mode: ChatSessionMode;
  assistantIdentityId?: string;
  provider?: string;
  model?: string;
  createdAt: string;
  updatedAt: string;
};

export type WorkspaceRef = Pick<Workspace, 'id' | 'name'>;
export type ProjectRef = Pick<Project, 'id' | 'workspaceId' | 'name'>;
export type ChatSessionRef = Pick<WorkspaceChatSession, 'id' | 'workspaceId' | 'projectId' | 'title' | 'mode'>;

export function isChatSessionMode(value: string): value is ChatSessionMode {
  return ASSISTANT_WORKSPACE_SESSION_MODES.includes(value as ChatSessionMode);
}

export function assertWorkspaceProjectLink(workspace: Workspace, project: Project): void {
  if (project.workspaceId !== workspace.id) {
    throw new Error(`Project ${project.id} does not belong to workspace ${workspace.id}`);
  }
}

export function assertWorkspaceSessionLink(workspace: Workspace, session: WorkspaceChatSession): void {
  if (session.workspaceId !== workspace.id) {
    throw new Error(`Session ${session.id} does not belong to workspace ${workspace.id}`);
  }
}

export function assertProjectSessionLink(project: Project, session: WorkspaceChatSession): void {
  if (session.projectId !== undefined && session.projectId !== project.id) {
    throw new Error(`Session ${session.id} does not belong to project ${project.id}`);
  }

  if (session.workspaceId !== project.workspaceId) {
    throw new Error(`Session ${session.id} does not belong to project workspace ${project.workspaceId}`);
  }
}

export function createWorkspaceRef(workspace: Workspace): WorkspaceRef {
  return {
    id: workspace.id,
    name: workspace.name,
  };
}

export function createProjectRef(project: Project): ProjectRef {
  return {
    id: project.id,
    workspaceId: project.workspaceId,
    name: project.name,
  };
}

export function createChatSessionRef(session: WorkspaceChatSession): ChatSessionRef {
  return {
    id: session.id,
    workspaceId: session.workspaceId,
    projectId: session.projectId,
    title: session.title,
    mode: session.mode,
  };
}
