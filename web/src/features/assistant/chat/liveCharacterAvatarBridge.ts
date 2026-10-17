 
import { useSyncExternalStore } from 'react';
import type { CharacterAvatarPack, CharacterLiveCallRuntime } from './characterClient';
import { liveCallPresentationStore } from '../workspace';
import './liveCharacterAvatarBridge.css';
import { CHARACTER_AVATAR_FRAME_EVENT, CHARACTER_AVATAR_PCM_EVENT, CHARACTER_AVATAR_RUNTIME_EVENT, emitOmnixEvent, LIVE_CALL_DIAGNOSTIC_EVENT } from '../../../events/bus';
import { startTicker } from '../../../shared/timers';

export type AvatarMouthFrame = 'closed' | 'small' | 'medium' | 'wide';
export type AvatarPresentationState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';

let bridgeInstalled = false;
const AUDIO_ELEMENT_FRAME_MS = 50;
const AUDIO_ELEMENT_FFT_SIZE = 1_024;
const AUDIO_BUFFER_WINDOW_MS = 60;
const ENVELOPE_FRAME_ALIASES: Record<AvatarMouthFrame, string[]> = {
  closed: ['closed', 'silence', 'MBP'],
  small: ['small', 'U', 'WQ', 'FV', 'other'],
  medium: ['medium', 'E', 'L', 'other', 'U'],
  wide: ['wide', 'A', 'O', 'other', 'E'],
};

type AudioMonitorWindow = Window & typeof globalThis & {
  AudioContext?: typeof AudioContext;
  webkitAudioContext?: typeof AudioContext;
};

type CapturableAudioElement = HTMLAudioElement & {
  captureStream?: () => MediaStream;
  mozCaptureStream?: () => MediaStream;
};

type PatchedCreateBufferSource = AudioContext['createBufferSource'];
// createBufferSource implementations this bridge already wraps.
const monitoredCreators = new WeakSet<PatchedCreateBufferSource>();

type LiveCallDiagnosticDetail = {
  source?: string;
  event?: string;
  details?: Record<string, unknown>;
};

let currentRuntime: CharacterLiveCallRuntime | null = null;
let currentMouthFrame: AvatarMouthFrame = 'closed';
let nextAudioFrameAt = 0;
let blinkClosed = false;
let blinkTimer: ReturnType<typeof setTimeout> | null = null;
const audioElementStops = new WeakMap<HTMLAudioElement, () => void>();

/** The live avatar's runtime and current mouth and blink frames (WP-9.4: components render them). */
export type LiveAvatarState = {
  runtime: CharacterLiveCallRuntime | null;
  mouthFrame: AvatarMouthFrame;
  blinkClosed: boolean;
};

let avatarState: LiveAvatarState = { runtime: null, mouthFrame: 'closed', blinkClosed: false };
const avatarListeners = new Set<() => void>();

function publishAvatarState(): void {
  if (avatarState.runtime === currentRuntime && avatarState.mouthFrame === currentMouthFrame && avatarState.blinkClosed === blinkClosed) return;
  avatarState = { runtime: currentRuntime, mouthFrame: currentMouthFrame, blinkClosed };
  avatarListeners.forEach((listener) => listener());
}

export const liveAvatarStore = {
  getState: (): LiveAvatarState => avatarState,
  subscribe(listener: () => void): () => void {
    avatarListeners.add(listener);
    return () => avatarListeners.delete(listener);
  },
};

export function useLiveAvatar(): LiveAvatarState {
  return useSyncExternalStore(liveAvatarStore.subscribe, liveAvatarStore.getState, liveAvatarStore.getState);
}

export function publishCharacterAvatarRuntime(runtime: CharacterLiveCallRuntime | null): void {
  currentRuntime = runtime;
  currentMouthFrame = 'closed';
  nextAudioFrameAt = 0;
  blinkClosed = false;
  scheduleBlink();
  publishAvatarState();
  if (typeof window !== 'undefined') {
    emitOmnixEvent(CHARACTER_AVATAR_RUNTIME_EVENT, runtime);
  }
}

