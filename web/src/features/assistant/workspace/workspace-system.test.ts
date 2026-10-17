import { describe, expect, it } from 'vitest';
import { createWorkspaceProjectTree, getProjectConversationIds, summarizeWorkspaceProjectTree } from './workspace-system';

const workspace = { id: 'w1', name: 'Workspace', createdAt: 't1', updatedAt: 't1' };
const project = { id: 'p1', workspaceId: 'w1', name: 'Project', createdAt: 't1', updatedAt: 't1' };
const otherProject = { id: 'p2', workspaceId: 'w2', name: 'Other', createdAt: 't1', updatedAt: 't1' };
const conversation = {
  id: 's1',
  workspaceId: 'w1',
  projectId: 'p1',
  title: 'Session',
  mode: 'text' as const,
  createdAt: 't1',
  updatedAt: 't1',
};

describe('workspace project system contracts', () => {
  it('filters tree contents to the active workspace', () => {
    const tree = createWorkspaceProjectTree({ workspace, projects: [project, otherProject], conversations: [conversation] });

    expect(tree.projects).toEqual([project]);
    expect(summarizeWorkspaceProjectTree(tree)).toEqual({
      workspaceId: 'w1',
      projectCount: 1,
      conversationCount: 1,
    });
  });

  it('finds conversations scoped to a project', () => {
    const tree = createWorkspaceProjectTree({ workspace, projects: [project], conversations: [conversation] });
    expect(getProjectConversationIds(tree, 'p1')).toEqual(['s1']);
  });
});
