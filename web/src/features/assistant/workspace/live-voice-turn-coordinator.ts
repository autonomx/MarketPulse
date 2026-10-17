import { liveSttUsesAuthoritativeEou } from './live-stt-capability-state';
import { liveVoiceTranscriptStore } from './live-voice-transcript-store';
import { ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent, LIVE_COORDINATION_TERMINAL_EVENT, LIVE_VOICE_TURN_TIMELINE_EVENT } from '../../../events/bus';

const AUTHORITATIVE_EOU_COMPLETE_CONFIRMATION_MS = 360;
const AUTHORITATIVE_EOU_GENERAL_CONFIRMATION_MS = 500;
const AUTHORITATIVE_EOU_SPECULATION_MIN_SILENCE_MS = 100;

export type LiveVoiceTurnState =
  | 'listening'
  | 'speaking'
  | 'endpoint_candidate'
  | 'speculating'
  | 'finalizing'
  | 'committed'
  | 'playing'
  | 'cancelled';

export type EndpointFusionAction = 'continue' | 'speculate' | 'commit';

export type EndpointFusionInput = {
  endpointProbability: number;
  endpointThreshold: number;
  silenceMs: number;
  transcriptStableMs: number;
  semanticProbabilityDone: number;
  transcriptWords: number;
  correctionPending: boolean;
};

export type LiveVoiceTurnTimelineDetail = {
  turnId: string;
  event: 'speech_ended' | 'final_received' | 'playback_started' | 'cancelled';
  atMs: number;
  state: LiveVoiceTurnState;
};

type PerfDetail = {
  stage?: unknown;
  turnId?: unknown;
};

type CoordinationTerminalDetail = {
  outcome?: unknown;
};

type TurnRecord = {
  turnId: string;
  state: LiveVoiceTurnState;
  speechEndedAt: number | null;
  finalReceivedAt: number | null;
  playbackStartedAt: number | null;
};

export function endpointFusionAction(input: EndpointFusionInput): EndpointFusionAction {
  if (input.correctionPending || input.transcriptWords < 1) return 'continue';
  const probability = Number.isFinite(input.endpointProbability)
    ? input.endpointProbability
    : 0;
  const stable = Math.max(0, input.transcriptStableMs);
  const silence = Math.max(0, input.silenceMs);
  const complete = input.semanticProbabilityDone >= 0.9;
  // Parakeet EOU is a strong endpoint vote, but the captured Nemotron/EOU
  // traces include real intra-sentence pauses around 430-480 ms. Those pauses
  // must not split one utterance before the full-buffer Nemotron final can see
  // the complete sentence. Keep the 360/500 ms commit confirmation bounds,
  // while allowing a stable multi-word EOU candidate to start side-effect-free
  // speculation earlier. Provider candidates are not delivered continuously;
  // a 160 ms floor often missed the 100-150 ms candidate and waited until the
  // next update near 300 ms. The 500 ms general bound remains beyond the longest
  // captured intra-sentence pause (478 ms). Resumed speech cancels private work, so this
  // recovers LLM/TTS lead time without making an early EOU user-visible.
  if (liveSttUsesAuthoritativeEou() && probability >= input.endpointThreshold) {
    const requiredSilenceMs = complete && input.transcriptWords >= 2
      ? AUTHORITATIVE_EOU_COMPLETE_CONFIRMATION_MS
      : AUTHORITATIVE_EOU_GENERAL_CONFIRMATION_MS;
    if (silence >= requiredSilenceMs) return 'commit';
    const speculationReady = silence >= AUTHORITATIVE_EOU_SPECULATION_MIN_SILENCE_MS
      && input.transcriptWords >= 2
      && (stable >= 60 || complete);
    return speculationReady ? 'speculate' : 'continue';
  }
  // Once a semantically complete transcript arrives after a long acoustic
  // pause, the ordinary 80 ms transcript-stability guard no longer buys useful
  // safety. On delayed-streaming STT (Kyutai in particular), the last word can
  // arrive roughly a model-delay behind the microphone even though endpoint
  // confidence has already stayed high for hundreds of milliseconds. Commit
  // that newly completed text immediately only when the acoustic evidence is
  // also mature; short internal pauses still use the normal stability guard.
  const matureComplete = complete
    && probability >= Math.max(0.85, input.endpointThreshold + 0.1)
    && silence >= 650;
  if (
    complete
    && probability >= input.endpointThreshold
    && silence >= 160
    && (stable >= 80 || matureComplete)
  ) return 'commit';
  // Kyutai runs behind live audio by roughly its model delay, so ordinary
  // statements often become semantically readable only after the user has
  // already held the floor quiet for a substantial interval. Recover that
  // latency only when both acoustic and transcript evidence are mature. This
  // deliberately excludes short internal pauses such as "You should lie ...
  // more", whose failing trace had 0 ms measured pause and 21 ms stability.
  const stableDefinitiveStatement = !complete
    && input.semanticProbabilityDone >= 0.75
    && input.transcriptWords >= 2
    && probability >= Math.max(0.8, input.endpointThreshold + 0.05)
    && silence >= 650
    && stable >= 140;
  if (stableDefinitiveStatement) return 'commit';
  if (
    probability >= Math.max(0.35, input.endpointThreshold - 0.35)
    && stable >= 60
    && (input.transcriptWords >= 2 || probability >= 0.9)
  ) return 'speculate';
  return 'continue';
}

