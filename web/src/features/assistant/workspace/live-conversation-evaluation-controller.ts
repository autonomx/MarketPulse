/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { type LiveAssistantTurnSummary } from './live-conversation-assistant-summary';
import {
  evaluateLiveConversation,
  type LiveConversationEvaluationEvent,
  type LiveConversationEvaluationReport,
} from './live-conversation-evaluation';
import { liveConversationStore } from './live-conversation-store';
import { ASSISTANT_LIVE_VOICE_CALL_START_EVENT, ASSISTANT_LIVE_VOICE_STOP_EVENT, ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent, LIVE_CONVERSATION_ASSISTANT_SUMMARY_EVENT, LIVE_CONVERSATION_EVALUATION_UPDATED_EVENT, LIVE_CONVERSATION_LISTENER_BACKCHANNEL_EVENT, LIVE_CONVERSATION_PROACTIVE_DELIVERED_EVENT, LIVE_CONVERSATION_REPAIR_PLANNED_EVENT } from '../../../events/bus';

let liveConversationEvaluationInstalled = false;

export const LIVE_EVALUATION_STORAGE_KEY = 'omnix.liveConversation.evaluation.v1';
const MAX_EVENTS = 600;
const MAX_TOPIC_FINGERPRINTS = 12;
const PROACTIVE_REGRET_WINDOW_MS = 2_500;
const BACKCHANNEL_COLLISION_WINDOW_MS = 650;

type PerfDetail = Record<string, unknown> & { stage?: unknown };

type EvaluationSnapshot = {
  events: LiveConversationEvaluationEvent[];
  report: LiveConversationEvaluationReport;
};

let events: LiveConversationEvaluationEvent[] = [];
let userSpeechStartedAt: number | null = null;
let overlapStartedAt: number | null = null;
let lastProactiveAt: number | null = null;
let pendingRepair = false;
let pendingObligation = false;
let pendingAssistantSummary: LiveAssistantTurnSummary | null = null;
let recentTopicFingerprints: string[] = [];
let listenerBackchannelAt: number | null = null;
let listenerBackchannelTimer: ReturnType<typeof setTimeout> | null = null;
let assistantSpeechStartedAt: number | null = null;
let previousAssistantSpeaking = false;

