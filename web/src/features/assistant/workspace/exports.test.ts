import { describe, expect, it } from 'vitest';
import {
  DEFAULT_ASSISTANT_IDENTITY_NAMES,
  CONTEXT_PANEL_TABS,
  DEFAULT_ASSISTANT_APP_REGIONS,
  DEFAULT_COMPOSER_CONTROLS,
  LIVE_SESSION_MODES,
  canUseCapability,
  createAssistantIdentity,
  createContextPanelSummary,
  createMemoryRecord,
  createMemoryViewRows,
  createPlaybackQueue,
  createProcessStep,
  createTextSegment,
  createTimelineNote,
  createWorkspaceProjectTree,
  getReadyLibraryItems,
  isContextPanelTab,
  isLiveSessionMode,
  sortInstructionRecords,
} from './index';

describe('assistant workspace public exports', () => {
  it('exposes helper modules added after the foundation phases', () => {
    expect(DEFAULT_ASSISTANT_IDENTITY_NAMES).toContain('Architect');
    expect(CONTEXT_PANEL_TABS).toContain('audit');
    expect(DEFAULT_ASSISTANT_APP_REGIONS).toContain('timeline');
    expect(DEFAULT_COMPOSER_CONTROLS).toContain('voice');
    expect(LIVE_SESSION_MODES).toContain('ready');
    expect(isContextPanelTab('tools')).toBe(true);
    expect(isLiveSessionMode('ready')).toBe(true);

    expect(
      createAssistantIdentity({
        id: 'assistant-1',
        name: 'Architect',
        description: 'Design assistant',
        systemPrompt: 'Be precise.',
        createdAt: '2026-01-01T00:00:00.000Z',
        updatedAt: '2026-01-01T00:00:00.000Z',
      }).name,
    ).toBe('Architect');

    expect(createMemoryRecord({
      id: 'memory-1',
      scope: 'workspace',
      source: 'user_saved',
      category: 'fact',
      content: 'Use rpg branch.',
      confidence: 1,
      createdAt: '2026-01-01T00:00:00.000Z',
      updatedAt: '2026-01-01T00:00:00.000Z',
    }).scope).toBe('workspace');

    expect(createMemoryViewRows([])).toEqual([]);
    expect(getReadyLibraryItems([{ id: 'library-1', workspaceId: 'workspace-1', title: 'Readme', status: 'ready' }])).toHaveLength(1);
    expect(sortInstructionRecords([{ id: 'instruction-1', scope: 'workspace', content: 'A', priority: 1, enabled: true }])[0]?.id).toBe('instruction-1');
    expect(createTimelineNote('note-1', 'Ready', '2026-01-01T00:00:00.000Z').kind).toBe('note');
    expect(createWorkspaceProjectTree({
      workspace: {
        id: 'workspace-1',
        name: 'W',
        createdAt: '2026-01-01T00:00:00.000Z',
        updatedAt: '2026-01-01T00:00:00.000Z',
      },
      projects: [],
      conversations: [],
    }).workspace.id).toBe('workspace-1');
    expect(createTextSegment({ id: 'segment-1', text: 'Hi', kind: 'complete', createdAt: '2026-01-01T00:00:00.000Z' }).kind).toBe('complete');
    expect(createProcessStep({ id: 'step-1', stage: 'input', completed: true }).completed).toBe(true);
    expect(createPlaybackQueue()).toEqual({ items: [] });
    expect(canUseCapability({ id: 'capability-1', name: 'Search', description: 'Search', scope: 'global', enabled: true }, 'workspace')).toBe(true);
    expect(createContextPanelSummary({
      conversation: {
        session: {
          id: 'session-1',
          workspaceId: 'workspace-1',
          title: 'Session',
          mode: 'text',
          createdAt: '2026-01-01T00:00:00.000Z',
          updatedAt: '2026-01-01T00:00:00.000Z',
        },
        turns: [],
        events: [],
      },
      workspaceInstructions: [],
      projectInstructions: [],
      sessionInstructions: [],
      memories: [],
      retrievedKnowledge: [],
      activeTools: [],
      sources: [],
      includedSources: [],
      omittedSources: [],
      estimatedTokens: 0,
      budget: {
        maxTokens: 10,
        reserved: {
          system: 1,
          conversation: 3,
          memory: 1,
          knowledge: 2,
          tools: 1,
          response: 2,
        },
      },
    }).maxTokens).toBe(10);
  });
});
