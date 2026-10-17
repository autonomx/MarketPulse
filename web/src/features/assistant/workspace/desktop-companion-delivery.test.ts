import { describe, expect, it } from 'vitest';

import {
  decideDesktopCompanionDelivery,
  parseDesktopCompanionSse,
  type DesktopCompanionDeliveryRequest,
} from './desktop-companion-delivery';
import { INITIAL_LIVE_CONVERSATION_RUNTIME_STATE, type LiveConversationRuntimeState } from './live-conversation-store';

const request: DesktopCompanionDeliveryRequest = {
  sessionId: 'chat-1',
  observationId: 'obs-1',
  groundingIds: ['obs-1'],
  stateSummary: 'Current scene: inventory',
  priority: 'normal',
  presentation: 'text',
  expiresAtMs: 20_000,
};

function runtime(patch: Partial<LiveConversationRuntimeState> = {}): LiveConversationRuntimeState {
  return {
    ...INITIAL_LIVE_CONVERSATION_RUNTIME_STATE,
    sessionId: 'chat-1',
    ...patch,
  };
}

describe('desktop companion delivery arbitration', () => {
  it('delivers fresh text candidates without requiring auto-speech', () => {
    expect(decideDesktopCompanionDelivery(request, runtime(), {
      nowMs: 10_000,
      requestInFlight: false,
    })).toEqual({ action: 'deliver', reason: 'desktop_candidate_eligible' });

    expect(decideDesktopCompanionDelivery(request, runtime(), {
      nowMs: 20_000,
      requestInFlight: false,
    })).toEqual({ action: 'suppress', reason: 'candidate_stale' });
  });

  it('waits while the user or assistant owns the floor', () => {
    const user = runtime({
      conversation: {
        ...INITIAL_LIVE_CONVERSATION_RUNTIME_STATE.conversation,
        floorOwner: 'user',
        userTurn: 'speaking',
      },
    });
    const assistant = runtime({
      conversation: {
        ...INITIAL_LIVE_CONVERSATION_RUNTIME_STATE.conversation,
        floorOwner: 'assistant',
        assistantTurn: 'speaking',
        delivery: 'audio_started',
      },
    });

    expect(decideDesktopCompanionDelivery(request, user, {
      nowMs: 10_000, requestInFlight: false,
    }).reason).toBe('user_floor_active');
    expect(decideDesktopCompanionDelivery(request, assistant, {
      nowMs: 10_000, requestInFlight: false,
    }).reason).toBe('assistant_busy');
  });

  it('does not compete with existing social initiative or barge-in', () => {
    const initiative = runtime({
      conversation: {
        ...INITIAL_LIVE_CONVERSATION_RUNTIME_STATE.conversation,
        initiative: 'prompting',
      },
    });
    const bargeIn = runtime({
      conversation: {
        ...INITIAL_LIVE_CONVERSATION_RUNTIME_STATE.conversation,
        bargeIn: 'confirming',
      },
    });

    expect(decideDesktopCompanionDelivery(request, initiative, {
      nowMs: 10_000, requestInFlight: false,
    }).reason).toBe('social_initiative_active');
    expect(decideDesktopCompanionDelivery(request, bargeIn, {
      nowMs: 10_000, requestInFlight: false,
    }).reason).toBe('barge_in_active');
  });
});

describe('desktop companion proactive SSE', () => {
  it('parses the shared live-call stream and preserves critical purpose', () => {
    const parsed = parseDesktopCompanionSse([
      'data: {"type":"initiative","turn_id":"desktop:one"}',
      '',
      'data: {"type":"text_chunk","text":"Watch that health bar."}',
      '',
      'data: {"type":"complete","content":"Watch that health bar.","metadata":{"turn_id":"desktop:one","purpose":"desktop_critical"}}',
      '',
    ].join('\n'));

    expect(parsed).toEqual({
      turnId: 'desktop:one',
      content: 'Watch that health bar.',
      purpose: 'desktop_critical',
    });
  });
});
