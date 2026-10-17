import {
  hasVoiceCueSamples,
  resolveCueSamples,
  type CueSampleSource,
  type LiveVoiceCueId,
} from './live-voice-cue-bank';
import { ensureLiveVoiceCuePack } from './live-voice-cue-pack-loader';
import { readLiveVoiceHumanizationFlags } from './live-voice-humanization-flags';
import { createCueSegmentId } from './live-voice-playback-contract';
import { ASSISTANT_LIVE_VOICE_STOP_EVENT, ASSISTANT_VOICE_INTERRUPT_EVENT, emitOmnixEvent, LIVE_VOICE_CUE_SEGMENT_EVENT, LIVE_VOICE_CUE_SKIPPED_EVENT } from '../../../events/bus';

const VOICE_SETTINGS_KEY = 'omnix.chatbot.assistantSettings';

type ActiveCue = {
  source: AudioBufferSourceNode;
  segmentId: string;
  cueId: LiveVoiceCueId;
  variantId: string;
  cueSource: CueSampleSource;
  voiceId: string | null;
  terminal: boolean;
};

let context: AudioContext | null = null;
let activeCue: ActiveCue | null = null;
let cueSequence = 0;
let listenersInstalled = false;

export async function playLowLatencyVoiceCue(
  cueId: LiveVoiceCueId,
  variantId: string,
  gainValue = 0.75,
  options: {
    voiceId?: string | null;
    allowProceduralFallback?: boolean;
  } = {},
): Promise<boolean> {
  if (typeof window === 'undefined') return false;
  installCueCancellationListeners();
  const AudioContextCtor = window.AudioContext
    ?? (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) return false;
  if (!context || context.state === 'closed') {
    context = new AudioContextCtor({ latencyHint: 'interactive' });
  }
  if (context.state !== 'running') await context.resume().catch(() => undefined);
  if (context.state !== 'running') return false;

  stopLowLatencyVoiceCue('superseded');
  const flags = readLiveVoiceHumanizationFlags();
  const voiceId = options.voiceId ?? selectedVoiceId();
  const allowProceduralFallback = options.allowProceduralFallback
    ?? flags.proceduralCueFallback;
  if (voiceId && !hasVoiceCueSamples(voiceId, cueId, variantId)) {
    await ensureLiveVoiceCuePack(voiceId);
  }
  const resolution = resolveCueSamples(cueId, variantId, context.sampleRate, {
    voiceId,
    allowProceduralFallback,
  });
  if (!resolution) {
    emitOmnixEvent(LIVE_VOICE_CUE_SKIPPED_EVENT, {
        cue_id: cueId,
        variant_id: variantId,
        voice_id: voiceId,
        reason: 'voice_asset_unavailable',
        procedural_fallback_allowed: allowProceduralFallback,
      });
    return false;
  }

  const buffer = context.createBuffer(1, resolution.samples.length, context.sampleRate);
  buffer.copyToChannel(new Float32Array(resolution.samples), 0);
  const source = context.createBufferSource();
  const gain = context.createGain();
  gain.gain.value = Math.max(0, Math.min(1, gainValue));
  source.buffer = buffer;
  source.connect(gain);
  gain.connect(context.destination);
  const segmentId = createCueSegmentId('standalone', cueId, cueSequence++);
  const active: ActiveCue = {
    source,
    segmentId,
    cueId,
    variantId,
    cueSource: resolution.source,
    voiceId: resolution.voiceId,
    terminal: false,
  };
  activeCue = active;
  dispatchCueLifecycle('segment_started', active);
  return new Promise<boolean>((resolve) => {
    source.addEventListener('ended', () => {
      if (!active.terminal) {
        active.terminal = true;
        dispatchCueLifecycle('segment_completed', active);
      }
      if (activeCue === active) activeCue = null;
      resolve(true);
    }, { once: true });
    source.start();
  });
}

export function stopLowLatencyVoiceCue(reason = 'stopped'): void {
  const active = activeCue;
  activeCue = null;
  if (!active) return;
  if (!active.terminal) {
    active.terminal = true;
    dispatchCueLifecycle('segment_interrupted', active, reason);
  }
  try { active.source.stop(); } catch { /* already stopped */ }
  try { active.source.disconnect(); } catch { /* already disconnected */ }
}

export async function closeLowLatencyVoiceCuePlayer(): Promise<void> {
  stopLowLatencyVoiceCue('player_closed');
  const current = context;
  context = null;
  await current?.close().catch(() => undefined);
}

function installCueCancellationListeners(): void {
  if (listenersInstalled || typeof window === 'undefined') return;
  listenersInstalled = true;
  window.addEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, () => stopLowLatencyVoiceCue('voice_interrupt'));
  window.addEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, () => stopLowLatencyVoiceCue('live_call_stop'));
}

function dispatchCueLifecycle(
  type: 'segment_started' | 'segment_completed' | 'segment_interrupted',
  active: ActiveCue,
  reason?: string,
): void {
  emitOmnixEvent(LIVE_VOICE_CUE_SEGMENT_EVENT, {
      type,
      segment_id: active.segmentId,
      segment_kind: 'cue',
      cue_id: active.cueId,
      variant_id: active.variantId,
      cue_source: active.cueSource,
      voice_id: active.voiceId,
      semantic_speech_samples: 0,
      reason: reason ?? null,
    });
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
