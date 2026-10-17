import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  clearVoiceCueAssets,
  hasVoiceCueSamples,
  resolveCueSamples,
} from './live-voice-cue-bank';
import { initializeLiveVoiceCueAssetBridge } from './live-voice-cue-asset-bridge';
import { VOICE_CUE_ASSETS_CLEAR_EVENT, VOICE_CUE_ASSETS_READY_EVENT, VOICE_CUE_ASSETS_REGISTERED_EVENT } from '../../../events/bus';

let cleanup: (() => void) | null = null;

afterEach(() => {
  cleanup?.();
  cleanup = null;
  clearVoiceCueAssets();
});

describe('live voice cue asset bridge', () => {
  it('registers bulk float and PCM16 voice assets with content-free diagnostics', () => {
    cleanup = initializeLiveVoiceCueAssetBridge();
    const registered = vi.fn();
    window.addEventListener(VOICE_CUE_ASSETS_REGISTERED_EVENT, registered);

    window.dispatchEvent(new CustomEvent(VOICE_CUE_ASSETS_READY_EVENT, {
      detail: {
        assets: [
          {
            voiceId: 'Jinx',
            cueId: 'hmm',
            variantId: 'hmm-v1',
            samples: new Float32Array([0, 0.25, -0.25, 0]),
            sampleRate: 24_000,
          },
          {
            voiceId: 'Jinx',
            cueId: 'inhale',
            variantId: 'inhale-v1',
            samples: new Int16Array([0, 16_384, -16_384, 0]),
            sampleRate: 24_000,
          },
        ],
      },
    }));

    expect(hasVoiceCueSamples('Jinx', 'hmm', 'hmm-v1')).toBe(true);
    expect(hasVoiceCueSamples('Jinx', 'inhale', 'inhale-v1')).toBe(true);
    expect(resolveCueSamples('inhale', 'inhale-v1', 24_000, {
      voiceId: 'Jinx',
      allowProceduralFallback: false,
    })?.samples).toEqual(new Float32Array([0, 0.5, -0.5, 0]));
    expect(registered).toHaveBeenCalledTimes(1);
    expect((registered.mock.calls[0]?.[0] as CustomEvent).detail).toEqual({
      received_count: 2,
      registered_count: 2,
      rejected_count: 0,
      failures: [],
    });
    window.removeEventListener(VOICE_CUE_ASSETS_REGISTERED_EVENT, registered);
  });

  it('rejects malformed assets without retaining sample content in diagnostics', () => {
    cleanup = initializeLiveVoiceCueAssetBridge();
    const registered = vi.fn();
    window.addEventListener(VOICE_CUE_ASSETS_REGISTERED_EVENT, registered);

    window.dispatchEvent(new CustomEvent(VOICE_CUE_ASSETS_READY_EVENT, {
      detail: {
        assets: [
          {
            voiceId: '',
            cueId: 'hmm',
            variantId: 'hmm-v1',
            samples: new Float32Array([0.1]),
            sampleRate: 24_000,
          },
          {
            voiceId: 'Jinx',
            cueId: 'hmm',
            variantId: 'hmm-v2',
            samples: new Float32Array([Number.NaN]),
            sampleRate: 24_000,
          },
          {
            voiceId: 'Jinx',
            cueId: 'hmm',
            variantId: 'hmm-v3',
            samples: new ArrayBuffer(8),
            sampleRate: 24_000,
          },
        ],
      },
    }));

    const detail = (registered.mock.calls[0]?.[0] as CustomEvent).detail as {
      received_count: number;
      registered_count: number;
      rejected_count: number;
      failures: Array<{ index: number; reason: string }>;
    };
    expect(detail).toEqual({
      received_count: 3,
      registered_count: 0,
      rejected_count: 3,
      failures: [
        { index: 0, reason: 'voice_id_required' },
        { index: 1, reason: 'invalid_samples' },
        { index: 2, reason: 'invalid_samples' },
      ],
    });
    expect(Object.keys(detail).sort()).toEqual([
      'failures',
      'received_count',
      'registered_count',
      'rejected_count',
    ]);
    expect(detail.failures.every((failure) => (
      Object.keys(failure).length === 2
      && Object.hasOwn(failure, 'index')
      && Object.hasOwn(failure, 'reason')
    ))).toBe(true);
    window.removeEventListener(VOICE_CUE_ASSETS_REGISTERED_EVENT, registered);
  });

  it('clears one voice or the complete ephemeral asset registry', () => {
    cleanup = initializeLiveVoiceCueAssetBridge();
    for (const voiceId of ['Jinx', 'Maya']) {
      window.dispatchEvent(new CustomEvent(VOICE_CUE_ASSETS_READY_EVENT, {
        detail: {
          voiceId,
          cueId: 'mhm',
          variantId: 'mhm-v1',
          samples: [0, 0.1, -0.1, 0],
          sampleRate: 24_000,
        },
      }));
    }

    window.dispatchEvent(new CustomEvent(VOICE_CUE_ASSETS_CLEAR_EVENT, {
      detail: { voiceId: 'Jinx' },
    }));
    expect(hasVoiceCueSamples('Jinx', 'mhm', 'mhm-v1')).toBe(false);
    expect(hasVoiceCueSamples('Maya', 'mhm', 'mhm-v1')).toBe(true);

    window.dispatchEvent(new CustomEvent(VOICE_CUE_ASSETS_CLEAR_EVENT));
    expect(hasVoiceCueSamples('Maya', 'mhm', 'mhm-v1')).toBe(false);
  });

  it('does not double-install listeners', () => {
    const firstCleanup = initializeLiveVoiceCueAssetBridge();
    const secondCleanup = initializeLiveVoiceCueAssetBridge();
    const registered = vi.fn();
    window.addEventListener(VOICE_CUE_ASSETS_REGISTERED_EVENT, registered);

    window.dispatchEvent(new CustomEvent(VOICE_CUE_ASSETS_READY_EVENT, {
      detail: {
        voiceId: 'Jinx',
        cueId: 'mhm',
        variantId: 'mhm-v1',
        samples: [0, 0.1, -0.1, 0],
        sampleRate: 24_000,
      },
    }));

    expect(registered).toHaveBeenCalledTimes(1);
    firstCleanup();
    secondCleanup();
    cleanup = null;
    window.removeEventListener(VOICE_CUE_ASSETS_REGISTERED_EVENT, registered);
  });
});
