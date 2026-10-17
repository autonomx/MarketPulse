import { describe, expect, it } from 'vitest';

import {
  advanceDeliveryLedger,
  appendDeliveryPhrase,
  createLiveVoiceDeliveryLedger,
  handleDeliveryDiagnostic,
} from './live-voice-delivery-ledger';

describe('live voice delivery ledger', () => {
  it('keeps visual and conversational delivery separate', () => {
    const ledger = createLiveVoiceDeliveryLedger();
    appendDeliveryPhrase(ledger, 0, 'First delivered phrase.');
    appendDeliveryPhrase(ledger, 1, 'Second generated phrase.');

    handleDeliveryDiagnostic(ledger, 'phrase_buffered', {
      phrase_index: 0,
      playback_samples: 24_000,
      sample_rate: 24_000,
    });
    handleDeliveryDiagnostic(ledger, 'phrase_buffered', {
      phrase_index: 1,
      playback_samples: 24_000,
      sample_rate: 24_000,
    });

    expect(advanceDeliveryLedger(ledger, 1)).toBe(true);
    expect(ledger.visualDeliveredTextEnd).toBe(23);
    expect(ledger.contextDeliveredTextEnd).toBe(0);
    expect(ledger.activePhraseIndex).toBe(0);

    expect(advanceDeliveryLedger(ledger, 24_000)).toBe(true);
    expect(ledger.contextDeliveredTextEnd).toBe(23);
    expect(ledger.audioDeliveredPhraseCount).toBe(1);

    expect(advanceDeliveryLedger(ledger, 24_001)).toBe(true);
    expect(ledger.visualDeliveredTextEnd).toBe(48);
    expect(ledger.contextDeliveredTextEnd).toBe(23);
    expect(ledger.activePhraseIndex).toBe(1);
  });

  it('derives phrase sample ranges in the explicit playback sample domain', () => {
    const ledger = createLiveVoiceDeliveryLedger(48_000);
    appendDeliveryPhrase(ledger, 0, 'One.');
    appendDeliveryPhrase(ledger, 1, 'Two.');

    handleDeliveryDiagnostic(ledger, 'phrase_buffered', { phrase_index: 0, audio_ms: 500 });
    handleDeliveryDiagnostic(ledger, 'phrase_buffered', { phrase_index: 1, audio_ms: 250 });

    expect(ledger.phrases[0]?.audioSampleStart).toBe(0);
    expect(ledger.phrases[0]?.audioSampleEnd).toBe(24_000);
    expect(ledger.phrases[1]?.audioSampleStart).toBe(24_000);
    expect(ledger.phrases[1]?.audioSampleEnd).toBe(36_000);
  });

  it('advances only from canonical semantic speech samples', () => {
    const ledger = createLiveVoiceDeliveryLedger();
    appendDeliveryPhrase(ledger, 0, 'Canonical speech.');
    handleDeliveryDiagnostic(ledger, 'phrase_buffered', {
      phrase_index: 0,
      playback_samples: 1_000,
      sample_rate: 24_000,
    });

    handleDeliveryDiagnostic(ledger, 'worklet_render_progress', {
      sample_rate: 24_000,
      render_clock_samples: 5_000,
      segment_timeline_samples: 3_000,
      semantic_speech_samples: 0,
      played_samples: 3_000,
    });

    expect(ledger.semanticSpeechSamples).toBe(0);
    expect(ledger.visualDeliveredTextEnd).toBe(0);

    handleDeliveryDiagnostic(ledger, 'worklet_render_progress', {
      sample_rate: 24_000,
      render_clock_samples: 6_000,
      segment_timeline_samples: 4_000,
      semantic_speech_samples: 1,
      played_samples: 4_000,
    });

    expect(ledger.semanticSpeechSamples).toBe(1);
    expect(ledger.visualDeliveredTextEnd).toBe('Canonical speech.'.length);
  });
});
