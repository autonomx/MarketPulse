/**
 * The assistant's events on the in-page bus (WP-9.6). Each feature declares its
 * own events by augmenting `OmnixEventMap`, so the core bus never imports a
 * feature (PA-2.4).
 */
import type { DesktopCompanionExpression } from './workspace/desktop-companion-delivery';
import type { DesktopCompanionEvaluationEvent } from './workspace/desktop-companion-watch-controller';
import type { LiveFinalTerminalOutcome } from './workspace/live-accepted-final';
import type { LiveChatReleaseGateReport, VoiceSessionEvaluationRecord } from './workspace/live-chat-evaluation-client';
import type { LiveAssistantTurnSummary } from './workspace/live-conversation-assistant-summary';
import type { LiveConversationEvaluationEvent, LiveConversationEvaluationReport } from './workspace/live-conversation-evaluation';
import type { LiveConversationRuntimeState } from './workspace/live-conversation-store';
import type { LiveObservation, LiveObservationPriority } from './workspace/live-observation-coordinator';
import type { LiveRuntimeProvenance } from './workspace/live-runtime-provenance';
import type { LiveTaskContract } from './workspace/live-task-contract';
import type { BackchannelToken } from './workspace/live-voice-backchannel';
import type { LiveVoiceCalibrationRecord } from './workspace/live-voice-calibration';
import type { RegistrationFailure } from './workspace/live-voice-cue-asset-bridge';
import type { CueSampleSource, LiveVoiceCueId } from './workspace/live-voice-cue-bank';
import type { DecodedCueAsset, LiveVoiceCuePackLoadResult } from './workspace/live-voice-cue-pack-loader';
import type { LiveConversationSettings } from './workspace/live-voice-conversation-settings';
import type { OverlapIntent } from './workspace/live-voice-overlap-classifier';
import type { VocalHabit } from './workspace/live-voice-performance-behavior';
import type { LiveVoiceReleaseObservation } from './workspace/live-voice-release-observer';
import type { LiveVoiceTurnState } from './workspace/live-voice-turn-coordinator';
import type { LiveTurnKind } from './workspace/live-voice-unified-audio-controller';
import type { StreamingSttConnectionStatus } from './workspace/live-voice-websocket';
import type { CharacterLiveCallRuntime } from './chat/characterClient';
import type { Live2DFraming, Live2DMotionSelection } from './chat/live2dCharacterRenderer';
import type { LiveConversationProfile } from './chat/liveConversationProfileClient';
import type { AvatarMouthFrame } from './chat/liveCharacterAvatarBridge';

/** Open-ended diagnostics (performance stages, telemetry): readers check the fields they use. */
type Diagnostics = Record<string, unknown>;

type SttSegment = { chatSessionId: string | null; segmentId: string; sourceSequence: number; text: string };

