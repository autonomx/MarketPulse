import { describe, expect, it } from 'vitest';
import {
  DEFAULT_GITHUB_TOOL_ACTIONS,
  canExecuteToolAction,
  createToolAction,
  requiresToolActionApproval,
  updateToolActionEnabled,
} from './tool-actions';

describe('assistant workspace tool actions', () => {
  it('defaults low-risk read actions to automatic execution', () => {
    const action = createToolAction({
      id: 'example.read',
      label: 'Read example',
      description: 'Read example data.',
      category: 'read',
      riskLevel: 'low',
      requiresConnection: true,
      requiresConfirmation: false,
      isDestructive: false,
    });

    expect(action.approvalPolicy).toBe('allow_automatic');
    expect(canExecuteToolAction(action)).toEqual({ allowed: true, approvalRequired: false });
  });

  it('requires approval for high-risk write actions', () => {
    const mergeAction = DEFAULT_GITHUB_TOOL_ACTIONS.find((action) => action.id === 'github.merge_pr');

    expect(mergeAction).toBeDefined();
    expect(mergeAction?.approvalPolicy).toBe('always_ask');
    expect(mergeAction ? requiresToolActionApproval(mergeAction) : false).toBe(true);
  });

  it('blocks disabled actions before execution', () => {
    const deleteBranch = DEFAULT_GITHUB_TOOL_ACTIONS.find((action) => action.id === 'github.delete_branch');

    expect(deleteBranch).toBeDefined();
    expect(deleteBranch?.isDestructive).toBe(true);
    expect(deleteBranch ? canExecuteToolAction(deleteBranch).allowed : true).toBe(false);
  });

  it('updates enablement without mutating the original action', () => {
    const action = createToolAction({
      id: 'example.write',
      label: 'Write example',
      description: 'Write example data.',
      category: 'write',
      riskLevel: 'medium',
      requiresConnection: true,
      requiresConfirmation: false,
      isDestructive: false,
    });

    const disabled = updateToolActionEnabled(action, false);

    expect(action.enabled).toBe(true);
    expect(disabled.enabled).toBe(false);
    expect(canExecuteToolAction(disabled).allowed).toBe(false);
  });
});
