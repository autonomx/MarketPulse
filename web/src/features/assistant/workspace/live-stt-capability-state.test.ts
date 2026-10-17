import { afterEach, describe, expect, it } from 'vitest';

import {
  currentLiveSttCapabilities,
  liveSttUsesAuthoritativeEou,
  liveSttUsesFinalOnlyEndpointing,
  resetLiveSttCapabilityState,
} from './live-stt-capability-state';

afterEach(() => {
  resetLiveSttCapabilityState();
});

describe('live STT capability state', () => {
  it('learns the negotiated provider capabilities from live voice perf events', () => {
    window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
      detail: {
        stage: 'stt_negotiated',
        provider: 'parakeet',
        capabilities: ['segmented_audio', 'authoritative_final', 'result_replay'],
      },
    }));

    expect(currentLiveSttCapabilities()).toEqual({
      provider: 'parakeet',
      capabilities: ['authoritative_final', 'result_replay', 'segmented_audio'],
      negotiated: true,
    });
    expect(liveSttUsesFinalOnlyEndpointing()).toBe(true);
    expect(liveSttUsesAuthoritativeEou()).toBe(false);
  });

  it('recognizes the Nemotron plus Parakeet EOU split contract', () => {
    window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
      detail: {
        stage: 'stt_negotiated',
        provider: 'nemotron_parakeet_eou',
        capabilities: [
          'segmented_audio',
          'authoritative_final',
          'result_replay',
          'partial_transcripts',
          'authoritative_eou',
        ],
      },
    }));

    expect(liveSttUsesAuthoritativeEou()).toBe(true);
    expect(liveSttUsesFinalOnlyEndpointing()).toBe(false);
  });

  it('clears stale capabilities as soon as a new STT authority is selected', () => {
    window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
      detail: {
        stage: 'stt_negotiated',
        provider: 'parakeet',
        capabilities: ['segmented_audio', 'authoritative_final', 'result_replay'],
      },
    }));
    expect(liveSttUsesFinalOnlyEndpointing()).toBe(true);

    window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
      detail: {
        stage: 'stt_authority_selected',
        selectedProvider: 'configured_stt',
      },
    }));

    expect(currentLiveSttCapabilities()).toEqual({
      provider: null,
      capabilities: [],
      negotiated: false,
    });
    expect(liveSttUsesFinalOnlyEndpointing()).toBe(false);
    expect(liveSttUsesAuthoritativeEou()).toBe(false);
  });

  it('does not classify semantic or unknown capability sets as final-only', () => {
    window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
      detail: {
        stage: 'stt_negotiated',
        provider: 'streaming-test-provider',
        capabilities: [
          'segmented_audio',
          'authoritative_final',
          'continuous_words',
          'semantic_endpointing',
        ],
      },
    }));
    expect(liveSttUsesFinalOnlyEndpointing()).toBe(false);

    window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
      detail: {
        stage: 'stt_negotiated',
        provider: 'future-provider',
        capabilities: ['segmented_audio', 'authoritative_final', 'partial_transcripts'],
      },
    }));
    expect(liveSttUsesFinalOnlyEndpointing()).toBe(false);
  });

  it('ignores unrelated performance events', () => {
    window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
      detail: {
        stage: 'stt_final_received',
        provider: 'parakeet',
        capabilities: ['segmented_audio', 'authoritative_final', 'result_replay'],
      },
    }));

    expect(currentLiveSttCapabilities()).toEqual({
      provider: null,
      capabilities: [],
      negotiated: false,
    });
    expect(liveSttUsesFinalOnlyEndpointing()).toBe(false);
    expect(liveSttUsesAuthoritativeEou()).toBe(false);
  });
});
