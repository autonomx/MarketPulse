import { LIVE_VOICE_PCM_WORKLET_NAME } from './live-voice-pcm-worklet';
import { ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent } from '../../../events/bus';

let liveTtsAdaptiveBufferInstalled = false;

const STORAGE_KEY = 'omnix.liveTts.adaptiveBuffer.v3';
const MAX_TRACKED_ANCILLARY_SEGMENTS = 128;
const MIN_START_BUFFER_MS = 120;

export type AdaptiveBufferSnapshot = {
  startBufferMs: number;
  rebufferMs: number;
  maxRebufferMs: number;
  stableTurns: number;
  underrunTurns: number;
};

export type AncillaryCancellationDecision = {
  forward: boolean;
  cancelSegmentIds: string[];
  reason?: string;
};

type AdaptiveWindow = Window & typeof globalThis;

type WorkletOutboundMessage = Record<string, unknown> & {
  type?: unknown;
};

export class AdaptiveTtsBufferPolicy {
  private snapshotValue: AdaptiveBufferSnapshot;
  private turnUnderruns = 0;

  constructor(initial?: Partial<AdaptiveBufferSnapshot>) {
    this.snapshotValue = {
      // One 1,920-sample / 80 ms Qwen frame is not quite enough to cover the
      // handoff to the next codec step on steady hardware. Keep the startup
      // reserve at the roadmap's 120 ms upper bound so playback starts only
      // once the second frame is available; prefetched turns pay no extra wait.
      startBufferMs: clamp(initial?.startBufferMs ?? MIN_START_BUFFER_MS, MIN_START_BUFFER_MS, 650),
      rebufferMs: clamp(initial?.rebufferMs ?? 520, 300, 1_200),
      maxRebufferMs: clamp(initial?.maxRebufferMs ?? 1_400, 800, 2_000),
      stableTurns: Math.max(0, initial?.stableTurns ?? 0),
      underrunTurns: Math.max(0, initial?.underrunTurns ?? 0),
    };
  }

  snapshot(): AdaptiveBufferSnapshot {
    return { ...this.snapshotValue };
  }

  observeWorkletEvent(type: string): AdaptiveBufferSnapshot {
    if (type === 'underrun') {
      this.turnUnderruns += 1;
      this.snapshotValue.startBufferMs = clamp(
        this.snapshotValue.startBufferMs + 70,
        MIN_START_BUFFER_MS,
        650,
      );
      this.snapshotValue.rebufferMs = clamp(
        this.snapshotValue.rebufferMs + 110,
        300,
        1_200,
      );
    }
    if (type === 'drained' || type === 'idle') {
      if (this.turnUnderruns > 0) {
        this.snapshotValue.underrunTurns += 1;
        this.snapshotValue.stableTurns = 0;
      } else {
        this.snapshotValue.stableTurns += 1;
        if (this.snapshotValue.stableTurns >= 3) {
          // Decay the post-underrun reserve after three stable turns, but never
          // below the 120 ms startup reserve. Stable hardware can therefore
          // return to the measured safe floor after temporary contention.
          this.snapshotValue.startBufferMs = clamp(
            this.snapshotValue.startBufferMs - 30,
            MIN_START_BUFFER_MS,
            650,
          );
          this.snapshotValue.rebufferMs = clamp(
            this.snapshotValue.rebufferMs - 30,
            300,
            1_200,
          );
          this.snapshotValue.stableTurns = 0;
        }
      }
      this.turnUnderruns = 0;
    }
    return this.snapshot();
  }
}

/**
 * Tracks pause/cue segments that predate output ownership.
 *
 * Speech output already carries output_id + generation_epoch and is removed by
 * cancel_output. Planned silence and response cues currently do not, so an
 * interrupted turn can otherwise leave a stale pause in the persistent
 * worklet queue. The guard removes queued ancillary segments on turn-level
 * cancellation and rejects late ancillary messages until a replacement turn
 * establishes a fresh start policy or owned speech frame.
 */
export class LiveTtsAncillaryCancellationGuard {
  private suppressUnownedAncillary = false;
  private readonly trackedSegmentIds = new Set<string>();

