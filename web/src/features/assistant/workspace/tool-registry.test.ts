import { describe, expect, it, vi } from 'vitest';
import { canExecuteToolAction } from './tool-actions';
import { createDefaultAssistantToolRegistry, executeAssistantToolRequest } from './tool-registry';

describe('assistant workspace tool registry', () => {
  it('registers default assistant tools by category', () => {
    const registry = createDefaultAssistantToolRegistry();
    const tools = registry.list();

    expect(tools.map((tool) => tool.id)).toEqual(['gmail', 'calendar', 'contacts', 'github']);
    expect(registry.get('github')?.category).toBe('development');
    expect(registry.get('gmail')?.category).toBe('communication');
    expect(registry.get('calendar')?.category).toBe('productivity');
    expect(registry.get('contacts')?.category).toBe('productivity');
  });

  it('discovers actions across registered tools', () => {
    const registry = createDefaultAssistantToolRegistry();
    const match = registry.getAction('github.merge_pr');

    expect(match?.tool.id).toBe('github');
    expect(match?.action.label).toBe('Merge pull requests');
    expect(match?.action.requiresConfirmation).toBe(true);
  });

  it('keeps destructive defaults disabled or approval gated', () => {
    const registry = createDefaultAssistantToolRegistry();
    const deleteBranch = registry.getAction('github.delete_branch');

    expect(deleteBranch?.action.isDestructive).toBe(true);
    expect(deleteBranch ? canExecuteToolAction(deleteBranch.action).allowed : true).toBe(false);
  });

  it('validates connection-backed tool config', () => {
    const registry = createDefaultAssistantToolRegistry();
    const gmail = registry.get('gmail');

    expect(gmail).toBeDefined();
    expect(gmail?.validateConfig({ enabled: true, connectionStatus: 'not_configured' })).toEqual({
      valid: false,
      status: 'not_configured',
      messages: ['Tool must be connected before actions can run.'],
    });
    expect(gmail?.validateConfig({ enabled: true, connectionStatus: 'connected' }).valid).toBe(true);
  });

  it('routes execution requests through the backend endpoint', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ proposal_id: 'server-proposal', approval_required: false }))
      .mockResolvedValueOnce(Response.json({ execution_result: { output: { messages: [] } } }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await executeAssistantToolRequest(
      { toolId: 'gmail', actionId: 'gmail.read_email', input: { query: 'receipt' } },
      { enabled: true, connectionStatus: 'connected' },
    );

    expect(result.status).toBe('completed');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/assistant/tools/proposals',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(fetchMock).toHaveBeenLastCalledWith('/api/assistant/tools/proposals/server-proposal/execute', expect.objectContaining({ method: 'POST' }));
    vi.unstubAllGlobals();
  });
});
