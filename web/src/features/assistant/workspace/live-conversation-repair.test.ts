import { describe, expect, it } from 'vitest';

import { planConversationRepair } from './live-conversation-repair';

describe('planConversationRepair', () => {
  it('ignores user continuers', () => {
    expect(planConversationRepair({ transcript: 'mhm', overlapIntent: 'backchannel' })).toBeNull();
  });

  it('plans correction and clarification repairs', () => {
    expect(planConversationRepair({ transcript: 'Actually, I meant Tuesday.', overlapReason: 'correction', confidence: 0.9 })?.kind)
      .toBe('acknowledge_correction');
    expect(planConversationRepair({ transcript: 'Did you say fifteen or fifty?', confidence: 0.8 })?.kind)
      .toBe('clarify_number');
    expect(planConversationRepair({ transcript: 'What was the name called?', confidence: 0.7 })?.kind)
      .toBe('clarify_name');
  });

  it('plans yielding and interrupted-thought recovery', () => {
    expect(planConversationRepair({ transcript: 'Wait.', overlapIntent: 'hard_stop', overlapReason: 'explicit_stop' })?.kind)
      .toBe('yield_to_user');
    expect(planConversationRepair({ transcript: 'Can I add something?', overlapIntent: 'interrupt', assistantWasInterrupted: true })?.kind)
      .toBe('resume_interrupted_thought');
  });

  it('clamps confidence', () => {
    expect(planConversationRepair({ transcript: 'Actually, no.', overlapReason: 'correction', confidence: 3 })?.confidence).toBe(1);
  });
});
