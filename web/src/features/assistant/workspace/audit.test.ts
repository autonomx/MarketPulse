import { describe, expect, it } from 'vitest';
import { createAssistantResponseAudit, explainContextSource, summarizeAssistantResponseAudit } from './audit';

const source = {
  type: 'memory' as const,
  sourceId: 'memory-1',
  title: 'Preference',
  reasonIncluded: 'Pinned memory in active scope.',
};

describe('assistant workspace response audit contracts', () => {
  it('copies audit sources and summarizes provenance', () => {
    const audit = createAssistantResponseAudit({
      responseEventId: 'response-1',
      provider: 'local',
      model: 'test-model',
      contextSources: [source],
      tokenUsage: { inputTokens: 10, outputTokens: 5 },
      latencyMs: 25,
    });

    expect(audit.contextSources).toEqual([source]);
    expect(audit.contextSources).not.toBe([source]);
    expect(summarizeAssistantResponseAudit(audit)).toEqual({
      responseEventId: 'response-1',
      sourceCount: 1,
      sourceTypes: ['memory'],
      tokenTotal: 15,
      latencyMs: 25,
    });
  });

  it('explains context sources for audit UI rows', () => {
    expect(explainContextSource(source)).toContain('memory');
    expect(explainContextSource(source)).toContain('Preference');
  });
});