/** Apply a newly selected pack to the runtime currently driving the live host. */
export function applyAvatarPackToCurrentRuntime(
  characterId: string,
  avatarPack: CharacterAvatarPack | null,
): boolean {
  if (!currentRuntime || currentRuntime.character_id !== characterId) return false;
  publishCharacterAvatarRuntime({ ...currentRuntime, avatar_pack: avatarPack });
  return true;
}

export function mouthFrameForRms(rms: number): AvatarMouthFrame {
  if (!Number.isFinite(rms) || rms < 0.015) return 'closed';
  if (rms < 0.035) return 'small';
  if (rms < 0.075) return 'medium';
  return 'wide';
}

export function floatPcmMouthFrame(samples: Float32Array): AvatarMouthFrame {
  if (!samples.length) return 'closed';
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  return mouthFrameForRms(Math.sqrt(sum / samples.length));
}

export function floatPcmMouthTimeline(
  samples: Float32Array,
  sampleRate: number,
  windowMs = AUDIO_BUFFER_WINDOW_MS,
): Array<{ offsetMs: number; frame: AvatarMouthFrame }> {
  if (!samples.length || !Number.isFinite(sampleRate) || sampleRate <= 0) return [];
  const windowSamples = Math.max(1, Math.floor(sampleRate * (windowMs / 1000)));
  const timeline: Array<{ offsetMs: number; frame: AvatarMouthFrame }> = [];
  let lastFrame: AvatarMouthFrame | null = null;
  for (let start = 0; start < samples.length; start += windowSamples) {
    const frame = floatPcmMouthFrame(samples.subarray(start, Math.min(samples.length, start + windowSamples)));
    if (frame !== lastFrame) {
      timeline.push({ offsetMs: (start / sampleRate) * 1000, frame });
      lastFrame = frame;
    }
  }
  return timeline;
}

export function pcmMouthTimeline(
  samples: Int16Array,
  sampleRate: number,
  windowMs = 60,
): Array<{ offsetMs: number; frame: AvatarMouthFrame }> {
  if (!samples.length || !Number.isFinite(sampleRate) || sampleRate <= 0) return [];
  const windowSamples = Math.max(1, Math.floor(sampleRate * (windowMs / 1000)));
  const timeline: Array<{ offsetMs: number; frame: AvatarMouthFrame }> = [];
  let lastFrame: AvatarMouthFrame | null = null;
  for (let start = 0; start < samples.length; start += windowSamples) {
    const end = Math.min(samples.length, start + windowSamples);
    let sum = 0;
    for (let index = start; index < end; index += 1) {
      const normalized = samples[index] / 32768;
      sum += normalized * normalized;
    }
    const frame = mouthFrameForRms(Math.sqrt(sum / Math.max(1, end - start)));
    if (frame !== lastFrame) {
      timeline.push({ offsetMs: (start / sampleRate) * 1000, frame });
      lastFrame = frame;
    }
  }
  return timeline;
}

export function characterAvatarAssetUrl(assetId: string): string {
  return `/api/assets/${encodeURIComponent(assetId)}/file`;
}

export function avatarMouthAssetForFrame(
  pack: CharacterAvatarPack,
  frame: AvatarMouthFrame,
): string {
  for (const key of ENVELOPE_FRAME_ALIASES[frame]) {
    const assetId = pack.mouth_frames[key];
    if (assetId) return assetId;
  }
  return pack.base_asset_id || '';
}

/** The avatar's state: an open mouth is speech, then the call orb's mode, with a reply in progress as thinking. */
export function avatarPresentationState(
  mouthFrame: AvatarMouthFrame,
  voiceMode: string | undefined,
  thinking: boolean,
): AvatarPresentationState {
  if (mouthFrame !== 'closed' || voiceMode === 'speaking') return 'speaking';
  if (voiceMode === 'error') return 'error';
  if (thinking) return 'thinking';
  if (voiceMode === 'listening') return 'listening';
  return 'idle';
}