  handleOutbound(message: unknown): AncillaryCancellationDecision {
    const outbound = asOutboundMessage(message);
    if (!outbound) return { forward: true, cancelSegmentIds: [] };
    const type = typeof outbound.type === 'string' ? outbound.type : '';

    if (type === 'stop') {
      this.reset();
      return { forward: true, cancelSegmentIds: [] };
    }

    if (type === 'set_start_policy') {
      this.suppressUnownedAncillary = false;
      this.trackedSegmentIds.clear();
      return { forward: true, cancelSegmentIds: [] };
    }

    if (type === 'cancel_output' && isTurnTerminalCancellation(outbound.reason)) {
      const cancelSegmentIds = [...this.trackedSegmentIds];
      this.trackedSegmentIds.clear();
      this.suppressUnownedAncillary = true;
      return {
        forward: true,
        cancelSegmentIds,
        reason: normalizedReason(outbound.reason) || 'turn-superseded',
      };
    }

    if (type === 'cancel_segment' || type === 'segment_end') {
      const segmentId = normalizedSegmentId(outbound.segmentId);
      if (segmentId) this.trackedSegmentIds.delete(segmentId);
      return { forward: true, cancelSegmentIds: [] };
    }

    if (isOwnedSpeechMessage(outbound)) {
      this.suppressUnownedAncillary = false;
      this.trackedSegmentIds.clear();
      return { forward: true, cancelSegmentIds: [] };
    }

    if (!isUnownedAncillaryMessage(outbound)) {
      return { forward: true, cancelSegmentIds: [] };
    }

    const segmentId = normalizedSegmentId(outbound.segmentId);
    if (this.suppressUnownedAncillary) {
      return {
        forward: false,
        cancelSegmentIds: [],
        reason: 'superseded-unowned-ancillary',
      };
    }
    if (segmentId) {
      this.trackedSegmentIds.add(segmentId);
      while (this.trackedSegmentIds.size > MAX_TRACKED_ANCILLARY_SEGMENTS) {
        const oldest = this.trackedSegmentIds.values().next().value;
        if (typeof oldest !== 'string') break;
        this.trackedSegmentIds.delete(oldest);
      }
    }
    return { forward: true, cancelSegmentIds: [] };
  }

  observeWorkletEvent(message: unknown): void {
    const event = asOutboundMessage(message);
    if (!event) return;
    const type = typeof event.type === 'string' ? event.type : '';
    if (type !== 'segment_completed'
      && type !== 'segment_cancelled'
      && type !== 'segment_interrupted') return;
    const segmentId = normalizedSegmentId(event.segment_id ?? event.segmentId);
    if (segmentId) this.trackedSegmentIds.delete(segmentId);
  }

  reset(): void {
    this.suppressUnownedAncillary = false;
    this.trackedSegmentIds.clear();
  }
}

export function adaptiveBufferWorkletMessage(
  snapshot: AdaptiveBufferSnapshot,
  sampleRate: number,
): Record<string, number | string> {
  return {
    type: 'set_buffer_policy',
    startBufferSamples: millisecondsToSamples(
      snapshot.startBufferMs,
      sampleRate,
    ),
    minimumBufferedSpeechSamples: millisecondsToSamples(
      snapshot.startBufferMs,
      sampleRate,
    ),
    rebufferSamples: millisecondsToSamples(
      snapshot.rebufferMs,
      sampleRate,
    ),
    maxRebufferSamples: millisecondsToSamples(
      snapshot.maxRebufferMs,
      sampleRate,
    ),
  };
}