declare module '../../events/bus' {
  interface OmnixEventMap {
    // Live voice call.
    'omnix:assistant-live-voice-call-start': { token: number; timestamp: string };
    'omnix:assistant-live-voice-call-connected': { token: number; timestamp: string };
    'omnix:assistant-live-voice-stop': null;
    'omnix:assistant-live-voice-user-speech': { timestamp: string; rms: number; assistantSpeaking: boolean; assistantOwnsFloor: boolean };
    'omnix:assistant-voice-interrupt': {
      source: string;
      intent: OverlapIntent | string;
      confidence: number;
      status?: StreamingSttConnectionStatus;
      reason?: string;
      timestamp?: string;
    };
    'omnix:assistant-voice-perf': Diagnostics;
    'omnix:assistant-voice-release-quality': { qualityName: string; occurred: boolean };
    'omnix:assistant-audio-duck': { gain: number; reason: string; ducked?: boolean; timestamp?: number };
    'omnix:assistant-audio-playback-state': { speaking: boolean; source: string; kind: LiveTurnKind | null };
    'omnix:live-call-diagnostic': { traceId: string; source: string; event: string; details: Diagnostics };
    'omnix:live-runtime-bootstrap': LiveRuntimeProvenance;
    'omnix:live-task-contract': { instruction?: string; contract?: LiveTaskContract };
    'omnix:live-presence-policy-refresh': null;
    'omnix:live-voice-turn-timeline': {
      turnId: string;
      event: 'cancelled' | 'speech_ended' | 'final_received' | 'playback_started';
      atMs: number;
      state: LiveVoiceTurnState;
    };
    'omnix:live-voice-release-observation': LiveVoiceReleaseObservation;
    'omnix:live-voice-calibration-updated': LiveVoiceCalibrationRecord;
    'omnix:live-voice-humanization-flags-changed': {
      master: boolean;
      stableClauses: boolean;
      naturalTiming: boolean;
      performancePlans: boolean;
      responseCues: boolean;
      listenerCues: boolean;
      proceduralCueFallback: boolean;
      vocalContinuity: boolean;
    };
    'omnix:live-voice-performance-behavior': {
      scope_key: string;
      reflective: boolean;
      genuine_self_correction: boolean;
      calibrated_uncertainty: boolean;
      playful: boolean;
      habit: VocalHabit;
      observation_count: number;
      warmth: number;
      energy: number;
      tension: number;
      playfulness: number;
      uncertainty: number;
      canonical_text_modified: boolean;
    };
    // Live STT speculation.
    'omnix:live-stt-speculation-partial': SttSegment;
    'omnix:live-stt-speculation-candidate': SttSegment & { probability: number; modelTimeMs: number | undefined; earlyTrigger?: boolean };
    'omnix:live-stt-speculation-final': SttSegment & { chatSessionId: string };
    'omnix:live-stt-speculation-delivery-settled': SttSegment & { chatSessionId: string };
    // Live conversation.
    'omnix:live-conversation-store-updated': LiveConversationRuntimeState;
    'omnix:live-conversation-settings-changed': LiveConversationSettings;
    'omnix:live-conversation-profile-changed': LiveConversationProfile;
    'omnix:live-conversation-pronunciations-changed': {
      entries: Array<{ created_at: string; id: string; locale: string; phrase: string; pronunciation: string; updated_at: string }>;
    };
    'omnix:live-conversation-assistant-summary': LiveAssistantTurnSummary;
    'omnix:live-conversation-evaluation-updated': { events: LiveConversationEvaluationEvent[]; report: LiveConversationEvaluationReport };
    'omnix:live-conversation-durable-evaluation-saved': {
      record: VoiceSessionEvaluationRecord & { release_gate_status: LiveChatReleaseGateReport['status'] };
      gate: LiveChatReleaseGateReport;
    };
    'omnix:live-conversation-listener-backchannel': { sessionId: string; token: BackchannelToken; cueId: LiveVoiceCueId; variantId: string; playedAt: number };
    'omnix:live-conversation-proactive-delivered': { sessionId: string; turnId: string; status: 'completed' | 'interrupted' };
    'omnix:live-conversation-repair-planned': {
      confidence?: number;
      instruction: string;
      kind: 'acknowledge_correction' | 'clarify_number' | 'clarify_name' | 'yield_to_user' | 'resume_interrupted_thought';
      source_reason: string;
    };
    'omnix:live-conversation-user-continuer': { transcript: string; action: string };
    'omnix:live-coordination-terminal': {
      captureEpoch: string;
      segmentId: string;
      resultId: string;
      sourceSequence: number;
      outcome: LiveFinalTerminalOutcome;
      contextVersion: number | undefined;
      errorCode: string | undefined;
    };
    'omnix:live-observation-candidate': { observation: LiveObservation; sourceText: string };
    'omnix:live-observation-superseded': { observationIds: string[]; reason: string };
    'omnix:live-observation-text': { observationId: string; outputId: string; text: string; priority: LiveObservationPriority };
    // Voice cues.
    'omnix:voice-cue-pack-status': LiveVoiceCuePackLoadResult;
    'omnix:voice-cue-assets-ready': { assets: DecodedCueAsset[] };
    'omnix:voice-cue-assets-clear': { voiceId: string };
    'omnix:voice-cue-assets-registered': { received_count: number; registered_count: number; rejected_count: number; failures: RegistrationFailure[] };
    'omnix:live-voice-cue-segment': {
      type: 'segment_started' | 'segment_completed' | 'segment_interrupted';
      segment_id: string;
      segment_kind: string;
      cue_id: LiveVoiceCueId;
      variant_id: string;
      cue_source: CueSampleSource;
      voice_id: string | null;
      semantic_speech_samples: number;
      reason: string | null;
    };
    'omnix:live-voice-cue-skipped': { cue_id: LiveVoiceCueId; variant_id: string; voice_id: string | null; reason: string; procedural_fallback_allowed: boolean };
    // Character avatar.
    'omnix:character-avatar-runtime': CharacterLiveCallRuntime | null;
    'omnix:character-avatar-frame': { frame: AvatarMouthFrame };
    'omnix:character-avatar-pcm': { samples: Int16Array; sampleRate: number; startDelayMs?: number };
    'omnix:character-live2d-render': { runtime: CharacterLiveCallRuntime; host: HTMLElement };
    'omnix:character-live2d-motion': Live2DMotionSelection;
    'omnix:character-live2d-framing': { framing: Live2DFraming };
    'omnix:character-live2d-zoom': { zoom: number };
    // Desktop companion.
    'omnix:desktop-companion-status': { phase: string; reason: string } & Diagnostics;
    'omnix:desktop-companion-evaluation': DesktopCompanionEvaluationEvent;
    'omnix:desktop-companion-delivery-request': {
      sessionId: string;
      observationId: string;
      groundingIds: string[];
      stateSummary: string;
      priority: string;
      presentation: string;
      expiresAtMs: number;
    };
    'omnix:desktop-companion-delivery': { status: string; sessionId: string; observationId: string };
    'omnix:desktop-companion-text': { sessionId: string; observationId: string; turnId: string; content: string; priority: 'normal' | 'critical'; expiresAtMs: number };
    'omnix:desktop-companion-expression': {
      active: boolean;
      sessionId: string;
      observationId: string;
      turnId: string;
      expression: DesktopCompanionExpression;
      intensity: number;
    };
  }
}