export function initializeLiveConversationEvaluationController(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (liveConversationEvaluationInstalled) return () => undefined;
  liveConversationEvaluationInstalled = true;
  events = readStoredEvents();

  const handleCallStart = () => resetLiveConversationEvaluation();
  const handleStop = () => {
    if (pendingRepair) recordLiveConversationEvaluationEvent({ atMs: now(), type: 'repair', success: false });
    if (pendingObligation) recordLiveConversationEvaluationEvent({ atMs: now(), type: 'obligation', answered: false });
    pendingRepair = false;
    pendingObligation = false;
    pendingAssistantSummary = null;
    resolveBackchannel(false);
    completeAssistantTurn();
  };
  const handleUserSpeech = () => {
    const timestamp = now();
    userSpeechStartedAt ??= timestamp;
    if (lastProactiveAt !== null) {
      recordLiveConversationEvaluationEvent({
        atMs: timestamp,
        type: 'proactive_prompt',
        accepted: timestamp - lastProactiveAt > PROACTIVE_REGRET_WINDOW_MS,
      });
      lastProactiveAt = null;
    }
    if (listenerBackchannelAt !== null && timestamp - listenerBackchannelAt <= BACKCHANNEL_COLLISION_WINDOW_MS) {
      resolveBackchannel(true);
    }
  };
  const handleProactive = () => {
    lastProactiveAt = now();
    recordLiveConversationEvaluationEvent({ atMs: lastProactiveAt, type: 'proactive_prompt', accepted: null });
  };
  const handleRepair = () => { pendingRepair = true; };
  const handleListenerBackchannel = () => {
    resolveBackchannel(false, false);
    listenerBackchannelAt = now();
    listenerBackchannelTimer = setTimeout(() => resolveBackchannel(false), BACKCHANNEL_COLLISION_WINDOW_MS);
  };
  const handleAssistantSummary = (event: Event) => {
    const summary = (event as CustomEvent<LiveAssistantTurnSummary>).detail;
    if (!summary || summary.turnKind !== 'response') return;
    pendingAssistantSummary = summary;
    if (summary.topicFingerprint) {
      const repeated = recentTopicFingerprints.includes(summary.topicFingerprint);
      recordLiveConversationEvaluationEvent({ atMs: now(), type: 'topic', repeated });
      recentTopicFingerprints = [
        ...recentTopicFingerprints.filter((fingerprint) => fingerprint !== summary.topicFingerprint),
        summary.topicFingerprint,
      ].slice(-MAX_TOPIC_FINGERPRINTS);
    }
    if (summary.createsObligation) {
      if (pendingObligation) {
        recordLiveConversationEvaluationEvent({ atMs: now(), type: 'obligation', answered: false });
      }
      pendingObligation = true;
    }
  };
  const handlePerf = (event: Event) => {
    const detail = (event as CustomEvent<PerfDetail>).detail ?? {};
    const mapped = evaluationEventFromPerfDetail(detail, now());
    if (mapped) recordLiveConversationEvaluationEvent(mapped);
    const stage = String(detail.stage ?? '');
    if (stage === 'overlap_candidate') overlapStartedAt = now();
    if (stage === 'overlap_classified' && overlapStartedAt !== null) {
      recordLiveConversationEvaluationEvent({
        atMs: now(),
        type: 'talk_over',
        durationMs: Math.max(0, now() - overlapStartedAt),
      });
      overlapStartedAt = null;
    }
    if (stage === 'stt_final_received' && userSpeechStartedAt !== null) {
      recordTurn('user', Math.max(100, now() - userSpeechStartedAt), 0);
      userSpeechStartedAt = null;
      if (pendingObligation) {
        recordLiveConversationEvaluationEvent({ atMs: now(), type: 'obligation', answered: true });
        pendingObligation = false;
      }
    }
    if (stage === 'voice_audio_turnaround' && pendingRepair) {
      recordLiveConversationEvaluationEvent({ atMs: now(), type: 'repair', success: true });
      pendingRepair = false;
    }
  };
  const handleStore = () => {
    const conversation = liveConversationStore.getState().conversation;
    const speaking = conversation.assistantTurn === 'speaking' || conversation.delivery === 'audio_started';
    if (speaking && !previousAssistantSpeaking) assistantSpeechStartedAt = now();
    const assistantTurnCompleted = !speaking && previousAssistantSpeaking;
    previousAssistantSpeaking = speaking;
    if (assistantTurnCompleted) completeAssistantTurn();
  };

  window.addEventListener(ASSISTANT_LIVE_VOICE_CALL_START_EVENT, handleCallStart);
  window.addEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, handleStop);
  window.addEventListener(ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, handleUserSpeech);
  window.addEventListener(LIVE_CONVERSATION_PROACTIVE_DELIVERED_EVENT, handleProactive);
  window.addEventListener(LIVE_CONVERSATION_REPAIR_PLANNED_EVENT, handleRepair);
  window.addEventListener(LIVE_CONVERSATION_LISTENER_BACKCHANNEL_EVENT, handleListenerBackchannel);
  window.addEventListener(LIVE_CONVERSATION_ASSISTANT_SUMMARY_EVENT, handleAssistantSummary);
  window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerf);
  const unsubscribe = liveConversationStore.subscribe(handleStore);
  handleStore();
  dispatchUpdate();

  return () => {
    unsubscribe();
    window.removeEventListener(ASSISTANT_LIVE_VOICE_CALL_START_EVENT, handleCallStart);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, handleStop);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, handleUserSpeech);
    window.removeEventListener(LIVE_CONVERSATION_PROACTIVE_DELIVERED_EVENT, handleProactive);
    window.removeEventListener(LIVE_CONVERSATION_REPAIR_PLANNED_EVENT, handleRepair);
    window.removeEventListener(LIVE_CONVERSATION_LISTENER_BACKCHANNEL_EVENT, handleListenerBackchannel);
    window.removeEventListener(LIVE_CONVERSATION_ASSISTANT_SUMMARY_EVENT, handleAssistantSummary);
    window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerf);
    resolveBackchannel(false, false);
    liveConversationEvaluationInstalled = false;
  };
}

export function evaluationEventFromPerfDetail(
  detail: PerfDetail,
  atMs: number,
): LiveConversationEvaluationEvent | null {
  const stage = String(detail.stage ?? '');
  if (stage === 'voice_audio_turnaround') {
    const latency = numberValue(detail.total_ms ?? detail.totalMs);
    return latency === null ? null : { atMs, type: 'first_audio', latencyMs: latency };
  }
  if (stage === 'endpoint_false_positive') return { atMs, type: 'endpoint', falsePositive: true };
  if (stage === 'endpoint_committed') return { atMs, type: 'endpoint', falsePositive: false };
  if (stage === 'talk_over') {
    const duration = numberValue(detail.duration_ms ?? detail.durationMs);
    return duration === null ? null : { atMs, type: 'talk_over', durationMs: duration };
  }
  if (stage === 'overlap_classified') {
    const intent = String(detail.intent ?? '');
    if (!['interrupt', 'hard_stop', 'correction', 'question'].includes(intent)) return null;
    const latency = numberValue(detail.cancellation_latency_ms ?? detail.cancel_ms ?? detail.latency_ms);
    return { atMs, type: 'interruption', success: true, ...(latency === null ? {} : { latencyMs: latency }) };
  }
  if (stage === 'barge_in_rejected') return { atMs, type: 'interruption', success: false };
  return null;
}