export function initializeLiveTtsAdaptiveBufferController(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const liveWindow = window as AdaptiveWindow;
  if (liveTtsAdaptiveBufferInstalled) return () => undefined;
  const NativeAudioWorkletNode = liveWindow.AudioWorkletNode;
  if (!NativeAudioWorkletNode) return () => undefined;
  const originalDescriptor = Object.getOwnPropertyDescriptor(
    liveWindow,
    'AudioWorkletNode',
  );
  const adaptiveEnabled = adaptiveBufferEnabled();
  const policy = new AdaptiveTtsBufferPolicy(loadSnapshot());

  const WrappedAudioWorkletNode = new Proxy(NativeAudioWorkletNode, {
    construct(target, argumentsList, newTarget) {
      const [audioContext, name, options] = argumentsList as [
        AudioContext,
        string,
        AudioWorkletNodeOptions | undefined,
      ];
      if (name !== LIVE_VOICE_PCM_WORKLET_NAME) {
        return Reflect.construct(target, argumentsList, newTarget);
      }
      const snapshot = policy.snapshot();
      const sampleRate = audioContext.sampleRate;
      const policyMessage = adaptiveBufferWorkletMessage(
        snapshot,
        sampleRate,
      );
      const nextOptions: AudioWorkletNodeOptions = adaptiveEnabled
        ? {
            ...(options ?? {}),
            processorOptions: {
              ...(
                (options?.processorOptions as
                  | Record<string, unknown>
                  | undefined) ?? {}
              ),
              startBufferSamples: policyMessage.startBufferSamples,
              minimumBufferedSpeechSamples:
                policyMessage.minimumBufferedSpeechSamples,
              rebufferSamples: policyMessage.rebufferSamples,
              maxRebufferSamples: policyMessage.maxRebufferSamples,
            },
          }
        : { ...(options ?? {}) };
      const node = Reflect.construct(
        target,
        [audioContext, name, nextOptions],
        newTarget,
      ) as AudioWorkletNode;
      const originalPostMessage = node.port.postMessage.bind(node.port);
      const ancillaryGuard = new LiveTtsAncillaryCancellationGuard();
      try {
        node.port.postMessage = ((
          message: unknown,
          transfer?: Transferable[],
        ) => {
          const decision = ancillaryGuard.handleOutbound(message);
          for (const segmentId of decision.cancelSegmentIds) {
            originalPostMessage({
              type: 'cancel_segment',
              segmentId,
              reason: decision.reason ?? 'turn-superseded',
            });
          }
          if (decision.cancelSegmentIds.length > 0) {
            dispatchPerformance('tts_stale_ancillary_cancelled', {
              reason: decision.reason,
              segmentCount: decision.cancelSegmentIds.length,
            });
          }
          if (!decision.forward) {
            const outbound = asOutboundMessage(message);
            dispatchPerformance('tts_stale_ancillary_dropped', {
              reason: decision.reason,
              messageType: typeof outbound?.type === 'string' ? outbound.type : undefined,
              segmentId: normalizedSegmentId(outbound?.segmentId),
            });
            return;
          }
          if (adaptiveEnabled && isBufferPolicyMessage(message)) {
            const effective = adaptiveBufferWorkletMessage(
              policy.snapshot(),
              sampleRate,
            );
            originalPostMessage(
              {
                ...message,
                startBufferSamples: effective.startBufferSamples,
                minimumBufferedSpeechSamples:
                  effective.minimumBufferedSpeechSamples,
                rebufferSamples: effective.rebufferSamples,
                maxRebufferSamples: effective.maxRebufferSamples,
              },
              transfer ?? [],
            );
            dispatchPerformance('tts_adaptive_policy_message_overridden', {
              sourceType: message.type,
              sampleRate,
              ...effective,
            });
            return;
          }
          originalPostMessage(message, transfer ?? []);
        }) as MessagePort['postMessage'];
      } catch {
        dispatchPerformance('tts_adaptive_post_message_unavailable', {});
      }
      node.port.addEventListener(
        'message',
        (event: MessageEvent<Record<string, unknown>>) => {
          ancillaryGuard.observeWorkletEvent(event.data);
          if (!adaptiveEnabled) return;
          const type = typeof event.data?.type === 'string'
            ? event.data.type
            : '';
          if (type !== 'underrun' && type !== 'drained' && type !== 'idle') return;
          const next = policy.observeWorkletEvent(type);
          saveSnapshot(next);
          const nextMessage = adaptiveBufferWorkletMessage(next, sampleRate);
          try {
            originalPostMessage(nextMessage);
          } catch {
            dispatchPerformance(
              'tts_adaptive_runtime_update_failed',
              { trigger: type },
            );
          }
          dispatchPerformance('tts_adaptive_buffer_updated', {
            trigger: type,
            sampleRate,
            ...next,
            ...nextMessage,
          });
        },
      );
      node.port.start?.();
      if (adaptiveEnabled) {
        dispatchPerformance('tts_adaptive_buffer_applied', {
          sampleRate,
          ...snapshot,
          ...policyMessage,
        });
      }
      return node;
    },
  });

  try {
    Object.defineProperty(liveWindow, 'AudioWorkletNode', {
      configurable: true,
      writable: true,
      value: WrappedAudioWorkletNode,
    });
  } catch (error) {
    liveTtsAdaptiveBufferInstalled = false;
    dispatchPerformance('tts_adaptive_install_failed', {
      error: error instanceof Error ? error.message : String(error),
    });
    return () => undefined;
  }
  liveTtsAdaptiveBufferInstalled = true;

  return () => {
    try {
      if (originalDescriptor) {
        Object.defineProperty(
          liveWindow,
          'AudioWorkletNode',
          originalDescriptor,
        );
      } else {
        Object.defineProperty(liveWindow, 'AudioWorkletNode', {
          configurable: true,
          writable: true,
          value: NativeAudioWorkletNode,
        });
      }
    } finally {
      liveTtsAdaptiveBufferInstalled = false;
    }
  };
}

