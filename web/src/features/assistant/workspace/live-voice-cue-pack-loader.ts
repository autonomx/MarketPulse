import type { LiveVoiceCueId } from './live-voice-cue-bank';
import { ApiError } from '../../../api/errors';
import { fetchBytes } from '../../../api/transport';
import { ASSISTANT_LIVE_VOICE_CALL_START_EVENT, emitOmnixEvent, VOICE_CUE_ASSETS_CLEAR_EVENT, VOICE_CUE_ASSETS_READY_EVENT, VOICE_CUE_PACK_STATUS_EVENT } from '../../../events/bus';
import { api } from '../api/gateway';

const VOICE_SETTINGS_KEY = 'omnix.chatbot.assistantSettings';
const MAX_ASSETS_PER_PACK = 32;
const MAX_ASSET_BYTES = 2_000_000;
const SUPPORTED_CUES = new Set<LiveVoiceCueId>(['mhm', 'hmm', 'inhale', 'amused_exhale']);

export type LiveVoiceCuePackLoadResult = {
  voiceId: string;
  available: boolean;
  loadedCount: number;
  skippedCount: number;
  reason: string;
};

type CueManifestAsset = {
  cue_id?: unknown;
  variant_id?: unknown;
  url?: unknown;
  size_bytes?: unknown;
  sha256?: unknown;
};

type CueManifest = {
  schema_version?: unknown;
  voice_id?: unknown;
  available?: unknown;
  assets?: unknown;
};

export type DecodedCueAsset = {
  voiceId: string;
  cueId: LiveVoiceCueId;
  variantId: string;
  samples: Float32Array;
  sampleRate: number;
};

const inFlight = new Map<string, Promise<LiveVoiceCuePackLoadResult>>();
const loadedFingerprints = new Map<string, string>();
let initialized = false;

export function initializeLiveVoiceCuePackLoader(): () => void {
  if (initialized || typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;
  initialized = true;
  const preloadSelected = () => {
    const voiceId = selectedVoiceId();
    if (voiceId) void ensureLiveVoiceCuePack(voiceId);
  };
  const handleChange = (event: Event) => {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement) || target.getAttribute('aria-label') !== 'Cloned voice') return;
    preloadSelected();
  };
  const handleStorage = (event: StorageEvent) => {
    if (event.key === VOICE_SETTINGS_KEY) preloadSelected();
  };
  window.addEventListener(ASSISTANT_LIVE_VOICE_CALL_START_EVENT, preloadSelected);
  window.addEventListener('storage', handleStorage);
  document.addEventListener('change', handleChange);
  preloadSelected();
  return () => {
    window.removeEventListener(ASSISTANT_LIVE_VOICE_CALL_START_EVENT, preloadSelected);
    window.removeEventListener('storage', handleStorage);
    document.removeEventListener('change', handleChange);
    initialized = false;
  };
}

export function ensureLiveVoiceCuePack(voiceId: string | null | undefined): Promise<LiveVoiceCuePackLoadResult> {
  const normalized = String(voiceId ?? '').trim();
  if (!normalized) return Promise.resolve(result('', false, 0, 0, 'voice_id_required'));
  const current = inFlight.get(normalized);
  if (current) return current;
  const request = loadCuePack(normalized).finally(() => inFlight.delete(normalized));
  inFlight.set(normalized, request);
  return request;
}

export function resetLiveVoiceCuePackLoaderState(): void {
  inFlight.clear();
  loadedFingerprints.clear();
}

async function loadCuePack(voiceId: string): Promise<LiveVoiceCuePackLoadResult> {
  try {
    const { data, response } = await api.GET('/api/voice/cues/{voice_id}/manifest', { params: { path: { voice_id: voiceId } } });
    if (!response.ok || !data) return publish(result(voiceId, false, 0, 0, `manifest_http_${response.status}`));
    const manifest = data as unknown as CueManifest;
    const assets = normalizeManifest(manifest, voiceId);
    if (!assets.length) {
      emitOmnixEvent(VOICE_CUE_ASSETS_CLEAR_EVENT, { voiceId });
      loadedFingerprints.delete(voiceId);
      return publish(result(voiceId, false, 0, 0, 'pack_unavailable'));
    }
    const fingerprint = assets.map((asset) => `${asset.variantId}:${asset.sha256}`).join('|');
    if (loadedFingerprints.get(voiceId) === fingerprint) {
      return publish(result(voiceId, true, 0, assets.length, 'already_loaded'));
    }

    const context = createDecodeContext();
    if (!context) return publish(result(voiceId, false, 0, assets.length, 'audio_context_unavailable'));
    const decoded: DecodedCueAsset[] = [];
    let skipped = 0;
    try {
      for (const asset of assets) {
        const cue = await fetchAndDecodeAsset(context, voiceId, asset).catch(() => null);
        if (cue) decoded.push(cue);
        else skipped += 1;
      }
    } finally {
      await context.close().catch(() => undefined);
    }
    if (!decoded.length) return publish(result(voiceId, false, 0, skipped, 'decode_failed'));
    emitOmnixEvent(VOICE_CUE_ASSETS_READY_EVENT, { assets: decoded });
    loadedFingerprints.set(voiceId, fingerprint);
    return publish(result(voiceId, true, decoded.length, skipped, 'loaded'));
  } catch {
    return publish(result(voiceId, false, 0, 0, 'manifest_failed'));
  }
}