/** The sprite frame to show: blink, open mouth, the state's expression, the outfit, then the closed mouth. */
export function avatarFrameAsset(
  pack: CharacterAvatarPack | null | undefined,
  avatar: Pick<LiveAvatarState, 'mouthFrame' | 'blinkClosed'>,
  state: AvatarPresentationState,
): string {
  if (!pack || pack.renderer !== 'sprite') return '';
  if (avatar.blinkClosed && pack.blink_frames.closed) return pack.blink_frames.closed;
  if (avatar.mouthFrame !== 'closed') return avatarMouthAssetForFrame(pack, avatar.mouthFrame);
  if (pack.expression_frames[state]) return pack.expression_frames[state];
  if (pack.active_outfit && pack.outfit_frames[pack.active_outfit]) return pack.outfit_frames[pack.active_outfit];
  return avatarMouthAssetForFrame(pack, 'closed');
}

/** The scene behind a sprite avatar, as a CSS background. */
export function avatarBackgroundImage(pack: CharacterAvatarPack | null | undefined): string {
  const backgroundId = pack?.active_background ? pack.background_asset_ids[pack.active_background] : '';
  return backgroundId
    ? `linear-gradient(rgba(6, 10, 22, 0.12), rgba(6, 10, 22, 0.42)), url("${characterAvatarAssetUrl(backgroundId)}")`
    : '';
}

export function avatarCaption(displayName: string, state: AvatarPresentationState): string {
  return state === 'speaking'
    ? `${displayName} is speaking`
    : state === 'listening'
      ? `${displayName} is listening`
      : state === 'thinking'
        ? `${displayName} is thinking`
        : displayName;
}

/** Lip sync and avatar rendering for Chat; returns a function that removes it. */
export function installLiveCharacterAvatarBridge(): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;
  if (bridgeInstalled) return () => undefined;
  bridgeInstalled = true;

  const handleFrame = (event: Event) => {
    const detail = (event as CustomEvent<{ frame?: AvatarMouthFrame }>).detail;
    if (!detail?.frame) return;
    currentMouthFrame = detail.frame;
    publishAvatarState();
  };
  const handleDiagnostic = (event: Event) => {
    if (currentRuntime?.avatar_pack?.renderer !== 'live2d') return;
    const detail = (event as CustomEvent<LiveCallDiagnosticDetail>).detail;
    if (detail?.source !== 'audio_worklet') return;
    if (detail.event === 'worklet_avatar_frame') {
      const frame = detail.details?.frame;
      if (isAvatarMouthFrame(frame)) dispatchAvatarFrame(frame);
      return;
    }
    if (
      detail.event === 'worklet_idle'
      || detail.event === 'worklet_drained'
      || detail.event === 'worklet_stopped'
      || detail.event === 'worklet_underrun'
    ) {
      dispatchAvatarFrame('closed');
    }
  };
  const handlePcm = (event: Event) => {
    // Live2D live-call lip sync is driven from the AudioWorklet's actual
    // playback envelope. Arrival-time PCM can be hundreds of milliseconds
    // ahead of what is audible when playback is buffered or rebuffering.
    if (currentRuntime?.avatar_pack?.renderer === 'live2d') return;
    const detail = (event as CustomEvent<{
      samples?: Int16Array;
      sampleRate?: number;
      startDelayMs?: number;
    }>).detail;
    if (!(detail?.samples instanceof Int16Array)) return;
    schedulePcmSamples(
      detail.samples,
      Number(detail.sampleRate) || 24_000,
      Number(detail.startDelayMs) || 0,
    );
  };
  window.addEventListener(CHARACTER_AVATAR_FRAME_EVENT, handleFrame);
  window.addEventListener(LIVE_CALL_DIAGNOSTIC_EVENT, handleDiagnostic);
  window.addEventListener(CHARACTER_AVATAR_PCM_EVENT, handlePcm);

  const cleanups = [installAudioElementMonitor(), installAudioBufferSourceMonitor()];
  return () => {
    window.removeEventListener(CHARACTER_AVATAR_FRAME_EVENT, handleFrame);
    window.removeEventListener(LIVE_CALL_DIAGNOSTIC_EVENT, handleDiagnostic);
    window.removeEventListener(CHARACTER_AVATAR_PCM_EVENT, handlePcm);
    cleanups.reverse().forEach((cleanup) => cleanup());
    bridgeInstalled = false;
  };
}

