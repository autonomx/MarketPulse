import { describe, expect, it } from 'vitest';
import { createContextPanelSummary, isContextPanelTab } from './context-view';

const budgetedContext = {
  budget: { maxTokens: 100, reserved: { system: 10, conversation: 20, memory: 10, knowledge: 20, tools: 5, response: 10 } },
  includedSources: [{ type: 'conversation' as const, sourceId: 's1', reasonIncluded: 'Current session.' }],
  omittedSources: [],
  estimatedTokens: 8,
} as unknown as Parameters<typeof createContextPanelSummary>[0];

describe('context visualization contracts', () => {
  it('summarizes context budget state', () => {
    expect(createContextPanelSummary(budgetedContext)).toMatchObject({
      includedSourceCount: 1,
      omittedSourceCount: 0,
      estimatedTokens: 8,
      maxTokens: 100,
    });
  });

  it('recognizes known panel tabs', () => {
    expect(isContextPanelTab('audit')).toBe(true);
    expect(isContextPanelTab('unknown')).toBe(false);
  });
});