function asOutboundMessage(value: unknown): WorkletOutboundMessage | null {
  return value && typeof value === 'object'
    ? value as WorkletOutboundMessage
    : null;
}

function isBufferPolicyMessage(
  value: unknown,
): value is Record<string, unknown> & { type: string } {
  const outbound = asOutboundMessage(value);
  const type = outbound?.type;
  return type === 'set_start_policy' || type === 'set_buffer_policy';
}

function isUnownedAncillaryMessage(message: WorkletOutboundMessage): boolean {
  if (normalizedSegmentId(message.outputId)) return false;
  if (message.type === 'push_segment_silence') return true;
  return message.type === 'push_segment_samples' && message.segmentKind === 'cue';
}

function isOwnedSpeechMessage(message: WorkletOutboundMessage): boolean {
  return message.type === 'push_segment_samples'
    && message.segmentKind === 'speech'
    && Boolean(normalizedSegmentId(message.outputId));
}

function isTurnTerminalCancellation(value: unknown): boolean {
  const reason = normalizedReason(value).toLowerCase();
  return reason.includes('interrupt')
    || reason.includes('superseded')
    || reason.includes('user-spoke')
    || reason === 'turn-failed'
    || reason === 'audio-recovery'
    || reason === 'stopped';
}

function normalizedReason(value: unknown): string {
  return typeof value === 'string' ? value.trim().slice(0, 120) : '';
}

function normalizedSegmentId(value: unknown): string | null {
  return typeof value === 'string' && value.trim()
    ? value.trim().slice(0, 200)
    : null;
}

function millisecondsToSamples(
  milliseconds: number,
  sampleRate: number,
): number {
  return Math.max(1, Math.round(milliseconds * sampleRate / 1_000));
}

function loadSnapshot(): Partial<AdaptiveBufferSnapshot> | undefined {
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(STORAGE_KEY) ?? 'null',
    ) as unknown;
    return parsed && typeof parsed === 'object'
      ? parsed as Partial<AdaptiveBufferSnapshot>
      : undefined;
  } catch {
    return undefined;
  }
}

function saveSnapshot(snapshot: AdaptiveBufferSnapshot): void {
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(snapshot),
    );
  } catch {
    // Best-effort local tuning only.
  }
}

function adaptiveBufferEnabled(): boolean {
  const env = (
    import.meta as unknown as {
      env?: Record<string, string | undefined>;
    }
  ).env;
  return env?.VITE_LIVE_TTS_ADAPTIVE_BUFFER
    ?.trim()
    .toLowerCase() !== 'false';
}

function dispatchPerformance(
  stage: string,
  detail: Record<string, unknown>,
): void {
  emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, {
      stage,
      timestamp: new Date().toISOString(),
      ...detail,
    });
}

function clamp(
  value: number,
  minimum: number,
  maximum: number,
): number {
  return Math.max(minimum, Math.min(maximum, value));
}
