/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { beforeEach, describe, expect, it } from 'vitest';

import { liveConversationStore } from './live-conversation-store';
import {
  decideAssistantListenerBackchannel,
  isUserContinuer,
  resolveBackchannelCadence,
  resolveBackchannelTranscript,
} from './live-voice-backchannel';

describe('live voice backchannels', () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.body.innerHTML = '';
    liveConversationStore.reset();
  });

  it('recognizes user continuers without authorizing assistant speech', () => {
    expect(isUserContinuer('mhm')).toBe(true);
    expect(isUserContinuer('Yeah.')).toBe(true);
    expect(isUserContinuer('Actually, that is wrong.')).toBe(false);
  });

  it('requires sustained speech, a safe boundary, and echo-aware duplex', () => {
    expect(decideAssistantListenerBackchannel('I was explaining the whole situation,', 'natural', 4_000, 10_000, 0, 'half_duplex').reason)
      .toBe('requires_echo_aware_duplex');
    expect(decideAssistantListenerBackchannel('I was explaining the whole situation,', 'natural', 2_000, 10_000, 0, 'echo_aware').reason)
      .toBe('speech_too_short');
    expect(decideAssistantListenerBackchannel('I was explaining', 'natural', 4_000, 10_000, 0, 'echo_aware').reason)
      .toBe('no_safe_clause_boundary');
  });

  it('blocks sensitive, corrective, and question-shaped speech', () => {
    expect(decideAssistantListenerBackchannel('my passcode is 123456,', 'natural', 4_000, 10_000, 0, 'echo_aware').reason)
      .toBe('sensitive_dictation');
    expect(decideAssistantListenerBackchannel('Actually, that was wrong.', 'natural', 4_000, 10_000, 0, 'echo_aware').reason)
      .toBe('semantic_risk');
    expect(decideAssistantListenerBackchannel('Where should I go?', 'natural', 4_000, 10_000, 0, 'echo_aware').reason)
      .toBe('semantic_risk');
  });

  it('selects bounded character-voice tokens and enforces cooldown', () => {
    expect(decideAssistantListenerBackchannel('I have been walking through the background carefully,', 'minimal', 4_000, 10_000, 0, 'echo_aware'))
      .toEqual({ allowed: true, token: 'mhm', reason: 'minimal' });
    expect(decideAssistantListenerBackchannel('I have been walking through the background carefully,', 'natural', 4_000, 12_000, 10_000, 'echo_aware').reason)
      .toBe('cooldown');
  });

  it('derives bounded cadence from the active policy frequency', () => {
    const quiet = resolveBackchannelCadence(0.05);
    const natural = resolveBackchannelCadence(0.16);
    const engaged = resolveBackchannelCadence(0.24);

    expect(quiet.speechMs).toBeGreaterThan(natural.speechMs);
    expect(quiet.cooldownMs).toBeGreaterThan(natural.cooldownMs);
    expect(engaged.speechMs).toBeLessThan(natural.speechMs);
    expect(engaged.cooldownMs).toBeLessThan(natural.cooldownMs);
    expect(resolveBackchannelCadence(0).enabled).toBe(false);
  });

  it('recovers partial speech from the authoritative conversation store', () => {
    liveConversationStore.dispatch({ type: 'transcript_partial', text: 'I am still explaining this part,' });
    expect(resolveBackchannelTranscript(undefined)).toBe('I am still explaining this part,');
    expect(resolveBackchannelTranscript('explicit')).toBe('explicit');
  });
});