function installAudioElementMonitor(): () => void {
  const prototype = window.HTMLMediaElement?.prototype;
  if (!prototype || typeof prototype.play !== 'function') return () => undefined;
  const originalPlay = prototype.play;
  const patchedPlay = function patchedAvatarAudioPlay(this: HTMLMediaElement): Promise<void> {
    const audio = this instanceof HTMLAudioElement ? this : null;
    if (audio) startAudioElementMonitor(audio);
    const result = originalPlay.call(this);
    if (audio && result && typeof result.catch === 'function') {
      void result.catch(() => stopAudioElementMonitor(audio));
    }
    return result;
  };
  prototype.play = patchedPlay;
  return () => {
    // Restore only our own patch; a later patch on top stays in place.
    if (prototype.play === patchedPlay) prototype.play = originalPlay;
  };
}

function installAudioBufferSourceMonitor(): () => void {
  const restores: Array<() => void> = [];
  const liveWindow = window as AudioMonitorWindow;
  const constructors = [liveWindow.AudioContext, liveWindow.webkitAudioContext]
    .filter((value): value is typeof AudioContext => Boolean(value));
  const patchedPrototypes = new Set<AudioContext>();
  for (const AudioContextCtor of constructors) {
    const prototype = AudioContextCtor.prototype;
    if (patchedPrototypes.has(prototype)) continue;
    patchedPrototypes.add(prototype);
    const originalCreate = prototype.createBufferSource as PatchedCreateBufferSource;
    if (monitoredCreators.has(originalCreate)) continue;
    const patchedCreate = function patchedAvatarBufferSource(this: AudioContext): AudioBufferSourceNode {
      const source = originalCreate.call(this);
      const originalStart = source.start.bind(source);
      source.start = ((when = 0, offset?: number, duration?: number): void => {
        if (typeof duration === 'number') originalStart(when, offset ?? 0, duration);
        else if (typeof offset === 'number') originalStart(when, offset);
        else originalStart(when);
        scheduleAudioBufferFrames(source.buffer, this, when, source.playbackRate.value, offset, duration);
      }) as AudioBufferSourceNode['start'];
      return source;
    } as PatchedCreateBufferSource;
    monitoredCreators.add(patchedCreate);
    prototype.createBufferSource = patchedCreate;
    restores.push(() => {
      if (prototype.createBufferSource === patchedCreate) prototype.createBufferSource = originalCreate;
      monitoredCreators.delete(patchedCreate);
    });
  }
  return () => restores.forEach((restore) => restore());
}

function scheduleAudioBufferFrames(
  buffer: AudioBuffer | null,
  context: AudioContext,
  when: number,
  playbackRate: number,
  offset = 0,
  duration?: number,
): void {
  if (!buffer || !currentRuntime?.avatar_pack || buffer.numberOfChannels < 1) return;
  const rate = Number.isFinite(playbackRate) && playbackRate > 0 ? playbackRate : 1;
  const startFrame = Math.max(0, Math.min(buffer.length, Math.floor(Math.max(0, offset) * buffer.sampleRate)));
  const requestedEnd = typeof duration === 'number'
    ? startFrame + Math.floor(Math.max(0, duration) * buffer.sampleRate)
    : buffer.length;
  const endFrame = Math.max(startFrame, Math.min(buffer.length, requestedEnd));
  const samples = buffer.getChannelData(0).subarray(startFrame, endFrame);
  if (!samples.length) return;
  const startDelayMs = Math.max(0, (when - context.currentTime) * 1000);
  for (const point of floatPcmMouthTimeline(samples, buffer.sampleRate)) {
    window.setTimeout(() => dispatchAvatarFrame(point.frame), startDelayMs + (point.offsetMs / rate));
  }
  const durationMs = samples.length * 1000 / buffer.sampleRate / rate;
  window.setTimeout(() => dispatchAvatarFrame('closed'), startDelayMs + durationMs);
}

