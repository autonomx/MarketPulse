import {
  clearVoiceCueAssets,
  registerVoiceCueSamples,
  unregisterVoiceCueSamples,
  type LiveVoiceCueId,
} from './live-voice-cue-bank';
import { emitOmnixEvent, VOICE_CUE_ASSETS_CLEAR_EVENT, VOICE_CUE_ASSETS_READY_EVENT, VOICE_CUE_ASSETS_REGISTERED_EVENT } from '../../../events/bus';


const VALID_CUE_IDS = new Set<LiveVoiceCueId>(['mhm', 'hmm', 'inhale', 'amused_exhale']);
const MIN_SAMPLE_RATE = 8_000;
const MAX_SAMPLE_RATE = 192_000;
const MAX_CUE_SAMPLES = 10_000_000;

type CueSamplePayload = Float32Array | Int16Array | ArrayBuffer | number[];

export type VoiceCueAssetPayload = {
  voiceId: string;
  cueId: LiveVoiceCueId;
  variantId: string;
  samples: CueSamplePayload;
  sampleRate: number;
  sampleFormat?: 'float32' | 'pcm16';
};

export type VoiceCueAssetsReadyDetail =
  | VoiceCueAssetPayload
  | { assets: VoiceCueAssetPayload[] };

export type VoiceCueAssetsClearDetail = {
  voiceId?: string;
  cueId?: LiveVoiceCueId;
  variantId?: string;
};

export type RegistrationFailure = {
  index: number;
  reason: string;
};

let installed = false;

export function initializeLiveVoiceCueAssetBridge(): () => void {
  if (typeof window === 'undefined' || installed) return () => undefined;
  installed = true;

  const handleReady = (event: Event): void => {
    const detail = (event as CustomEvent<VoiceCueAssetsReadyDetail>).detail;
    const assets = normalizeAssetList(detail);
    const failures: RegistrationFailure[] = [];
    let registered = 0;

    assets.forEach((asset, index) => {
      const normalized = normalizeAsset(asset);
      if (!normalized.ok) {
        failures.push({ index, reason: normalized.reason });
        return;
      }
      if (!registerVoiceCueSamples(normalized.asset)) {
        failures.push({ index, reason: 'registration_rejected' });
        return;
      }
      registered += 1;
    });

    emitOmnixEvent(VOICE_CUE_ASSETS_REGISTERED_EVENT, {
        received_count: assets.length,
        registered_count: registered,
        rejected_count: failures.length,
        failures,
      });
  };

  const handleClear = (event: Event): void => {
    const detail = (event as CustomEvent<VoiceCueAssetsClearDetail>).detail ?? {};
    const voiceId = typeof detail.voiceId === 'string' ? detail.voiceId.trim() : '';
    if (!voiceId) {
      clearVoiceCueAssets();
      return;
    }
    unregisterVoiceCueSamples(voiceId, detail.cueId, detail.variantId);
  };

  window.addEventListener(VOICE_CUE_ASSETS_READY_EVENT, handleReady);
  window.addEventListener(VOICE_CUE_ASSETS_CLEAR_EVENT, handleClear);

  return () => {
    window.removeEventListener(VOICE_CUE_ASSETS_READY_EVENT, handleReady);
    window.removeEventListener(VOICE_CUE_ASSETS_CLEAR_EVENT, handleClear);
    installed = false;
  };
}

function normalizeAssetList(detail: VoiceCueAssetsReadyDetail | undefined): VoiceCueAssetPayload[] {
  if (!detail || typeof detail !== 'object') return [];
  if ('assets' in detail) return Array.isArray(detail.assets) ? detail.assets : [];
  return [detail];
}

function normalizeAsset(asset: VoiceCueAssetPayload):
  | {
      ok: true;
      asset: {
        voiceId: string;
        cueId: LiveVoiceCueId;
        variantId: string;
        samples: Float32Array;
        sampleRate: number;
      };
    }
  | { ok: false; reason: string } {
  if (!asset || typeof asset !== 'object') return { ok: false, reason: 'invalid_asset' };
  const voiceId = typeof asset.voiceId === 'string' ? asset.voiceId.trim() : '';
  const variantId = typeof asset.variantId === 'string' ? asset.variantId.trim() : '';
  if (!voiceId) return { ok: false, reason: 'voice_id_required' };
  if (!VALID_CUE_IDS.has(asset.cueId)) return { ok: false, reason: 'unsupported_cue_id' };
  if (!variantId) return { ok: false, reason: 'variant_id_required' };
  if (
    !Number.isFinite(asset.sampleRate)
    || asset.sampleRate < MIN_SAMPLE_RATE
    || asset.sampleRate > MAX_SAMPLE_RATE
  ) return { ok: false, reason: 'invalid_sample_rate' };

  const samples = normalizeSamples(asset.samples, asset.sampleFormat);
  if (!samples) return { ok: false, reason: 'invalid_samples' };
  if (samples.length <= 0) return { ok: false, reason: 'empty_samples' };
  if (samples.length > MAX_CUE_SAMPLES) return { ok: false, reason: 'samples_too_large' };

  return {
    ok: true,
    asset: {
      voiceId,
      cueId: asset.cueId,
      variantId,
      samples,
      sampleRate: Math.round(asset.sampleRate),
    },
  };
}

function normalizeSamples(
  payload: CueSamplePayload,
  format: VoiceCueAssetPayload['sampleFormat'],
): Float32Array | null {
  if (payload instanceof Float32Array) return sanitizeFloatSamples(payload);
  if (payload instanceof Int16Array) return pcm16ToFloat32(payload);
  if (payload instanceof ArrayBuffer) {
    if (format === 'pcm16' && payload.byteLength % 2 === 0) {
      return pcm16ToFloat32(new Int16Array(payload.slice(0)));
    }
    if (format === 'float32' && payload.byteLength % 4 === 0) {
      return sanitizeFloatSamples(new Float32Array(payload.slice(0)));
    }
    return null;
  }
  if (Array.isArray(payload)) return sanitizeFloatSamples(Float32Array.from(payload));
  return null;
}

function sanitizeFloatSamples(input: Float32Array): Float32Array | null {
  const output = new Float32Array(input.length);
  for (let index = 0; index < input.length; index += 1) {
    const value = input[index];
    if (!Number.isFinite(value)) return null;
    output[index] = Math.max(-1, Math.min(1, value));
  }
  return output;
}

function pcm16ToFloat32(input: Int16Array): Float32Array {
  const output = new Float32Array(input.length);
  for (let index = 0; index < input.length; index += 1) {
    output[index] = input[index] / 32768;
  }
  return output;
}