export function removeTransientFinalUserRows(): number {
  return liveVoiceTranscriptStore.removeFinalUserRows();
}

export class LiveVoiceTurnCoordinator {
  private readonly turns = new Map<string, TurnRecord>();

  speechEnded(turnId: string, atMs = performance.now()): void {
    const turn = this.record(turnId);
    if (
      turn.finalReceivedAt !== null
      || turn.playbackStartedAt !== null
      || turn.state === 'cancelled'
    ) return;
    // A user may pause and then resume within one utterance. Keep the latest
    // speech-to-silence transition so release latency is measured from the
    // pause that actually led to finalization, not an earlier internal pause.
    turn.speechEndedAt = atMs;
    turn.state = 'endpoint_candidate';
    this.emit(turn, 'speech_ended', atMs);
  }

  finalReceived(turnId: string, atMs = performance.now()): void {
    const turn = this.record(turnId);
    turn.finalReceivedAt = atMs;
    turn.state = 'committed';
    this.emit(turn, 'final_received', atMs);
  }

  playbackStarted(turnId: string, atMs = performance.now()): void {
    const turn = this.record(turnId);
    if (turn.playbackStartedAt !== null) return;
    turn.playbackStartedAt = atMs;
    turn.state = 'playing';
    this.emit(turn, 'playback_started', atMs);
  }

  cancel(turnId: string, atMs = performance.now()): void {
    const turn = this.record(turnId);
    turn.state = 'cancelled';
    this.emit(turn, 'cancelled', atMs);
  }

  snapshot(turnId: string): Readonly<TurnRecord> | null {
    const turn = this.turns.get(turnId);
    return turn ? { ...turn } : null;
  }

  clear(turnId?: string): void {
    if (turnId) this.turns.delete(turnId);
    else this.turns.clear();
  }

  private record(turnId: string): TurnRecord {
    const normalized = turnId.trim();
    const existing = this.turns.get(normalized);
    if (existing) return existing;
    const created: TurnRecord = {
      turnId: normalized,
      state: 'listening',
      speechEndedAt: null,
      finalReceivedAt: null,
      playbackStartedAt: null,
    };
    this.turns.set(normalized, created);
    while (this.turns.size > 32) {
      const oldest = this.turns.keys().next().value;
      if (typeof oldest !== 'string') break;
      this.turns.delete(oldest);
    }
    return created;
  }

  private emit(
    turn: TurnRecord,
    event: LiveVoiceTurnTimelineDetail['event'],
    atMs: number,
  ): void {
    if (typeof window === 'undefined') return;
    emitOmnixEvent(LIVE_VOICE_TURN_TIMELINE_EVENT, {
          turnId: turn.turnId,
          event,
          atMs,
          state: turn.state,
        });
  }
}

export const liveVoiceTurnCoordinator = new LiveVoiceTurnCoordinator();

let initialized = false;
let transcriptReconciliationInitialized = false;
let disposeTranscriptReconciliation: (() => void) | null = null;

export function initializeLiveVoiceTurnCoordinator(): () => void {
  if (initialized || typeof window === 'undefined') return () => undefined;
  initialized = true;
  const handlePerformance = (event: Event): void => {
    const detail = (event as CustomEvent<PerfDetail>).detail ?? {};
    const stage = typeof detail.stage === 'string' ? detail.stage : '';
    const turnId = typeof detail.turnId === 'string' ? detail.turnId.trim() : '';
    if (!turnId) return;
    if (stage === 'semantic_turn_assessed') {
      liveVoiceTurnCoordinator.speechEnded(turnId);
    } else if (stage === 'stt_final_received') {
      liveVoiceTurnCoordinator.finalReceived(turnId);
    }
  };
  window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerformance);
  return () => {
    window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerformance);
    liveVoiceTurnCoordinator.clear();
    initialized = false;
  };
}

export function initializeLiveVoiceTranscriptReconciliation(): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;
  if (transcriptReconciliationInitialized) return disposeTranscriptReconciliation ?? (() => undefined);
  transcriptReconciliationInitialized = true;
  const handleTerminal = (event: Event): void => {
    const detail = (event as CustomEvent<CoordinationTerminalDetail>).detail ?? {};
    if (detail.outcome !== 'conversation_submitted') return;
    // The dedicated controller owns only the transient draft. Once the final is
    // durably submitted, React's session message is the canonical user row.
    removeTransientFinalUserRows();
  };
  window.addEventListener(LIVE_COORDINATION_TERMINAL_EVENT, handleTerminal);
  disposeTranscriptReconciliation = () => {
    window.removeEventListener(LIVE_COORDINATION_TERMINAL_EVENT, handleTerminal);
    transcriptReconciliationInitialized = false;
    disposeTranscriptReconciliation = null;
  };
  return disposeTranscriptReconciliation;
}

export function resetLiveVoiceTurnCoordinatorForTests(): void {
  disposeTranscriptReconciliation?.();
  transcriptReconciliationInitialized = false;
  disposeTranscriptReconciliation = null;
  liveVoiceTurnCoordinator.clear();
}