function normalizeManifest(manifest: CueManifest, voiceId: string): Array<{
  cueId: LiveVoiceCueId;
  variantId: string;
  url: string;
  sizeBytes: number;
  sha256: string;
}> {
  if (manifest.schema_version !== 1 || manifest.voice_id !== voiceId || manifest.available !== true) return [];
  const source = Array.isArray(manifest.assets) ? manifest.assets.slice(0, MAX_ASSETS_PER_PACK) : [];
  const assets: Array<{ cueId: LiveVoiceCueId; variantId: string; url: string; sizeBytes: number; sha256: string }> = [];
  for (const raw of source) {
    const asset = raw as CueManifestAsset;
    const cueId = typeof asset.cue_id === 'string' && SUPPORTED_CUES.has(asset.cue_id as LiveVoiceCueId)
      ? asset.cue_id as LiveVoiceCueId
      : null;
    const variantId = typeof asset.variant_id === 'string' ? asset.variant_id : '';
    const url = typeof asset.url === 'string' ? asset.url : '';
    const sizeBytes = typeof asset.size_bytes === 'number' ? asset.size_bytes : 0;
    const sha256 = typeof asset.sha256 === 'string' ? asset.sha256 : '';
    if (!cueId || !new RegExp(`^${cueId}-v[1-9][0-9]?$`).test(variantId)) continue;
    if (!url.startsWith('/api/voice/cues/') || sizeBytes <= 0 || sizeBytes > MAX_ASSET_BYTES) continue;
    if (!/^[a-f0-9]{64}$/.test(sha256)) continue;
    assets.push({ cueId, variantId, url, sizeBytes, sha256 });
  }
  return assets;
}

async function fetchAndDecodeAsset(
  context: AudioContext,
  voiceId: string,
  asset: { cueId: LiveVoiceCueId; variantId: string; url: string; sizeBytes: number },
): Promise<DecodedCueAsset> {
  const response = await fetchBytes(asset.url, { headers: { Accept: 'audio/wav' } }).catch((error: unknown) => {
    throw error instanceof ApiError ? new Error(`cue_http_${error.status}`) : error;
  });
  const bytes = await response.arrayBuffer();
  if (!bytes.byteLength || bytes.byteLength > MAX_ASSET_BYTES) throw new Error('cue_size_invalid');
  const buffer = await context.decodeAudioData(bytes.slice(0));
  if (!buffer.length || buffer.numberOfChannels < 1) throw new Error('cue_decode_empty');
  return {
    voiceId,
    cueId: asset.cueId,
    variantId: asset.variantId,
    samples: new Float32Array(buffer.getChannelData(0)),
    sampleRate: buffer.sampleRate,
  };
}

function createDecodeContext(): AudioContext | null {
  const Constructor = window.AudioContext
    ?? (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  return Constructor ? new Constructor({ latencyHint: 'interactive' }) : null;
}

function selectedVoiceId(): string | null {
  const liveCallVoice = document.querySelector<HTMLElement>('.assistant-live-card')?.dataset.liveVoiceId?.trim();
  if (liveCallVoice) return liveCallVoice;
  const mounted = document.querySelector<HTMLSelectElement>('select[aria-label="Cloned voice"]')?.value.trim();
  if (mounted) return mounted;
  try {
    const parsed = JSON.parse(window.localStorage.getItem(VOICE_SETTINGS_KEY) || '{}') as { voiceId?: unknown };
    return typeof parsed.voiceId === 'string' && parsed.voiceId.trim() ? parsed.voiceId.trim() : null;
  } catch {
    return null;
  }
}

function result(
  voiceId: string,
  available: boolean,
  loadedCount: number,
  skippedCount: number,
  reason: string,
): LiveVoiceCuePackLoadResult {
  return { voiceId, available, loadedCount, skippedCount, reason };
}

function publish(value: LiveVoiceCuePackLoadResult): LiveVoiceCuePackLoadResult {
  emitOmnixEvent(VOICE_CUE_PACK_STATUS_EVENT, value);
  return value;
}