function startAudioElementMonitor(audio: HTMLAudioElement): void {
  if (!currentRuntime?.avatar_pack) return;
  stopAudioElementMonitor(audio);
  const liveWindow = window as AudioMonitorWindow;
  const AudioContextCtor = liveWindow.AudioContext ?? liveWindow.webkitAudioContext;
  if (!AudioContextCtor) return;

  let context: AudioContext | null = null;
  let source: AudioNode | null = null;
  let analyser: AnalyserNode | null = null;
  let stopFrames: (() => void) | null = null;
  let stopped = false;

  const stop = (): void => {
    if (stopped) return;
    stopped = true;
    stopFrames?.();
    audio.removeEventListener('pause', stop);
    audio.removeEventListener('ended', stop);
    audio.removeEventListener('error', stop);
    if (audioElementStops.get(audio) === stop) audioElementStops.delete(audio);
    try { source?.disconnect(); } catch { /* ignore monitor cleanup failures */ }
    try { analyser?.disconnect(); } catch { /* ignore monitor cleanup failures */ }
    if (context && context.state !== 'closed') void context.close().catch(() => undefined);
    dispatchAvatarFrame('closed');
  };

  try {
    context = new AudioContextCtor({ latencyHint: 'interactive' });
    analyser = context.createAnalyser();
    analyser.fftSize = AUDIO_ELEMENT_FFT_SIZE;
    analyser.smoothingTimeConstant = 0.2;

    const capturable = audio as CapturableAudioElement;
    const capturedStream = capturable.captureStream?.() ?? capturable.mozCaptureStream?.();
    if (capturedStream && typeof context.createMediaStreamSource === 'function') {
      source = context.createMediaStreamSource(capturedStream);
      source.connect(analyser);
    } else {
      source = context.createMediaElementSource(audio);
      source.connect(analyser);
      analyser.connect(context.destination);
    }

    const waveform = new Float32Array(analyser.fftSize);
    stopFrames = startTicker(() => {
      if (!analyser || audio.paused || audio.ended) return;
      analyser.getFloatTimeDomainData(waveform);
      dispatchAvatarFrame(floatPcmMouthFrame(waveform));
    }, AUDIO_ELEMENT_FRAME_MS);

    audioElementStops.set(audio, stop);
    audio.addEventListener('pause', stop, { once: true });
    audio.addEventListener('ended', stop, { once: true });
    audio.addEventListener('error', stop, { once: true });
    if (context.state !== 'running') void context.resume().catch(() => undefined);
  } catch {
    stop();
  }
}

function stopAudioElementMonitor(audio: HTMLAudioElement): void {
  audioElementStops.get(audio)?.();
}

function schedulePcmSamples(samples: Int16Array, sampleRate: number, startDelayMs = 0): void {
  const now = performance.now();
  const startAt = Math.max(nextAudioFrameAt, now + 25 + Math.max(0, startDelayMs));
  const timeline = pcmMouthTimeline(samples, sampleRate);
  for (const point of timeline) {
    window.setTimeout(() => dispatchAvatarFrame(point.frame), Math.max(0, startAt - now + point.offsetMs));
  }
  const durationMs = (samples.length / sampleRate) * 1000;
  nextAudioFrameAt = startAt + durationMs;
  window.setTimeout(() => dispatchAvatarFrame('closed'), Math.max(0, nextAudioFrameAt - now));
}

function isAvatarMouthFrame(value: unknown): value is AvatarMouthFrame {
  return value === 'closed' || value === 'small' || value === 'medium' || value === 'wide';
}

function dispatchAvatarFrame(frame: AvatarMouthFrame): void {
  emitOmnixEvent(CHARACTER_AVATAR_FRAME_EVENT, { frame });
}

function scheduleBlink(): void {
  if (typeof window === 'undefined') return;
  if (blinkTimer !== null) clearTimeout(blinkTimer);
  const pack = currentRuntime?.avatar_pack;
  if (!pack?.blink_frames.closed) return;
  blinkTimer = window.setTimeout(() => {
    if (currentMouthFrame !== 'closed' || liveCallPresentationStore.getState().speaking) {
      scheduleBlink();
      return;
    }
    blinkClosed = true;
    publishAvatarState();
    window.setTimeout(() => {
      blinkClosed = false;
      publishAvatarState();
      scheduleBlink();
    }, 120);
  }, 3_800 + Math.round(Math.random() * 2_400));
}

