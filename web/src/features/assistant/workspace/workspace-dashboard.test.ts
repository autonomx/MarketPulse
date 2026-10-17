import { describe, expect, it } from 'vitest';
import { createAssistantWorkspaceDashboard } from './workspace-dashboard';

const baseInput = {
  workspaceName: 'Default workspace',
  projectName: 'Chatbot',
  sessionTitle: 'Planning chat',
  sessionMode: 'text' as const,
  providerLabel: 'OpenAI compatible',
  modelLabel: 'GPT mini',
  messageCount: 3,
  contextSourceCount: 2,
  memoryCount: 1,
  knowledgeChunkCount: 4,
  enabledToolCount: 2,
  qualitySignals: [
    { id: 'event-stream', label: 'Event stream available', passed: true, severity: 'info' as const },
    { id: 'provider', label: 'Provider selected', passed: true, severity: 'warning' as const },
  ],
};

describe('createAssistantWorkspaceDashboard', () => {
  it('builds a compact workspace status view', () => {
    const dashboard = createAssistantWorkspaceDashboard(baseInput);

    expect(dashboard.title).toBe('Default workspace');
    expect(dashboard.subtitle).toBe('Chatbot · Planning chat');
    expect(dashboard.status).toBe('ready');
    expect(dashboard.badges).toEqual(['text', 'OpenAI compatible', 'GPT mini', 'text only']);
    expect(dashboard.metrics).toContainEqual({ id: 'context', label: 'Context sources', value: '2' });
  });

  it('surfaces failed quality signals for the UI', () => {
    const dashboard = createAssistantWorkspaceDashboard({
      ...baseInput,
      qualitySignals: [
        { id: 'provider', label: 'Provider selected', passed: false, severity: 'warning' },
        { id: 'events', label: 'Event stream missing', passed: false, severity: 'error' },
      ],
    });

    expect(dashboard.status).toBe('blocked');
    expect(dashboard.statusLabel).toBe('Blocked');
    expect(dashboard.failedQualitySignals.map((signal) => signal.label)).toEqual(['Provider selected', 'Event stream missing']);
  });
});
