import { describe, expect, it } from 'vitest';

import {
  normalizeDeliveryEvaluationEvent,
  normalizeEvaluationEvent,
  scenarioForDeliveryEvidence,
} from './desktop-companion-shadow-evaluation-controller';

describe('desktop companion evaluation events', () => {
  it('accepts bounded content-free lifecycle facts and effective stage', () => {
    expect(normalizeEvaluationEvent({
      kind: 'vision_result',
      sessionId: 'chat:1',
      rolloutStage: 'text',
      scenario: 'scene-change',
      latencyMs: 1234.5,
      callsThisMinute: 3,
      providerError: false,
      stale: false,
      observed: true,
      reason: 'observation_completed',
    })).toEqual({
      kind: 'vision_result',
      sessionId: 'chat:1',
      characterId: null,
      modelId: null,
      remoteProvider: false,
      rolloutStage: 'text',
      scenario: 'scene-change',
      meaningful: false,
      latencyMs: 1234.5,
      callsThisMinute: 3,
      providerError: false,
      stale: false,
      observed: true,
      reason: 'observation_completed',
    });
  });

  it('rejects unknown event types and ignores content-bearing extras', () => {
    expect(normalizeEvaluationEvent({ kind: 'frame', sessionId: 'chat:1' })).toBeNull();
    const result = normalizeEvaluationEvent({
      kind: 'capture',
      sessionId: 'chat:1',
      scenario: 'typing',
      meaningful: true,
      frame: 'data:image/png;base64,secret',
      screenText: 'secret',
    });
    expect(result).toEqual({
      kind: 'capture',
      sessionId: 'chat:1',
      characterId: null,
      modelId: null,
      remoteProvider: false,
      rolloutStage: undefined,
      scenario: 'typing',
      meaningful: true,
      latencyMs: undefined,
      callsThisMinute: undefined,
      providerError: false,
      stale: false,
      observed: false,
      reason: undefined,
    });
    expect(JSON.stringify(result)).not.toContain('secret');
  });

  it('normalizes speech outcomes without retaining generated content', () => {
    const result = normalizeDeliveryEvaluationEvent({
      status: 'interrupted',
      sessionId: 'chat:1',
      presentation: 'speech',
      reason: 'user_speech',
      content: 'private generated response',
      turnId: 'desktop:1',
    });

    expect(result).toEqual({
      status: 'interrupted',
      sessionId: 'chat:1',
      presentation: 'speech',
      reason: 'user_speech',
    });
    expect(JSON.stringify(result)).not.toContain('private generated response');
  });

  it('maps text and speech delivery outcomes to identifier-only scenarios', () => {
    expect(scenarioForDeliveryEvidence({
      status: 'interrupted', sessionId: 'chat:1', presentation: 'text', reason: 'interrupted',
    })).toBe('interruption');
    expect(scenarioForDeliveryEvidence({
      status: 'completed', sessionId: 'chat:1', presentation: 'speech', reason: null,
    })).toBe('speech-completed');
    expect(scenarioForDeliveryEvidence({
      status: 'suppress', sessionId: 'chat:1', presentation: 'speech', reason: 'candidate_stale',
    })).toBe('speech-stale');
  });
});