export function recordLiveConversationEvaluationEvent(event: LiveConversationEvaluationEvent): EvaluationSnapshot {
  events = [...events, sanitizeEvent(event)].sort((left, right) => left.atMs - right.atMs).slice(-MAX_EVENTS);
  persistEvents(events);
  return dispatchUpdate();
}

export function recordLiveConversationSurvey(listeningScore: number, pressureScore: number): EvaluationSnapshot {
  return recordLiveConversationEvaluationEvent({
    atMs: now(),
    type: 'survey',
    listeningScore,
    pressureScore,
  });
}

export function readLiveConversationEvaluationSnapshot(): EvaluationSnapshot {
  const current = events.length ? events : readStoredEvents();
  return { events: [...current], report: evaluateLiveConversation(current) };
}

export function resetLiveConversationEvaluation(): EvaluationSnapshot {
  events = [];
  userSpeechStartedAt = null;
  overlapStartedAt = null;
  lastProactiveAt = null;
  pendingRepair = false;
  pendingObligation = false;
  pendingAssistantSummary = null;
  recentTopicFingerprints = [];
  assistantSpeechStartedAt = null;
  previousAssistantSpeaking = false;
  resolveBackchannel(false, false);
  persistEvents(events);
  return dispatchUpdate();
}

function completeAssistantTurn(): void {
  if (assistantSpeechStartedAt === null) return;
  const startedAt = assistantSpeechStartedAt;
  const questionCount = pendingAssistantSummary?.questionCount ?? 0;
  assistantSpeechStartedAt = null;
  pendingAssistantSummary = null;
  recordTurn(
    'assistant',
    Math.max(350, now() - startedAt),
    questionCount,
  );
}

function recordTurn(role: 'user' | 'assistant', durationMs: number, questionCount: number): void {
  recordLiveConversationEvaluationEvent({
    atMs: now(),
    type: 'turn',
    role,
    durationMs,
    questionCount: Math.max(0, Math.round(questionCount)),
  });
}

function resolveBackchannel(collision: boolean, record = true): void {
  if (listenerBackchannelTimer) clearTimeout(listenerBackchannelTimer);
  listenerBackchannelTimer = null;
  if (record && listenerBackchannelAt !== null) {
    recordLiveConversationEvaluationEvent({ atMs: now(), type: 'backchannel', collision });
  }
  listenerBackchannelAt = null;
}

function sanitizeEvent(event: LiveConversationEvaluationEvent): LiveConversationEvaluationEvent {
  if (event.type === 'turn') {
    const { content: _content, ...contentFree } = event;
    return contentFree;
  }
  return event;
}

function redactPersistedEvent(event: LiveConversationEvaluationEvent): LiveConversationEvaluationEvent {
  return sanitizeEvent(event);
}

function readStoredEvents(): LiveConversationEvaluationEvent[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_EVALUATION_STORAGE_KEY) || '[]');
    return Array.isArray(parsed) ? parsed.filter(isEvaluationEvent).map(sanitizeEvent).slice(-MAX_EVENTS) : [];
  } catch {
    return [];
  }
}

function persistEvents(value: LiveConversationEvaluationEvent[]): void {
  try {
    window.localStorage.setItem(LIVE_EVALUATION_STORAGE_KEY, JSON.stringify(value.map(redactPersistedEvent)));
  } catch {
    // In-memory report remains available.
  }
}

function dispatchUpdate(): EvaluationSnapshot {
  const snapshot = { events: [...events], report: evaluateLiveConversation(events) };
  if (typeof window !== 'undefined') emitOmnixEvent(LIVE_CONVERSATION_EVALUATION_UPDATED_EVENT, snapshot);
  return snapshot;
}

function isEvaluationEvent(value: unknown): value is LiveConversationEvaluationEvent {
  return Boolean(value && typeof value === 'object' && typeof (value as { atMs?: unknown }).atMs === 'number' && typeof (value as { type?: unknown }).type === 'string');
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : null;
}

function now(): number {
  return Date.now();
}
